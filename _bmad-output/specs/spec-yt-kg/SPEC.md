---
id: SPEC-yt-kg
companions:
  - stack.md
  - ../../../yt_kg_pipeline.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability only — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# YouTube → Knowledge Graph Pipeline

## Why

A personal learning project to acquire hands-on proficiency with a specific set of modern ML and data-engineering tools — faster-whisper, LanceDB, Kuzu, Ollama, instructor, and the MCP SDK — by building a real, working system. The system ingests gym and fitness YouTube channels, transcribes audio locally, extracts a typed knowledge graph via a local LLM, and exposes query tools as an MCP server registered with Claude Code. The pipeline is useful in its own right (it will eventually feed the companion Gym Knowledge Base web UI), but the primary driver is learning-by-building: each pipeline stage introduces one or two new technologies in a concrete, debuggable context.

## Capabilities

- id: CAP-1
  intent: The system can discover and record all videos from configured YouTube channels, playlists, or individual video URLs without requiring API credentials.
  success: Given a `channels.yaml` entry of type `channel`, `playlist`, or `video`, `discover.py` populates the `videos` SQLite table with the relevant video IDs and metadata; re-running the same entry adds only genuinely new videos.

- id: CAP-2
  intent: The system can download the audio track of any tracked video at the minimum bitrate acceptable for speech recognition.
  success: `data/audio/{video_id}.m4a` exists at ≤96 kbps after download; the file is deleted automatically after transcription succeeds.

- id: CAP-3
  intent: The system can transcribe a downloaded audio file locally using faster-whisper, producing segment-level text with timestamps.
  success: Transcript JSON is written to `data/transcripts/{video_id}.json` with `start`/`end` per segment; a 60-minute video completes in under 15 minutes on the target GPU using `medium` at `float16`.

- id: CAP-4
  intent: The system can chunk transcripts into time-bounded, overlapping segments and store their embeddings in LanceDB for semantic search.
  success: A vector search query returns at least one chunk per relevant video, each carrying a valid `https://youtu.be/{id}?t={sec}` timestamp URL.

- id: CAP-5
  intent: The system can extract typed entity/relation triples from each chunk via a local LLM with schema-enforced structured output.
  success: ≥97% of chunks produce a valid `Extraction` matching the Pydantic schema; the remainder are logged with `error_stage=extract` and skipped without blocking any downstream stage.

- id: CAP-6
  intent: The system can deduplicate entity mentions across chunks by embedding similarity and LLM verification, producing a canonical entity table.
  success: A known alias pair (e.g. "Yann LeCun" / "LeCun") resolves to a single canonical node in Kuzu after an entity-resolution run.

- id: CAP-7
  intent: The system can load resolved triples into Kuzu with full provenance — video_id, chunk_id, evidence quote — attached to each edge.
  success: `MATCH (e:Entity {name: "..."}) RETURN e` returns the expected node; every `RELATED` edge carries non-null `evidence` and `video_id` properties.

- id: CAP-8
  intent: The system can extract academic paper references from video descriptions and transcripts, resolve them to structured metadata via OpenAlex, and download the full PDF for any paper with an open-access URL.
  success: For a video with at least one known paper reference, a `Paper` node with a valid DOI and title exists in Kuzu after the citation stage runs. For papers where OpenAlex returns a non-null `open_access.oa_url`, the PDF is downloaded to `data/papers/{doi_slug}.pdf`; papers without open-access URLs are recorded with metadata only and no PDF fetch is attempted.

- id: CAP-9
  intent: The system exposes vector search, Cypher query, entity neighborhood expansion, and topic-based paper lookup as MCP tools registered with Claude Code.
  success: With the MCP server active, asking Claude Code "What exercises target the posterior chain?" returns at least one transcript chunk with a YouTube timestamp URL and at least one related graph entity with an evidence quote.

- id: CAP-10
  intent: The pipeline is idempotent and resumable per video, tracking stage completion in SQLite and re-running only incomplete stages.
  success: Interrupting the pipeline mid-run and restarting it completes only the remaining stages; no duplicate rows appear in any store.

## Constraints

- Zero paid-API cost: all transcription, embedding, and LLM inference run locally via faster-whisper, sentence-transformers, and Ollama.
- All stores are embedded and serverless: SQLite (job state), LanceDB (vectors), Kuzu (graph) — no external database processes.
- Target hardware is RTX 5060 Ti (16 GB VRAM) + 32 GB DDR5; Whisper `medium` and the 7B extraction model must not OOM — run in separate passes if necessary.
- YouTube rate limits must be respected: yt-dlp must throttle requests; no burst downloading.
- LLM extraction failures (schema violations, timeouts) must be caught, logged, and skipped — never block the pipeline.
- Citation resolution uses OpenAlex (no API key) with Semantic Scholar as fallback; no paid citation services.
- The MCP server must be registrable in Claude Code's standard MCP config (`.claude/mcp.json` or equivalent).

## Non-goals

- Real-time or live-stream ingestion — batch/scheduled only.
- Multi-language extraction prompts — English-only for POC (faster-whisper handles multilingual transcription regardless).
- A standalone graph browser or visualization UI — graph is queried exclusively through Claude Code + MCP.
- Cloud deployment, containerization, or multi-user support.
- Model fine-tuning or training — inference only.
- Automatic synchronization with any external data warehouse.
- Production SLA, uptime guarantees, or monitoring beyond pipeline logs.
- Data integration between this pipeline and the Gym Knowledge Base web UI — that bridge is out of scope for this SPEC.

## Success signal

Starting from a `channels.yaml` with two gym or fitness YouTube channels and a clean data directory, `scripts/run_pipeline.py` processes at least five videos end-to-end and populates both LanceDB and Kuzu. A Claude Code session with the MCP server registered then answers "What exercises target the posterior chain?" with at least one transcript chunk citing a YouTube timestamp and at least one graph entity with an evidence quote.

## Assumptions

- The gym/fitness content domain is the initial target; entity types in `extraction_schema.py` (Person, Paper, Concept, Tool, Method, Dataset, Organization, Event) are an adequate starting schema, expected to extend after the first extraction run.
- `bge-small-en-v1.5` (384-dim) is sufficient embedding quality for POC; migration to `bge-m3` is deferred.
- Audio files are deleted after successful transcription — no persistent audio storage; re-download via yt-dlp is always possible.
- Prefect is deferred — plain Python loop + SQLite job table is the orchestration mechanism throughout POC.
- The companion Gym Knowledge Base web UI (existing `index.html`) is a downstream consumer of this pipeline's output, but that integration is a future concern outside this SPEC.
- `channels.yaml` supports `channel`, `playlist`, and `video` entry types from the start — not deferred.
- POC channels (5): Jeff Nippard, Squat University, Venus Gabby, Renaissance Periodization, Hybrid Calisthenics.
- Build progression: validate extraction on a single video before scaling to one playlist, then to full channels — do not ingest all 5 channels until the extraction flow is proven.
