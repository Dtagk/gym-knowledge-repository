# Technology Stack

Load-bearing component selection for the yt-kg pipeline. Every choice satisfies three constraints: zero paid APIs, all stores embedded (no server processes), and fits within 16 GB VRAM.

## Components

| Component | Choice | Why / notes |
|---|---|---|
| Channel/video discovery | `yt-dlp` Python API | No YouTube Data API key required; supports `channel`, `playlist`, and `video` entry types in `channels.yaml` |
| Audio download | `yt-dlp -x`, m4a at ≤96 kbps | Minimum bitrate for ASR quality; postprocessor strips video |
| Transcription | `faster-whisper` (CTranslate2) | 4–10× faster than openai-whisper; VAD filter mandatory |
| Embeddings | `sentence-transformers` + `BAAI/bge-small-en-v1.5` (384-dim) | CPU-runnable; upgrade path to `bge-m3` for hybrid dense+sparse |
| Vector store | **LanceDB** | Embedded, columnar, no server process |
| LLM runtime | **Ollama** (`http://localhost:11434/v1`) | Local inference, OpenAI-compatible endpoint |
| Extraction model | **Qwen2.5-7B-Instruct** (primary) / Llama-3.1-8B-Instruct (alt) | Both produce reliable JSON via Ollama `format: "json"` |
| Structured output | `pydantic` + `instructor` (`Mode.JSON`, Ollama backend) | Schema-enforced; up to 2 auto-retries on validation failure |
| Graph DB | **Kuzu** | Embedded, Cypher-compatible, columnar; no server process |
| Job state | SQLite (`data/jobs.sqlite`) | Per-video stage flags; idempotent select pattern |
| Orchestration | Plain Python loop | Prefect deferred to post-POC |
| Citation resolution | **OpenAlex** (no key) → Semantic Scholar fallback | Free, no rate-limit pain at POC scale; `open_access.oa_url` field used for PDF fetch |
| PDF download | `httpx` (already in stack) | GET `open_access.oa_url`; skip if null; store at `data/papers/{doi_slug}.pdf` |
| MCP server | `mcp` Python SDK, `FastMCP` | Registers with Claude Code via standard MCP config |
| Language / packaging | Python 3.11+, `pyproject.toml` | |

## Hardware targets

| Workload | Device | Setting |
|---|---|---|
| Whisper `medium` | GPU (RTX 5060 Ti 16 GB) | `device="cuda"`, `compute_type="float16"` |
| Whisper `small` (CPU fallback) | CPU | `device="cpu"`, `compute_type="int8"` |
| Qwen2.5-7B extraction | GPU | Q4_K_M via Ollama |
| `bge-small-en-v1.5` embeddings | CPU | Batch inference; no GPU allocation needed |

Whisper and the extraction LLM run in **separate passes**, not concurrently — both fit in 16 GB individually; concurrent load would OOM.

## Extraction schema (starting typology)

Entity types: `Person`, `Paper`, `Concept`, `Tool`, `Method`, `Dataset`, `Organization`, `Event`

Relation types: `WORKS_AT`, `AUTHORED`, `CITES`, `INTRODUCES`, `USES`, `RELATED_TO`, `CRITIQUES`, `EXTENDS`, `PART_OF`

Expected to extend after first extraction run on fitness content (anatomy terms, exercise taxonomy, trainer names).

## POC channels

| Channel | Focus area |
|---|---|
| Jeff Nippard | Evidence-based hypertrophy, programming |
| Squat University | Movement quality, injury prevention, mobility |
| Venus Gabby | Strength training, female athlete perspective |
| Renaissance Periodization | Periodization science, diet, hypertrophy research |
| Hybrid Calisthenics | Bodyweight / barbell hybrid programming |

Entity vocabulary to expect: muscle names, lift names (squat, deadlift, RDL, …), periodization terms (mesocycle, RPE, 1RM, …), researcher names (Schoenfeld, Israetel, …), and paper titles/DOIs.
