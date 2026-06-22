# Epic Stories Reference

Canonical story definitions for the gym-knowledge-repository pipeline.
Source of truth for GitHub issues (milestones 1–4).

---

## Epic 1 — Core Pipeline (Milestone 1)

### Story 1.1: Project Scaffold + Video Discovery
`discover.py` populates a SQLite `videos` table from `channels.yaml`.
- `pip install -e .` succeeds
- `video` / `channel` / `playlist` entries each produce the correct rows; re-run is idempotent
- All stage flag columns (`downloaded_at`, `transcribed_at`, `chunked_at`, `extracted_at`, `graphed_at`, `cited_at`) are `NULL` after discovery

### Story 1.2: Throttled Audio Download
`download.py` fetches audio for pending videos with yt-dlp rate limiting.
- `data/audio/{video_id}.m4a` at ≤96 kbps; `downloaded_at` set on success
- `--sleep-requests` throttling active; already-downloaded videos skipped

### Story 1.3: Local Transcription with faster-whisper
- `data/transcripts/{video_id}.json` with `language`, `duration`, `segments[]`
- GPU: `medium`/`float16`/`cuda`; CPU fallback: `small`/`int8`/`cpu`; `vad_filter=True`

### Story 1.4: Time-Aware Chunking and LanceDB Embedding
- Chunks ~2400 chars with ~300-char overlap, retaining `start`/`end` timestamps
- bge-small-en-v1.5 (384-dim) vectors upserted into LanceDB `chunks` table

### Story 1.5: Pipeline Orchestrator and End-to-End Smoke Test
- `scripts/run_pipeline.py` drives all stages; interrupted pipeline resumes without duplicates
- Vector search for "posterior chain exercises" returns ≥1 chunk with timestamp URL

---

## Epic 2 — Knowledge Graph (Milestone 2)

### Story 2.1: Pydantic Extraction Schema and Chunk-Level Triple Extraction
- `Entity`, `Relation`, `Extraction` models; raw output → `raw_extractions` SQLite table
- ≥97% of chunks produce a valid row; `extracted_at` set on success

### Story 2.2: Entity Resolution — Canonical Table and Alias Lookup
- Cosine similarity >0.85 pairs verified via Ollama → `entity_aliases`
- `entities` table: canonical_id (UUID), name, type, description

### Story 2.3: Kuzu Schema Initialisation and Provenance-Tagged Graph Load
- Node tables: Entity, Video, Chunk, Paper
- Rel tables: MENTIONS, APPEARS_IN, REFERENCES, RELATED (predicate/evidence/video_id)

### Story 2.4: Pipeline Extension and Extraction Spot-Check
- extract → resolve → graph in sequence; spot-check returns ≥50 RELATED edges with evidence

---

## Epic 3 — Citation Graph (Milestone 3)

### Story 3.1: Citation Extraction from Video Descriptions and Transcripts
- Regex pass for DOI/arXiv IDs; LLM pass for title/author-year references → `raw_citations`

### Story 3.2: OpenAlex Resolution and Paper Node Creation in Kuzu
- OpenAlex works endpoint; Semantic Scholar fallback; Paper node + REFERENCES edge in Kuzu

### Story 3.3: PDF Download for Open-Access Papers
- GET to `oa_url` on HTTP 200 → `data/papers/{doi_slug}.pdf`; null oa_url → skip cleanly

### Story 3.4: Pipeline Extension — Citation Stage Integration
- `cited_at` set on completion; one video failing cite does not block others

---

## Epic 4 — MCP Server (Milestone 4)

### Story 4.1: FastMCP Server with `vector_search` and `cypher_query` Tools
- FastMCP stdio transport; `claude mcp list` shows connected
- `vector_search(query, limit=5)`; write Cypher rejected with error message

### Story 4.2: `expand_entity` and `papers_for_topic` Tools
- `expand_entity(name, depth=1)` → RELATED edge traversal
- `papers_for_topic(topic)` → embed → chunks → REFERENCES → Paper records

### Story 4.3: Claude Code Registration and End-to-End Query Smoke Test
- MCP server added via `claude mcp add`; "posterior chain" query returns ≥1 chunk + entity
