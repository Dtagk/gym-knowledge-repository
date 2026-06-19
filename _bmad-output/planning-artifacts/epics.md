---
stepsCompleted: [1, 2]
inputDocuments:
  - _bmad-output/specs/spec-yt-kg/SPEC.md
  - _bmad-output/specs/spec-yt-kg/stack.md
  - yt_kg_pipeline.md
---

# YouTube → Knowledge Graph Pipeline - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for the yt-kg pipeline, decomposing requirements from SPEC.md (CAP-1–10 as FRs, Constraints as NFRs) and the architecture companions into implementable stories.

## Requirements Inventory

### Functional Requirements

FR1: The system discovers and records all videos from a `channels.yaml` entry of type `channel`, `playlist`, or `video` without YouTube API credentials; re-running adds only genuinely new videos.
FR2: The system downloads the audio track of any tracked video at ≤96 kbps (m4a), then deletes the file automatically after transcription succeeds.
FR3: The system transcribes a downloaded audio file locally with faster-whisper, producing segment-level JSON with `start`/`end` timestamps; a 60-min video completes in under 15 minutes on the target GPU using `medium` at `float16`.
FR4: The system chunks transcripts into time-bounded overlapping segments, embeds them with bge-small-en-v1.5, and stores them in LanceDB so vector search returns chunks with valid `https://youtu.be/{id}?t={sec}` URLs.
FR5: The system extracts typed entity/relation triples from each chunk via Ollama + instructor + Pydantic schema; ≥97% of chunks produce a valid `Extraction`; failures are logged with `error_stage=extract` and skipped without blocking downstream stages.
FR6: The system deduplicates entity mentions across chunks using embedding similarity (cosine > 0.85) plus LLM verification, producing a canonical entity table with an alias lookup; known alias pairs (e.g. "Yann LeCun" / "LeCun") resolve to a single node.
FR7: The system loads resolved triples into Kuzu with full provenance (video_id, chunk_id, evidence quote) on every edge; a Cypher `MATCH` on a named entity returns the node, and every `RELATED` edge carries non-null `evidence` and `video_id`.
FR8: The system extracts paper references from video descriptions and transcripts, resolves them via OpenAlex, and downloads the full PDF for any paper where OpenAlex returns a non-null `open_access.oa_url`; for a video with a known paper reference, a `Paper` node with a valid DOI and title exists in Kuzu; papers with an OA URL have a PDF at `data/papers/{doi_slug}.pdf`; papers without one are recorded with metadata only.
FR9: The system exposes `vector_search`, `cypher_query`, `expand_entity`, and `papers_for_topic` as MCP tools registered with Claude Code; with the server active, asking "What exercises target the posterior chain?" returns at least one chunk with a YouTube timestamp and at least one graph entity with an evidence quote.
FR10: The pipeline tracks per-video stage completion in a SQLite `videos` table with timestamp flags; interrupting and restarting completes only remaining stages with no duplicate rows in any store.

### NonFunctional Requirements

NFR1: Zero paid-API cost — all transcription, embedding, and LLM inference run locally via faster-whisper, sentence-transformers, and Ollama.
NFR2: All stores are embedded and serverless: SQLite (job state at `data/jobs.sqlite`), LanceDB (`data/vectors.lance/`), Kuzu (`data/graph.kuzu/`) — no external database processes required to run.
NFR3: Target hardware is RTX 5060 Ti (16 GB VRAM) + 32 GB DDR5; Whisper `medium` and Qwen2.5-7B-Instruct must not OOM — they run in separate passes, never concurrently.
NFR4: YouTube rate limits must be respected — yt-dlp must use `--sleep-requests` and `--max-sleep-interval`; no burst downloading.
NFR5: LLM extraction failures (schema violations, timeouts, validation errors after 2 instructor retries) must be caught, logged to the `videos` table (`last_error`, `error_stage`), and skipped — never block the pipeline.
NFR6: Citation resolution uses OpenAlex (no API key) as primary, Semantic Scholar as fallback; no paid citation services.
NFR7: The MCP server must be registrable in Claude Code's standard MCP config and show `connected` in `claude mcp list`.

### Additional Requirements

- Repo layout: `yt_kg/` Python package, `config/channels.yaml`, `config/extraction_schema.py`, `scripts/run_pipeline.py`, `data/` hierarchy (audio/, transcripts/, jobs.sqlite, vectors.lance/, graph.kuzu/)
- `channels.yaml` entry types: `channel` (full channel URL), `playlist` (playlist URL), `video` (individual video URL); each entry has `id`, `url`, `poll_every`
- Python 3.11+ with `pyproject.toml`; no framework, no Prefect — plain Python loop for POC
- Whisper: `vad_filter=True` is mandatory; `medium`/`float16`/`cuda` on GPU; `small`/`int8`/`cpu` as fallback
- SQLite `videos` table: stage timestamp flags (`downloaded_at`, `transcribed_at`, `chunked_at`, `extracted_at`, `graphed_at`, `cited_at`) + error fields; each stage selects `WHERE <prev>_at IS NOT NULL AND <this>_at IS NULL`
- Raw `Extraction` output must be persisted per chunk for auditability (Parquet or SQLite side table)
- Entity resolution: within-type clustering only; cosine > 0.85 candidate pairs; LLM verify merges; `entities` + `entity_aliases` canonical tables; manual override YAML loaded at ER runtime
- Kuzu schema: Node tables Entity, Video, Chunk, Paper; Rel tables MENTIONS (Chunk→Entity), APPEARS_IN (Entity→Video), REFERENCES (Chunk→Paper), RELATED (Entity→Entity with predicate, evidence, video_id)
- Citation extraction order: video description regex (arXiv IDs, DOI patterns) first; transcript LLM pass second
- yt-dlp cookie support (`--cookies-from-browser`) for rate-limit bypass fallback
- Build progression: (1) single video through transcribe+chunk+embed and verify vector search; (2) add extraction on one channel, spot-check 50 triples; (3) add Kuzu + entity resolution; (4) add citations; (5) MCP server wrap; (6) multi-channel
- POC channels: Jeff Nippard, Squat University, Venus Gabby, Renaissance Periodization, Hybrid Calisthenics

### UX Design Requirements

N/A — this is a pipeline project with no UI layer. The MCP server is the query interface; Claude Code is the "UI".

### FR Coverage Map

FR1 → Epic 1 · discovery (channels.yaml → SQLite videos table)
FR2 → Epic 1 · audio download (m4a, ≤96 kbps, delete after transcription)
FR3 → Epic 1 · transcription (faster-whisper, segment JSON with timestamps)
FR4 → Epic 1 · chunking + embedding → LanceDB with timestamp URLs
FR5 → Epic 2 · triple extraction (Qwen2.5-7B via Ollama + instructor, ≥97% valid)
FR6 → Epic 2 · entity resolution (cosine similarity + LLM verify → canonical table)
FR7 → Epic 2 · Kuzu graph load (provenance on every edge)
FR8 → Epic 3 · citation enrichment (OpenAlex, Paper nodes in Kuzu)
FR9 → Epic 4 · MCP server + Claude Code registration
FR10 → Epic 1 · SQLite stage flags + idempotent SELECT pattern (extended by each later epic for its own stage columns)

## Epic List

### Epic 1: Local Ingestion Pipeline with Semantic Search

After this epic the developer can run `run_pipeline.py` on a single video and get YouTube timestamp search results back from a semantic query against the transcript corpus.
**FRs covered:** FR1, FR2, FR3, FR4, FR10

### Epic 2: Knowledge Graph — Extraction and Entity Resolution

After this epic the developer can run Cypher queries against a Kuzu graph populated with resolved entities and provenance-tagged relations extracted from the transcript corpus.
**FRs covered:** FR5, FR6, FR7

### Epic 3: Academic Citation Enrichment

After this epic Paper nodes exist in Kuzu for papers referenced in videos, linked to the entities and videos that cite them.
**FRs covered:** FR8

### Epic 4: MCP Server — Claude Code Query Interface

After this epic Claude Code can answer fitness questions with YouTube timestamps and graph evidence via 4 registered MCP tools.
**FRs covered:** FR9

---

## Epic 1: Local Ingestion Pipeline with Semantic Search

After this epic the developer can run `run_pipeline.py` on a single video and get YouTube timestamp search results back from a semantic query against the transcript corpus.

### Story 1.1: Project Scaffold + Video Discovery

As a developer,
I want a working project structure and a `discover.py` that populates a SQLite `videos` table from a `channels.yaml` entry,
So that I have a runnable foundation and a video inventory to drive the rest of the pipeline.

**Acceptance Criteria:**

**Given** a `pyproject.toml` with all dependencies declared
**When** `pip install -e .` is run
**Then** the install succeeds with no missing-dependency errors

**Given** a `channels.yaml` with a `video` entry type
**When** `discover.py` is run
**Then** the `videos` table contains exactly 1 row with the correct `video_id`, `title`, and `channel_id`

**Given** a `channels.yaml` with a `channel` entry type
**When** `discover.py` is run
**Then** the `videos` table contains one row per video in that channel, each with a unique `video_id`

**Given** a `channels.yaml` with a `playlist` entry type
**When** `discover.py` is run
**Then** the `videos` table contains one row per video in that playlist

**Given** `discover.py` has already been run and the `videos` table is populated
**When** `discover.py` is run again with the same `channels.yaml`
**Then** no duplicate rows are added (row count is unchanged)

**Given** `discover.py` completes successfully
**When** the `videos` table is inspected
**Then** all stage flag columns (`downloaded_at`, `transcribed_at`, `chunked_at`, `extracted_at`, `graphed_at`, `cited_at`) are `NULL` — no downstream stage is triggered by discovery

---

### Story 1.2: Throttled Audio Download

As a developer,
I want `download.py` to fetch audio for pending videos with yt-dlp rate limiting,
So that I can feed transcription without risking YouTube rate-limit blocks.

**Acceptance Criteria:**

**Given** a video row with `downloaded_at IS NULL`
**When** `download.py` is run for that video
**Then** `data/audio/{video_id}.m4a` exists and its bitrate is ≤96 kbps

**When** the download completes successfully
**Then** `downloaded_at` is set to the current UTC timestamp in the `videos` table

**Given** a video row with `downloaded_at IS NOT NULL`
**When** `download.py` is run for that video
**Then** no download is attempted and the existing file is untouched (idempotent)

**Given** yt-dlp is downloading
**When** making requests
**Then** `--sleep-requests` throttling is active between requests (no burst)

**Given** a download fails (network error, removed video, etc.)
**When** the error is caught
**Then** `last_error` and `error_stage='download'` are written to the `videos` row and `download.py` moves on to the next video without crashing

---

### Story 1.3: Local Transcription with faster-whisper

As a developer,
I want `transcribe.py` to convert downloaded audio into timestamped segment JSON locally,
So that I have structured text with time references to chunk and embed.

**Acceptance Criteria:**

**Given** a video with `downloaded_at IS NOT NULL AND transcribed_at IS NULL`
**When** `transcribe.py` is run
**Then** `data/transcripts/{video_id}.json` is written with a top-level `language`, `duration`, and `segments` array; each segment has `start`, `end`, and `text` fields

**When** transcription succeeds
**Then** `data/audio/{video_id}.m4a` is deleted and `transcribed_at` is set

**Given** a CUDA-capable GPU is available
**When** the Whisper model is loaded
**Then** `medium` at `float16` on `cuda` is used; a 60-minute video completes in under 15 minutes

**Given** no CUDA GPU is available
**When** the Whisper model is loaded
**Then** `small` at `int8` on `cpu` is used automatically (no code change required)

**Given** Whisper is running
**When** processing any audio
**Then** `vad_filter=True` is active, silence and music segments are dropped

**Given** transcription fails for any reason
**When** the error is caught
**Then** `last_error` and `error_stage='transcribe'` are written; the audio file is NOT deleted; the pipeline continues to the next video

---

### Story 1.4: Time-Aware Chunking and LanceDB Embedding

As a developer,
I want `chunk.py` and `embed.py` to split transcripts into overlapping time-stamped chunks and store their embeddings in LanceDB,
So that I can run semantic searches that return YouTube timestamp links.

**Acceptance Criteria:**

**Given** a video with `transcribed_at IS NOT NULL AND chunked_at IS NULL`
**When** `chunk.py` runs
**Then** the transcript is split into chunks of approximately 2400 characters with ~300-character overlap; each chunk retains the `start` and `end` timestamps of its constituent segments

**When** `embed.py` runs on the chunks
**Then** each chunk is encoded by `bge-small-en-v1.5` (384-dim, CPU batch inference — no GPU allocation) and upserted into the LanceDB `chunks` table with fields: `chunk_id` (`{video_id}:{i}`), `video_id`, `start`, `end`, `text`, `vector`

**When** chunking and embedding succeed
**Then** `chunked_at` is set in the `videos` table

**Given** the LanceDB table is populated
**When** a vector search is run with a relevant query string
**Then** at least one result is returned for the processed video, and every result includes a valid `https://youtu.be/{video_id}?t={int(start)}` URL

---

### Story 1.5: Pipeline Orchestrator and End-to-End Smoke Test

As a developer,
I want `scripts/run_pipeline.py` to drive all stages in order per video using the idempotent stage-flag pattern,
So that I can process a video end-to-end and safely resume from any interruption.

**Acceptance Criteria:**

**Given** a `channels.yaml` with one video entry and an empty `data/` directory
**When** `python scripts/run_pipeline.py` is run to completion
**Then** the `videos` table row has `downloaded_at`, `transcribed_at`, and `chunked_at` all set; LanceDB contains chunks for the video

**Given** the pipeline is interrupted immediately after transcription completes
**When** `run_pipeline.py` is restarted
**Then** only the chunk+embed stage runs; no duplicate transcript JSON is written and no duplicate LanceDB rows are created

**Given** one video in the batch fails at download
**When** the pipeline continues
**Then** the error is logged and all other videos proceed through their stages unblocked

**Given** all stages have completed for all videos
**When** `run_pipeline.py` is run again
**Then** no stages are re-executed (all `WHERE prev_at IS NOT NULL AND this_at IS NULL` selects return empty)

**Given** the pipeline has finished successfully
**When** a vector search for `"posterior chain exercises"` is run against LanceDB
**Then** at least one chunk result is returned with a valid `https://youtu.be/...?t=...` URL

---

## Epic 2: Knowledge Graph — Extraction and Entity Resolution

After this epic the developer can run Cypher queries against a Kuzu graph populated with resolved entities and provenance-tagged relations extracted from the transcript corpus.

### Story 2.1: Pydantic Extraction Schema and Chunk-Level Triple Extraction

As a developer,
I want `config/extraction_schema.py` to define the typed extraction contract and `yt_kg/extract.py` to run each chunk through the local LLM and persist the raw output,
So that I have structured entity/relation triples per chunk that downstream resolution and graph-load stages can consume.

**Acceptance Criteria:**

**Given** `config/extraction_schema.py` exists
**When** it is imported
**Then** it exports `Entity`, `Relation`, and `Extraction` Pydantic models; `Entity` has fields `name`, `type` (one of `Person`, `Paper`, `Concept`, `Tool`, `Method`, `Dataset`, `Organization`, `Event`), and `description`; `Relation` has `subject`, `predicate`, `object`, and `evidence` (the verbatim quote supporting the relation); `Extraction` has `entities: list[Entity]` and `relations: list[Relation]`

**Given** a video with `chunked_at IS NOT NULL AND extracted_at IS NULL`
**When** `extract.py` is run for that video
**Then** for each chunk in LanceDB, `instructor` calls the Ollama endpoint (`Mode.JSON`, up to 2 retries) with the chunk text and returns a validated `Extraction` matching the schema

**When** extraction succeeds for a chunk
**Then** the raw `Extraction` is written to a SQLite side table `raw_extractions` with columns `chunk_id`, `video_id`, `extraction_json` (JSON-serialised), `created_at`

**When** all chunks for a video are processed without fatal error
**Then** `extracted_at` is set to the current UTC timestamp in the `videos` table

**Given** a chunk where the LLM returns a schema-invalid response after 2 retries
**When** the `instructor` validation error is caught
**Then** `last_error` and `error_stage='extract'` are written to the `videos` row for that video; the chunk is skipped; processing continues on the next chunk without crashing

**Given** a batch of chunks for a single video
**When** extraction completes
**Then** ≥97% of chunks have a row in `raw_extractions` (≤3% failure rate is acceptable and logged)

---

### Story 2.2: Entity Resolution — Canonical Table and Alias Lookup

As a developer,
I want `yt_kg/resolve.py` to cluster entity mentions across chunks by embedding similarity and LLM-verified merges, producing canonical `entities` and `entity_aliases` SQLite tables,
So that the same real-world entity referenced under different names collapses to a single node before being loaded into Kuzu.

**Acceptance Criteria:**

**Given** the `raw_extractions` table is populated
**When** `resolve.py` is run
**Then** it reads all entity mentions from `raw_extractions`, groups them by `type`, and computes cosine similarity between `bge-small-en-v1.5` embeddings of each entity `name + description`

**Given** two entity mentions of the same type with cosine similarity > 0.85
**When** the merge candidate is identified
**Then** an Ollama LLM call confirms or rejects the merge; confirmed merges write both names to `entity_aliases` pointing to a single `canonical_id` in the `entities` table

**Given** a `config/entity_overrides.yaml` file exists with explicit merge pairs
**When** `resolve.py` runs
**Then** those pairs are merged unconditionally before the similarity clustering step, regardless of their cosine similarity score

**Given** resolution completes
**When** the `entities` table is inspected
**Then** it has columns `canonical_id` (UUID), `name`, `type`, `description`; the `entity_aliases` table has columns `alias`, `type`, `canonical_id`; every alias in `entity_aliases` points to an existing `canonical_id`

**Given** a known alias pair (e.g. `"Brad Schoenfeld"` / `"Schoenfeld"`) present in the same extraction batch
**When** `resolve.py` completes
**Then** both names resolve to the same `canonical_id` in `entity_aliases`

---

### Story 2.3: Kuzu Schema Initialisation and Provenance-Tagged Graph Load

As a developer,
I want `yt_kg/graph.py` to initialise the Kuzu schema and load resolved triples with full provenance on every edge,
So that I can run Cypher queries returning entities with the video and chunk evidence for each relation.

**Acceptance Criteria:**

**Given** `data/graph.kuzu/` does not yet exist
**When** `graph.py` initialises the database
**Then** Kuzu node tables are created: `Entity (canonical_id STRING, name STRING, type STRING, description STRING)`, `Video (video_id STRING, title STRING, channel_id STRING)`, `Chunk (chunk_id STRING, video_id STRING, start DOUBLE, end DOUBLE, text STRING)`, `Paper (doi STRING, title STRING, authors STRING, year INT64)`; and rel tables: `MENTIONS (Chunk→Entity)`, `APPEARS_IN (Entity→Video)`, `REFERENCES (Chunk→Paper)`, `RELATED (Entity→Entity, predicate STRING, evidence STRING, video_id STRING)`; re-running initialisation on an existing database is a no-op

**Given** the `entities`, `entity_aliases`, and `raw_extractions` tables are populated
**When** `graph.py` loads triples for a video
**Then** each resolved entity is upserted as an `Entity` node; a `Video` node is upserted for the source video; a `Chunk` node is upserted for each chunk; `MENTIONS` edges are created from each `Chunk` to the `Entity` nodes it mentions; `APPEARS_IN` edges are created from each `Entity` to the `Video`; `RELATED` edges are created for each relation with non-null `predicate`, `evidence` (verbatim quote from `Relation.evidence`), and `video_id`

**When** graph load succeeds for a video
**Then** `graphed_at` is set to the current UTC timestamp in the `videos` table

**Given** the graph is populated
**When** the Cypher query `MATCH (e:Entity {name: "quad"}) RETURN e` is run (substituting any entity name present in the data)
**Then** the expected `Entity` node is returned

**Given** any `RELATED` edge in the graph
**When** its properties are inspected
**Then** `evidence` is non-null and `video_id` is non-null

---

### Story 2.4: Pipeline Extension and Extraction Spot-Check

As a developer,
I want `scripts/run_pipeline.py` extended to drive the extract → resolve → graph stages in sequence using the same idempotent stage-flag pattern,
So that a single command processes a video end-to-end from discovery through graph load, and I can spot-check triples to validate extraction quality.

**Acceptance Criteria:**

**Given** a video that has completed chunking (`chunked_at IS NOT NULL`) and `extracted_at IS NULL`
**When** `run_pipeline.py` is run
**Then** `extract.py`, `resolve.py`, and `graph.py` are called in sequence for that video; `extracted_at` and `graphed_at` are set on completion

**Given** the pipeline is interrupted between extraction and graph load
**When** `run_pipeline.py` is restarted
**Then** extraction is skipped (already flagged) and only resolve + graph load run; no duplicate rows appear in `raw_extractions`, `entities`, or Kuzu

**Given** one video's extraction fails entirely (all chunks log `error_stage='extract'`)
**When** the pipeline continues
**Then** resolve and graph load are skipped for that video; all other videos proceed through their stages unblocked

**Given** a channel's worth of videos have been processed through graph load
**When** the spot-check Cypher query `MATCH (e:Entity)-[r:RELATED]->(e2:Entity) RETURN e.name, r.predicate, e2.name, r.evidence LIMIT 50` is run
**Then** at least 50 `RELATED` edges are returned; every row has non-null `evidence` and a recognizable predicate from the extraction schema

---

## Epic 3: Academic Citation Enrichment

After this epic Paper nodes exist in Kuzu for papers referenced in videos, linked to the chunks and videos that cite them, with PDFs downloaded for any open-access paper.

### Story 3.1: Citation Extraction from Video Descriptions and Transcripts

As a developer,
I want `yt_kg/cite.py` to extract raw paper references from a video's description and transcript,
So that I have a list of candidate paper strings (DOIs, arXiv IDs, titles) to resolve in the next stage.

**Acceptance Criteria:**

**Given** a video description containing a DOI string (e.g. `10.1519/JSC.0000000000001873`) or an arXiv ID (e.g. `arXiv:2301.00001`)
**When** `cite.py` runs the description regex pass
**Then** all matching DOI and arXiv ID strings are extracted and written to a SQLite `raw_citations` table with columns `citation_id` (UUID), `video_id`, `source` (`'description'`), `raw_ref` (the matched string), `created_at`

**Given** a video transcript where a speaker mentions a paper by title or author/year (e.g. "Schoenfeld et al. 2017")
**When** `cite.py` runs the transcript LLM pass via Ollama
**Then** candidate paper references identified by the LLM are written to `raw_citations` with `source='transcript'`; references already captured by the description regex pass are not duplicated

**Given** a video with no paper references in description or transcript
**When** `cite.py` runs
**Then** no rows are written to `raw_citations` for that video; no error is raised

**Given** `cite.py` has already been run for a video
**When** it is run again
**Then** no duplicate rows are added to `raw_citations` (idempotent on `video_id` + `raw_ref`)

---

### Story 3.2: OpenAlex Resolution and Paper Node Creation in Kuzu

As a developer,
I want `yt_kg/cite.py` to resolve each raw reference via OpenAlex (with Semantic Scholar fallback) and upsert a `Paper` node and `REFERENCES` edge in Kuzu,
So that structured paper metadata exists in the graph linked to the chunks that cite it.

**Acceptance Criteria:**

**Given** a `raw_citations` row with a valid DOI or arXiv ID
**When** the OpenAlex works endpoint is queried (`https://api.openalex.org/works/{doi}` or by title search)
**Then** a response with `doi`, `title`, `authorships`, and `publication_year` is received and stored in a `resolved_citations` SQLite table with columns `citation_id`, `video_id`, `doi`, `title`, `authors` (comma-joined display names), `year`, `oa_url` (`open_access.oa_url`, nullable), `resolved_at`

**Given** OpenAlex returns a 404 or empty result for a raw reference
**When** the fallback is triggered
**Then** Semantic Scholar's `/paper/{identifier}` endpoint is queried; if that also fails, `last_error` and `error_stage='cite'` are written to the `videos` row and the reference is skipped

**Given** a successfully resolved citation
**When** the Kuzu graph load runs for that paper
**Then** a `Paper` node is upserted with `doi`, `title`, `authors`, `year`; a `REFERENCES` edge is created from each `Chunk` that contained the citation to the `Paper` node; re-running the load does not create duplicate nodes or edges

**Given** the Kuzu graph is populated with paper data
**When** the Cypher query `MATCH (c:Chunk)-[:REFERENCES]->(p:Paper) RETURN p.doi, p.title LIMIT 5` is run
**Then** at least one row is returned for a video known to contain paper references

---

### Story 3.3: PDF Download for Open-Access Papers

As a developer,
I want `yt_kg/cite.py` to download the PDF for any resolved paper that has a non-null `open_access.oa_url`,
So that full paper text is available locally for future analysis while papers without open access are recorded with metadata only.

**Acceptance Criteria:**

**Given** a resolved citation where `oa_url` is non-null
**When** the PDF download step runs
**Then** `httpx` performs a GET request to `oa_url`; on HTTP 200, the response body is written to `data/papers/{doi_slug}.pdf` where `doi_slug` is the DOI with `/` and `.` replaced by `_`

**Given** `data/papers/{doi_slug}.pdf` already exists for a paper
**When** the download step runs again
**Then** no HTTP request is made and the existing file is left untouched (idempotent)

**Given** a resolved citation where `oa_url` is null
**When** the download step runs
**Then** no HTTP request is attempted; the paper is recorded in Kuzu with metadata only; no error is raised

**Given** the HTTP request to `oa_url` returns a non-200 status or times out
**When** the error is caught
**Then** the failure is logged to `last_error` with `error_stage='cite_pdf'`; the Paper node and `REFERENCES` edges already created in Story 3.2 are unaffected; the pipeline continues

**Given** a video known to have a paper with an open-access URL (verifiable via OpenAlex)
**When** `cite.py` completes for that video
**Then** `data/papers/{doi_slug}.pdf` exists and has a non-zero file size

---

### Story 3.4: Pipeline Extension — Citation Stage Integration

As a developer,
I want `scripts/run_pipeline.py` extended to drive the citation stage after graph load, using the same idempotent stage-flag pattern,
So that `cited_at` is set per video and the pipeline can be interrupted and resumed without re-fetching citations or re-downloading PDFs.

**Acceptance Criteria:**

**Given** a video with `graphed_at IS NOT NULL AND cited_at IS NULL`
**When** `run_pipeline.py` is run
**Then** `cite.py` is called for that video; on completion `cited_at` is set to the current UTC timestamp in the `videos` table

**Given** a video with `cited_at IS NOT NULL`
**When** `run_pipeline.py` is run
**Then** the citation stage is skipped entirely for that video (idempotent)

**Given** the pipeline is interrupted after citation extraction but before PDF download
**When** `run_pipeline.py` is restarted
**Then** already-resolved citations are not re-fetched from OpenAlex; only the pending PDF downloads are retried

**Given** one video's citation stage fails entirely
**When** the pipeline continues
**Then** `error_stage='cite'` is logged for that video and all other videos proceed unblocked; `cited_at` is not set for the failed video

**Given** a video with known paper references has completed the full pipeline
**When** `MATCH (p:Paper) RETURN p.doi, p.title` is run in Kuzu
**Then** at least one Paper node with a valid DOI and title is returned
