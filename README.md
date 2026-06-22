# Gym Knowledge Repository

A personal knowledge base of gym/training YouTube videos — tagged, graphed, searchable.

**Live browser:** [https://dtagk.github.io/gym-knowledge-repository/](https://dtagk.github.io/gym-knowledge-repository/)

## What it does

Ingests YouTube videos from curated channels → transcribes → extracts entities (muscles, exercises, concepts) into a knowledge graph (Kuzu) and vector store (LanceDB) → classifies by body part + training goal → exports to a static GitHub Pages browser.

## Pipeline stages

```text
discover → filter → promote → download → transcribe → extract → embed → graph → classify → export → synthesize → cite → enrich
```

The `promote` stage scores `candidate` videos (from search-seeded discovery)
against the corpus's interests and promotes the relevant ones to `approved`:

```bash
python -m yt_kg.promote --dry-run          # score + preview, change nothing
python -m yt_kg.promote --threshold 0.4    # promote everything at/above 0.4
python -m yt_kg.promote --top 20           # promote only the 20 best
```

Run each stage:

```bash
python -m yt_kg.discover
python -m yt_kg.download
python -m yt_kg.transcribe
python -m yt_kg.extract
python -m yt_kg.embed
python -m yt_kg.graph
python -m yt_kg.classify
python -m yt_kg.export
python -m yt_kg.synthesize   # requires RAG server running
python -m yt_kg.enrich_papers # mine OA PDFs + OpenAlex relations into the graph
```

## RAG Server

Semantic search + Q&A over the corpus:

```bash
# Start (point DATA_DIR at the repo root where data/ lives)
DATA_DIR=/path/to/gym-knowledge-repository \
  uvicorn yt_kg.rag_server:app --reload --port 8000

# Semantic search (no LLM)
curl "http://localhost:8000/search?q=shoulder+mobility&limit=5"

# Q&A with citations (requires Ollama + gpt-oss:20b)
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "best exercises for shoulder mobility", "limit": 5}'
```

## Docker Compose (Ollama)

```bash
docker compose up -d ollama
docker exec ollama ollama pull gpt-oss:20b
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

On native Windows, if ffmpeg/Node live in non-default WinGet paths, opt into
PATH injection (see `yt_kg/__init__.py`):

```powershell
$env:YT_KG_INJECT_PATH = "1"
# optional overrides:
# $env:YT_KG_FFMPEG_BIN = "C:\path\to\ffmpeg\bin"
```

## Technique search

Find timestamped coaching cues / common mistakes for a given exercise:

```bash
# all cues for an exercise
curl "http://localhost:8000/technique?exercise=lateral+raise"

# filter by keyword (mistake | cue | setup | tempo | breathing)
curl "http://localhost:8000/technique?exercise=lateral+raise&kind=mistake"
```

Each result returns the cue text, its kind, and a deep link
(`https://youtu.be/<id>?t=<seconds>`) to the exact moment in the video.

## Graph-native retrieval

`/ask` expands recall through the graph by default (`graph_expand: true`):
after the vector search, it pulls in additional chunks that mention RELATED
neighbor entities. Pure graph traversals (no LLM) are also exposed:

```bash
# videos that cite at least one paper in common with a given video
curl "http://localhost:8000/related/co-cited?video_id=<id>"

# entities connected to one entity via RELATED edges
curl "http://localhost:8000/related/exercises?entity=Romanian+deadlift"
```

## Synthesis page

The `synthesize` stage now also renders a static, browsable
`docs/synthesis.html` (linked from the main browser) alongside the JSON, so
cross-video answers are readable without hitting the API.

## Paper enrichment

The `enrich_papers` stage deepens the graph using **only sources already
fetched** — the open-access PDFs in `data/papers/` and the OpenAlex metadata
from citation resolution. No new corpus is downloaded. Three layers:

1. **PDF mining** — extracts text from each OA PDF, chunks + embeds it into the
   same LanceDB table as transcripts (tagged `source='paper'`), so studies
   become answerable content in `/ask`.
2. **OpenAlex relations** — adds `Paper-[ABOUT]->Concept` and intra-corpus
   `Paper-[CITES]->Paper` edges from the cached Work records.
3. **Paper↔exercise linking** — runs entity extraction over paper text and
   links resolved entities via `Paper-[DISCUSSES]->Entity`, connecting the
   research layer to the coaching layer.

```bash
python -m yt_kg.enrich_papers            # all three layers
python -m yt_kg.enrich_papers --no-pdf   # OpenAlex relations + concepts only
```

Re-run `python -m yt_kg.export` afterward to refresh `docs/data/graph.json`.

## Graph explorer

`docs/graph.html` is an interactive explorer for the whole knowledge graph,
with two toggleable views: a **force-directed D3 graph** and a **searchable
node browser**. Filter by node type, click any node to inspect its neighbors
grouped by edge type, and follow links back to videos and papers. It loads the
static `docs/data/graph.json` produced by the `export` stage; D3 is vendored
locally under `docs/vendor/` so the page has no external dependencies.

## Stack

- **LanceDB** — vector store (384-dim bge-small-en-v1.5 embeddings; transcript + paper chunks)
- **Kuzu** — graph DB (Entity, Video, Chunk, Paper, TechniqueCue, Concept nodes; MENTIONS, APPEARS_IN, RELATED, REFERENCES, HAS_TECHNIQUE, DISCUSSES, ABOUT, CITES edges)
- **SQLite** — video metadata and pipeline state
- **Ollama** — local LLM (qwen2.5-coder:7b for classify, gpt-oss:20b for RAG)
- **FastAPI** — RAG server (`/search`, `/ask`, `/technique`, `/related/*`)
- **GitHub Pages** — static browser + graph explorer (`docs/`)
- **D3** — vendored locally for the graph explorer (`docs/vendor/`)
