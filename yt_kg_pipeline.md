# YouTube → Knowledge Graph → Chatbot

POC architecture for ingesting YouTube channels, transcribing locally with Whisper, extracting entities with a local LLM, building a knowledge graph, and querying it via chat / MCP.

---

## Goals & constraints

- **Input**: one or more YouTube channels (extensible to playlists, individual videos)
- **Output**: queryable knowledge graph + RAG retrieval, exposed as MCP server
- **Cost**: zero API costs for POC — all heavy lifting runs locally
- **Stack**: Python, embedded stores (no cloud DBs needed)
- **Citations**: extract paper references from descriptions & transcripts, resolve via OpenAlex / Semantic Scholar (both free)

---

## High-level flow

```
channels.yaml
    │
    ▼
[ 1. Discover ]  yt-dlp lists videos per channel  →  videos table
    │
    ▼
[ 2. Download audio ]  yt-dlp -x  (m4a/opus, lowest acceptable bitrate)
    │
    ▼
[ 3. Transcribe ]  faster-whisper  →  segments w/ timestamps
    │
    ▼
[ 4. Chunk + embed ]  sentence-aware chunking, local embeddings → LanceDB
    │
    ▼
[ 5. Extract triples ]  Ollama (Qwen2.5 / Llama 3.1) w/ structured outputs
    │
    ▼
[ 6. Entity resolution ]  embed names → cluster → LLM verify merges
    │
    ▼
[ 7. Load graph ]  Kuzu (Cypher, embedded)
    │
    ▼
[ 8. Enrich citations ]  parse descriptions/transcripts → OpenAlex
    │
    ▼
[ 9. Query layer ]  MCP server: vector_search, cypher_query, expand_neighborhood
```

Each step is **idempotent and resumable** — keyed on `video_id`, status tracked per stage.

---

## Tech choices

| Component | Choice | Notes |
|---|---|---|
| Channel/video discovery | `yt-dlp` Python API | No API key needed |
| Audio download | `yt-dlp` `-x` | m4a, 64-96 kbps is enough for ASR |
| Transcription | `faster-whisper` (CTranslate2) | 4–10× faster than `openai-whisper`. Models: `small` (POC), `medium` (quality), `large-v3` (best). Use `int8` quant on CPU, `float16` on GPU. |
| Embeddings | `bge-m3` or `bge-small-en-v1.5` via `sentence-transformers` | Both run on CPU; `bge-m3` is multilingual + dense+sparse |
| Vector store | **LanceDB** | Embedded, columnar, no server |
| Local LLM runtime | **Ollama** | Easiest. Alt: `llama.cpp` direct, vLLM if you have a real GPU |
| Extraction model | **Qwen2.5-7B-Instruct** or **Llama-3.1-8B-Instruct** | Both do structured JSON well via Ollama's `format: "json"` |
| Structured output | `pydantic` + `instructor` (Ollama backend) or `outlines` | Hard guarantee on schema |
| Graph DB | **Kuzu** | Embedded, Cypher-compatible, columnar, fast. No server. |
| Paper resolution | **OpenAlex** (no key, no rate limit pain) → Semantic Scholar fallback | |
| Orchestration | `prefect` if you want flows; else plain Python + SQLite job state | |
| Storage layout | DuckDB or Postgres if integrating with your DWH; else SQLite + Parquet | |
| MCP server | `mcp` Python SDK | Expose query tools to Claude Code |

**Hardware sanity check**: Whisper `medium` + Qwen2.5-7B both fit comfortably on a 16GB GPU. CPU-only is viable but slow — expect ~1× realtime for Whisper small `int8`, and ~10–30 tok/s for the 7B at Q4_K_M.

---

## Repo layout

```
yt_kg/
├── pyproject.toml
├── config/
│   ├── channels.yaml          # list of channels to track
│   └── extraction_schema.py   # entity/relation typology
├── yt_kg/
│   ├── __init__.py
│   ├── db.py                  # SQLite job state + Kuzu + LanceDB connections
│   ├── discover.py            # yt-dlp channel → video list
│   ├── download.py            # audio download
│   ├── transcribe.py          # faster-whisper wrapper
│   ├── chunk.py               # transcript → time-aware chunks
│   ├── embed.py               # chunk → vectors → LanceDB
│   ├── extract.py             # chunk → triples (Ollama)
│   ├── resolve.py             # entity resolution / merging
│   ├── graph.py               # load triples into Kuzu
│   ├── citations.py           # description/transcript → papers → OpenAlex
│   └── server.py              # MCP server exposing query tools
├── scripts/
│   ├── run_pipeline.py        # CLI: process pending videos through all stages
│   └── reset.py
└── data/
    ├── audio/                 # *.m4a, can be deleted after transcription
    ├── transcripts/           # *.json (segments + metadata)
    ├── jobs.sqlite            # per-video pipeline state
    ├── vectors.lance/         # LanceDB
    └── graph.kuzu/            # Kuzu DB
```

---

## Pipeline state table (SQLite)

One row per `video_id`, tracking what's done. Keep it dumb — boolean flags & timestamps.

```sql
CREATE TABLE videos (
    video_id        TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL,
    title           TEXT,
    description     TEXT,
    duration_s      INTEGER,
    published_at    TEXT,
    audio_path      TEXT,
    transcript_path TEXT,
    -- stage flags
    downloaded_at   TEXT,
    transcribed_at  TEXT,
    chunked_at      TEXT,
    extracted_at    TEXT,
    graphed_at      TEXT,
    cited_at        TEXT,
    -- error tracking
    last_error      TEXT,
    error_stage     TEXT
);
CREATE INDEX idx_videos_channel ON videos(channel_id);
```

Each stage selects `WHERE <prev>_at IS NOT NULL AND <this>_at IS NULL` — naturally resumable.

---

## Stage 1: Discover videos

```python
import yt_dlp

def list_channel_videos(channel_url: str) -> list[dict]:
    opts = {"extract_flat": "in_playlist", "quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    return [{
        "video_id": e["id"],
        "title": e.get("title"),
        "duration_s": e.get("duration"),
        "url": e.get("url"),
    } for e in info["entries"]]
```

For full metadata (description, upload date), do a second pass with `extract_flat=False` only on new IDs. Channel URL should be the `/videos` tab: `https://www.youtube.com/@channelname/videos`.

---

## Stage 2: Download audio

```python
def download_audio(video_id: str, out_dir: Path) -> Path:
    out_template = str(out_dir / "%(id)s.%(ext)s")
    opts = {
        "format": "bestaudio[abr<=96]/bestaudio",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
        }],
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    return out_dir / f"{video_id}.m4a"
```

Delete audio files after transcription succeeds — they're huge and re-derivable.

---

## Stage 3: Transcribe with faster-whisper

```python
from faster_whisper import WhisperModel

# Load once, reuse across videos
model = WhisperModel("medium", device="cuda", compute_type="float16")
# CPU fallback:
# model = WhisperModel("small", device="cpu", compute_type="int8")

def transcribe(audio_path: Path) -> dict:
    segments, info = model.transcribe(
        str(audio_path),
        vad_filter=True,           # skips silence — big speedup
        vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5,
        word_timestamps=False,     # segment-level is enough for chunking
    )
    return {
        "language": info.language,
        "duration": info.duration,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
        ],
    }
```

Persist as JSON next to audio. `vad_filter=True` is mandatory — YouTube intros, music, silence get dropped.

**Speed knob**: drop to `small` model for first pass, re-transcribe specific channels with `large-v3` later if extraction quality is poor.

---

## Stage 4: Chunking (time-aware)

Don't chunk on raw token count alone — preserve start/end timestamps so the chatbot can link back to the exact YouTube moment.

```python
def chunk_segments(segments: list[dict], target_chars: int = 2400, overlap_chars: int = 300) -> list[dict]:
    """Group segments into chunks of ~target_chars, keeping time bounds."""
    chunks, buf, buf_start = [], [], None
    for seg in segments:
        if buf_start is None:
            buf_start = seg["start"]
        buf.append(seg)
        if sum(len(s["text"]) for s in buf) >= target_chars:
            chunks.append({
                "start": buf_start,
                "end": buf[-1]["end"],
                "text": " ".join(s["text"] for s in buf),
            })
            # overlap: keep tail segments worth ~overlap_chars
            tail, total = [], 0
            for s in reversed(buf):
                tail.insert(0, s)
                total += len(s["text"])
                if total >= overlap_chars: break
            buf = tail
            buf_start = buf[0]["start"]
    if buf:
        chunks.append({"start": buf_start, "end": buf[-1]["end"],
                       "text": " ".join(s["text"] for s in buf)})
    return chunks
```

YouTube timestamp URL format: `https://youtu.be/{video_id}?t={int(start)}`.

---

## Stage 5: Embed + store in LanceDB

```python
import lancedb
from sentence_transformers import SentenceTransformer

embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")  # 384-dim, fast
db = lancedb.connect("data/vectors.lance")

# Schema (first-write inference is fine, but explicit is better)
def upsert_chunks(video_id: str, chunks: list[dict]):
    rows = []
    for i, c in enumerate(chunks):
        rows.append({
            "chunk_id": f"{video_id}:{i}",
            "video_id": video_id,
            "start": c["start"],
            "end": c["end"],
            "text": c["text"],
            "vector": embedder.encode(c["text"], normalize_embeddings=True),
        })
    table = db.open_table("chunks") if "chunks" in db.table_names() \
            else db.create_table("chunks", data=rows)
    table.add(rows)
```

For dense+sparse hybrid, swap to `bge-m3` and store both vectors.

---

## Stage 6: Entity/relation extraction (local LLM)

### Closed schema first

```python
# config/extraction_schema.py
from enum import Enum
from pydantic import BaseModel, Field

class EntityType(str, Enum):
    person = "Person"
    paper = "Paper"
    concept = "Concept"
    tool = "Tool"
    method = "Method"
    dataset = "Dataset"
    organization = "Organization"
    event = "Event"

class RelationType(str, Enum):
    works_at = "WORKS_AT"
    authored = "AUTHORED"
    cites = "CITES"
    introduces = "INTRODUCES"
    uses = "USES"
    related_to = "RELATED_TO"
    critiques = "CRITIQUES"
    extends = "EXTENDS"
    part_of = "PART_OF"

class Entity(BaseModel):
    name: str
    type: EntityType
    aliases: list[str] = Field(default_factory=list)

class Triple(BaseModel):
    subject: str           # entity name as it appears
    predicate: RelationType
    object: str
    evidence: str = Field(description="<=200 char quote from chunk supporting this triple")

class Extraction(BaseModel):
    entities: list[Entity]
    triples: list[Triple]
```

### Extract via Ollama + instructor

```python
from ollama import Client
import instructor

client = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)

EXTRACTION_PROMPT = """You extract structured knowledge from a transcript chunk.

Use ONLY these entity types: {entity_types}
Use ONLY these relation types: {relation_types}

Rules:
- Only extract entities & relations explicitly supported by the text.
- Each triple must include a short quoted evidence span.
- Prefer canonical names ("Geoffrey Hinton" not "Hinton" if both appear).
- Skip filler/conversational content.

Chunk:
{chunk_text}
"""

def extract(chunk_text: str) -> Extraction:
    return client.chat.completions.create(
        model="qwen2.5:7b-instruct",
        response_model=Extraction,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(
            entity_types=[e.value for e in EntityType],
            relation_types=[r.value for r in RelationType],
            chunk_text=chunk_text,
        )}],
        temperature=0.1,
        max_retries=2,   # instructor retries on validation failure
    )
```

Persist raw `Extraction` per chunk to a Parquet file or SQLite table — keep it auditable.

**Cost discipline (compute, not money)**: 7B model at ~30 tok/s, ~600 tok response per chunk → ~20s/chunk. A 60-min video ≈ 30 chunks ≈ 10 min of extraction. Plan accordingly; parallelize if multi-GPU.

---

## Stage 7: Entity resolution

The naive approach is "lowercase + dedupe". That's wrong. You need:

1. **Within-type clustering**: only consider merging entities of the same `type`.
2. **Candidate generation**: embed entity names (`bge-small`), cosine similarity > 0.85 → candidate pair.
3. **LLM verify**: ask the local LLM `is "{a}" the same as "{b}" given these contexts: ...`. Cache verdicts.
4. **Canonical table**:

```sql
CREATE TABLE entities (
    canonical_id TEXT PRIMARY KEY,   -- slug or uuid
    name         TEXT NOT NULL,
    type         TEXT NOT NULL
);
CREATE TABLE entity_aliases (
    alias        TEXT,
    canonical_id TEXT REFERENCES entities(canonical_id),
    PRIMARY KEY (alias, canonical_id)
);
```

Don't try to resolve perfectly upfront — keep aliases, allow human override later via a flat YAML of overrides loaded on top.

---

## Stage 8: Load graph (Kuzu)

```python
import kuzu

db = kuzu.Database("data/graph.kuzu")
conn = kuzu.Connection(db)

# Schema (run once)
conn.execute("""
CREATE NODE TABLE Entity(
    id STRING, name STRING, type STRING, PRIMARY KEY(id)
);
CREATE NODE TABLE Video(
    id STRING, title STRING, channel_id STRING, published_at TIMESTAMP, PRIMARY KEY(id)
);
CREATE NODE TABLE Chunk(
    id STRING, video_id STRING, start_s DOUBLE, end_s DOUBLE, text STRING, PRIMARY KEY(id)
);
CREATE NODE TABLE Paper(
    id STRING, doi STRING, title STRING, year INT64, PRIMARY KEY(id)
);
CREATE REL TABLE MENTIONS(FROM Chunk TO Entity);
CREATE REL TABLE APPEARS_IN(FROM Entity TO Video);
CREATE REL TABLE REFERENCES(FROM Chunk TO Paper);
CREATE REL TABLE RELATED(FROM Entity TO Entity, predicate STRING, evidence STRING, video_id STRING);
""")
```

Load triples per chunk: upsert entities, then `MENTIONS` edges, then typed `RELATED` edges with evidence + provenance.

---

## Stage 9: Citation enrichment

**Two sources, in order of yield:**

1. **Video description** — regex for `arXiv:\d{4}\.\d{4,5}`, DOI pattern `10\.\d{4,9}/[-._;()/:A-Z0-9]+`, paper URLs. Highest precision.
2. **Transcript LLM pass** — separate prompt: "extract any paper/book references with author + year if mentioned." Lower precision, fills gaps.

**Resolution**:

```python
import httpx

def resolve_openalex(query: str) -> dict | None:
    r = httpx.get("https://api.openalex.org/works",
                  params={"search": query, "per_page": 1},
                  headers={"User-Agent": "yt-kg/0.1 (mailto:you@example.com)"})
    results = r.json().get("results", [])
    return results[0] if results else None
```

OpenAlex returns abstract, references, citation count, concepts — all free, no key. Add `(Paper)-[:CITES]->(Paper)` from the `referenced_works` field to extend the graph.

---

## Stage 10: Query layer / MCP server

Expose four tools — the chatbot routes between them.

```python
# yt_kg/server.py (sketch)
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("yt-kg")

@mcp.tool()
def vector_search(query: str, k: int = 8) -> list[dict]:
    """Semantic search over transcript chunks. Returns chunks with YouTube timestamp URLs."""
    vec = embedder.encode(query, normalize_embeddings=True)
    table = lance_db.open_table("chunks")
    hits = table.search(vec).limit(k).to_list()
    return [{
        "text": h["text"],
        "video_id": h["video_id"],
        "url": f"https://youtu.be/{h['video_id']}?t={int(h['start'])}",
        "score": h["_distance"],
    } for h in hits]

@mcp.tool()
def cypher_query(query: str) -> list[dict]:
    """Run a read-only Cypher query against the knowledge graph."""
    # SAFETY: block CREATE/DELETE/MERGE/DROP in the string
    result = kuzu_conn.execute(query)
    return result.get_as_df().to_dict(orient="records")

@mcp.tool()
def expand_entity(name: str, hops: int = 1) -> dict:
    """Return an entity's neighborhood: connected entities, source videos, papers."""
    ...

@mcp.tool()
def papers_for_topic(topic: str, min_citations: int = 0) -> list[dict]:
    """Find papers mentioned across videos related to a topic."""
    ...
```

Register in Claude Code's `.claude` MCP config. The chatbot is then Claude itself, with your KG as a connector.

---

## Channels config

```yaml
# config/channels.yaml
channels:
  - id: lex_fridman
    url: https://www.youtube.com/@lexfridman/videos
    poll_every: 24h
  - id: yannic
    url: https://www.youtube.com/@YannicKilcher/videos
    poll_every: 24h
```

`scripts/run_pipeline.py` reads this, discovers new videos, and runs each pending stage in order. Run it on cron, or wrap in `prefect` for retries + observability.

---

## Build order (recommended)

1. **One channel, audio-only, ≤5 videos** end-to-end through transcribe + chunk + embed. Verify vector search returns sensible results. **Skip the graph for now.**
2. Add extraction on one channel. Spot-check 50 triples manually — tune prompt or escalate model size before scaling.
3. Add Kuzu loading + entity resolution.
4. Add citation enrichment (descriptions first, transcripts second).
5. Multi-channel + scheduling.
6. Wrap MCP server, register in Claude Code.

Resist building all of this before step 1 is solid. Most of the failure modes show up in transcription quality and extraction prompt — find them early on a small sample.

---

## Known sharp edges

- **YouTube blocks bursts**: throttle yt-dlp with `--sleep-requests 1` + `--max-sleep-interval`. Use cookies file if blocked (`--cookies-from-browser firefox`).
- **Whisper hallucinations on silence/music**: VAD filter usually handles it; if not, set `condition_on_previous_text=False`.
- **Local LLM JSON drift**: even with `instructor` retries, ~1-3% of chunks will fail. Log and skip; reprocess later. Don't block the pipeline.
- **Entity explosion**: without ER, you'll have "Yann LeCun", "LeCun", "Yann" as three nodes. Run ER nightly, not per-chunk.
- **Description regex on shortened URLs**: `bit.ly` / `t.co` hide DOIs. Either resolve redirects (slow) or accept the miss.
- **Hardware**: if 7B + Whisper-medium together OOM on your GPU, run them in separate passes — extract is offline-friendly, doesn't need to be live.

---

## Open questions to decide before starting

- Audio retention policy: delete after transcription, or keep for re-transcription with bigger Whisper later?
- DWH integration: write transcripts/chunks/triples to your existing DWH directly, or keep the local stores and sync periodically?
- Multi-language: faster-whisper handles it; does extraction prompt need per-language variants?
- Long-tail entities: do you want a manual override file for canonical names, or trust ER fully?
