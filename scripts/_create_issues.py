"""Create GitHub issues for all BMad stories."""
import subprocess, sys

REPO = "Dtagk/gym-knowledge-repository"

STORIES = [
    (1, "Story 1.1: Project Scaffold + Video Discovery", """## Story 1.1: Project Scaffold + Video Discovery

As a developer, I want a working project structure and a `discover.py` that populates a SQLite `videos` table from a `channels.yaml` entry.

### Acceptance Criteria
- `pip install -e .` succeeds with no missing-dependency errors
- `video` entry → exactly 1 row with correct `video_id`, `title`, `channel_id`
- `channel` entry → one row per video in that channel
- `playlist` entry → one row per video in that playlist
- Re-running `discover.py` adds no duplicate rows
- All stage flag columns are NULL after discovery

**Implemented in:** `feat(epic1)` — commit `0a06a2f`"""),

    (1, "Story 1.2: Throttled Audio Download", """## Story 1.2: Throttled Audio Download

As a developer, I want `download.py` to fetch audio for pending videos with yt-dlp rate limiting.

### Acceptance Criteria
- `data/audio/{video_id}.m4a` exists at ≤96 kbps after download
- `downloaded_at` set on success; `last_error`/`error_stage='download'` on failure
- Already-downloaded videos are skipped (idempotent)
- `sleep_interval_requests` throttling active between requests

**Implemented in:** `feat(epic1)` — commit `0a06a2f`"""),

    (1, "Story 1.3: Local Transcription with faster-whisper", """## Story 1.3: Local Transcription with faster-whisper

As a developer, I want `transcribe.py` to convert downloaded audio into timestamped segment JSON locally.

### Acceptance Criteria
- `data/transcripts/{video_id}.json` written with `language`, `duration`, `segments[]` (start/end/text)
- Audio file deleted and `transcribed_at` set on success
- GPU: `medium`/`float16`/`cuda`; CPU fallback: `small`/`int8`/`cpu`
- `vad_filter=True` always active
- Failure writes `error_stage='transcribe'`; audio NOT deleted; pipeline continues

**Implemented in:** `feat(epic1)` — commit `0a06a2f`"""),

    (1, "Story 1.4: Time-Aware Chunking and LanceDB Embedding", """## Story 1.4: Time-Aware Chunking and LanceDB Embedding

As a developer, I want `chunk.py` and `embed.py` to split transcripts into overlapping time-stamped chunks and store their embeddings in LanceDB.

### Acceptance Criteria
- Chunks ~2400 chars with ~300-char overlap, retaining `start`/`end` timestamps
- Each chunk encoded by `bge-small-en-v1.5` (384-dim), upserted into LanceDB `chunks` table
- `chunked_at` set on success
- Vector search returns results with valid `https://youtu.be/{video_id}?t={int(start)}` URLs

**Implemented in:** `feat(epic1)` — commit `0a06a2f`"""),

    (1, "Story 1.5: Pipeline Orchestrator and End-to-End Smoke Test", """## Story 1.5: Pipeline Orchestrator and End-to-End Smoke Test

As a developer, I want `scripts/run_pipeline.py` to drive all stages in order using the idempotent stage-flag pattern.

### Acceptance Criteria
- Single command processes a video end-to-end; all stage timestamps set
- Interrupted pipeline resumes from where it left off; no duplicates
- One video failing download does not block others
- Re-running a fully-processed pipeline is a no-op
- Vector search for "posterior chain exercises" returns ≥1 chunk with timestamp URL

**Implemented in:** `feat(epic1)` — commit `0a06a2f`"""),

    (2, "Story 2.1: Pydantic Extraction Schema and Chunk-Level Triple Extraction", """## Story 2.1: Pydantic Extraction Schema and Chunk-Level Triple Extraction

As a developer, I want `config/extraction_schema.py` to define the typed extraction contract and `yt_kg/extract.py` to run each chunk through the local LLM.

### Acceptance Criteria
- `Entity`, `Relation`, `Extraction` Pydantic models exported
- instructor calls Ollama (Mode.JSON, 2 retries) per chunk, returns validated `Extraction`
- Raw output written to `raw_extractions` SQLite table
- `extracted_at` set when all chunks processed
- Schema-invalid responses caught; `error_stage='extract'` written; pipeline continues
- ≥97% of chunks produce a valid row in `raw_extractions`

**Implemented in:** `feat(epic2)` — commit `2ff1f3b`"""),

    (2, "Story 2.2: Entity Resolution — Canonical Table and Alias Lookup", """## Story 2.2: Entity Resolution — Canonical Table and Alias Lookup

As a developer, I want `yt_kg/resolve.py` to cluster entity mentions across chunks by embedding similarity and LLM-verified merges.

### Acceptance Criteria
- Groups entities by type; cosine similarity between bge-small-en-v1.5 embeddings
- Pairs with similarity >0.85 verified via Ollama; confirmed merges → `entity_aliases`
- `config/entity_overrides.yaml` merges applied unconditionally before clustering
- `entities` table: canonical_id (UUID), name, type, description
- `entity_aliases` table: alias, type, canonical_id; all aliases point to existing canonical_id
- Known alias pairs resolve to same canonical_id

**Implemented in:** `feat(epic2)` — commit `2ff1f3b`"""),

    (2, "Story 2.3: Kuzu Schema Initialisation and Provenance-Tagged Graph Load", """## Story 2.3: Kuzu Schema Initialisation and Provenance-Tagged Graph Load

As a developer, I want `yt_kg/graph.py` to initialise the Kuzu schema and load resolved triples with full provenance on every edge.

### Acceptance Criteria
- Node tables: Entity, Video, Chunk, Paper
- Rel tables: MENTIONS, APPEARS_IN, REFERENCES, RELATED (with predicate/evidence/video_id)
- Re-initialising on existing DB is a no-op
- Every RELATED edge has non-null evidence and video_id
- `graphed_at` set on success

**Implemented in:** `feat(epic2)` — commit `2ff1f3b`"""),

    (2, "Story 2.4: Pipeline Extension and Extraction Spot-Check", """## Story 2.4: Pipeline Extension and Extraction Spot-Check

As a developer, I want `scripts/run_pipeline.py` extended to drive extract → resolve → graph stages in sequence.

### Acceptance Criteria
- extract.py, resolve.py, graph.py called in sequence; stage timestamps set
- Interrupted pipeline resumes without duplicates
- One video failing extraction does not block others
- Spot-check query returns ≥50 RELATED edges with non-null evidence

**Implemented in:** `feat(epic2)` — commit `2ff1f3b`"""),

    (3, "Story 3.1: Citation Extraction from Video Descriptions and Transcripts", """## Story 3.1: Citation Extraction from Video Descriptions and Transcripts

As a developer, I want `yt_kg/cite_extract.py` to extract raw paper references from a video's description and transcript.

### Acceptance Criteria
- Description regex pass extracts DOI and arXiv ID strings → `raw_citations` table
- Transcript LLM pass extracts title/author-year references with `source='transcript'`
- No duplicates across passes; no error on videos with no references
- Idempotent on re-run

**Implemented in:** `feat(epic3)` — commit `8b15a6b`"""),

    (3, "Story 3.2: OpenAlex Resolution and Paper Node Creation in Kuzu", """## Story 3.2: OpenAlex Resolution and Paper Node Creation in Kuzu

As a developer, I want `yt_kg/cite_resolve.py` to resolve each raw reference via OpenAlex (with Semantic Scholar fallback) and upsert a Paper node in Kuzu.

### Acceptance Criteria
- OpenAlex queried; response stored in `resolved_citations` table
- 404/empty → Semantic Scholar fallback; both fail → `error_stage='cite'`
- Paper node upserted in Kuzu; REFERENCES edge from each citing Chunk; idempotent
- Cypher query returns ≥1 Paper row for video with known paper reference

**Implemented in:** `feat(epic3)` — commit `8b15a6b`"""),

    (3, "Story 3.3: PDF Download for Open-Access Papers", """## Story 3.3: PDF Download for Open-Access Papers

As a developer, I want the citation stage to download the PDF for any resolved paper with a non-null `open_access.oa_url`.

### Acceptance Criteria
- GET to oa_url on HTTP 200 → written to `data/papers/{doi_slug}.pdf`
- Already-existing PDF → no HTTP request (idempotent)
- null oa_url → no request, metadata-only Paper node, no error
- Non-200/timeout → `error_stage='cite_pdf'`; existing Paper node unaffected

**Implemented in:** `feat(epic3)` — commit `8b15a6b`"""),

    (3, "Story 3.4: Pipeline Extension — Citation Stage Integration", """## Story 3.4: Pipeline Extension — Citation Stage Integration

As a developer, I want `scripts/run_pipeline.py` extended to drive the citation stage after graph load.

### Acceptance Criteria
- Videos with `graphed_at IS NOT NULL AND cited_at IS NULL` run cite stage; `cited_at` set
- Videos with `cited_at IS NOT NULL` are skipped (idempotent)
- Interrupted pipeline resumes without re-fetching resolved citations
- One video failing cite does not block others

**Implemented in:** `feat(epic3)` — commit `8b15a6b`"""),

    (4, "Story 4.1: FastMCP Server with vector_search and cypher_query Tools", """## Story 4.1: FastMCP Server with `vector_search` and `cypher_query` Tools

As a developer, I want `yt_kg/mcp_server.py` to expose `vector_search` and `cypher_query` as FastMCP tools.

### Acceptance Criteria
- Server starts with FastMCP stdio transport; `claude mcp list` shows connected
- `vector_search(query, limit=5)` → embeds query, ANN search, returns chunk_id/video_id/start/end/text/url
- `cypher_query(query)` → runs against Kuzu, returns JSON-serialisable rows; errors as message strings
- Write Cypher (CREATE/MERGE/DELETE/SET/CALL) rejected with error message

**Implemented in:** `feat(epic4)` — commit `d6fed25`"""),

    (4, "Story 4.2: expand_entity and papers_for_topic Tools", """## Story 4.2: `expand_entity` and `papers_for_topic` Tools

As a developer, I want `expand_entity` and `papers_for_topic` added to the MCP server.

### Acceptance Criteria
- `expand_entity(name, depth=1)` → Kuzu traversal from matching Entity via RELATED edges; returns name/type/predicate/evidence
- `papers_for_topic(topic)` → embed topic, top-5 chunks, Kuzu REFERENCES lookup, returns Paper records
- All 4 tools listed after server restart

**Implemented in:** `feat(epic4)` — commit `d6fed25`"""),

    (4, "Story 4.3: Claude Code Registration and End-to-End Query Smoke Test", """## Story 4.3: Claude Code Registration and End-to-End Query Smoke Test

As a developer, I want the MCP server registered in Claude Code's MCP config and verified with the FR9 acceptance query.

### Acceptance Criteria
- MCP server added via `claude mcp add`; `claude mcp list` shows connected
- "What exercises target the posterior chain?" returns ≥1 chunk with YouTube timestamp URL and ≥1 entity with evidence quote
- Server disconnected → Claude Code reports disconnected, not hang
- `scripts/start_mcp.py` starts the server and prints name + transport to stdout

**Implemented in:** `feat(epic4)` — commit `d6fed25`"""),
]

created = []
for milestone, title, body in STORIES:
    import tempfile, os, json
    payload = json.dumps({"title": title, "body": body, "milestone": milestone})
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        f.write(payload)
        tmp = f.name
    result = subprocess.run(
        ["gh", "api", f"repos/{REPO}/issues",
         "--method", "POST",
         "--input", tmp],
        capture_output=True, text=True
    )
    os.unlink(tmp)
    if result.returncode == 0:
        import json as _json
        url = _json.loads(result.stdout).get("html_url", "?")
        created.append(url)
        print(f"OK {title[:60]}  ->  {url}")
    else:
        print(f"FAILED: {title[:60]}")
        print(result.stderr[:200])

print(f"\nCreated {len(created)}/16 issues.")
