# Developer Brief — Epic 1: Local Ingestion Pipeline with Semantic Search

## Goal

Implement a pipeline that discovers YouTube videos, downloads audio, transcribes locally with faster-whisper, chunks and embeds transcripts into LanceDB, and exposes semantic search returning YouTube timestamp URLs.

After this epic: `python scripts/run_pipeline.py` processes a single video end-to-end and a vector search for "posterior chain exercises" returns at least one `https://youtu.be/...?t=...` result.

---

## Repo layout to create

```
gym-knowledge-repository/
├── pyproject.toml
├── config/
│   └── channels.yaml
├── yt_kg/
│   ├── __init__.py
│   ├── db.py          # SQLite connection + schema init
│   ├── discover.py
│   ├── download.py
│   ├── transcribe.py
│   ├── chunk.py
│   └── embed.py
├── scripts/
│   └── run_pipeline.py
└── data/              # gitignored — created at runtime
    ├── audio/
    ├── transcripts/
    ├── jobs.sqlite
    └── vectors.lance/
```

---

## Tech stack (fixed — do not substitute)

| Concern | Library / tool |
|---|---|
| Discovery + download | `yt-dlp` Python API |
| Transcription | `faster-whisper` (CTranslate2) |
| Embeddings | `sentence-transformers`, model `BAAI/bge-small-en-v1.5` |
| Vector store | `lancedb` |
| Job state | `sqlite3` (stdlib) |
| Config | `PyYAML` |

Python 3.11+. `pyproject.toml` with `[project]` table, no setup.py.

---

## SQLite schema (`data/jobs.sqlite`)

```sql
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    title           TEXT,
    channel_id      TEXT,
    url             TEXT,
    downloaded_at   TEXT,   -- ISO UTC timestamp or NULL
    transcribed_at  TEXT,
    chunked_at      TEXT,
    extracted_at    TEXT,   -- reserved for Epic 2
    graphed_at      TEXT,   -- reserved for Epic 2
    cited_at        TEXT,   -- reserved for Epic 3
    last_error      TEXT,
    error_stage     TEXT
);
```

Each stage selects `WHERE <prev>_at IS NOT NULL AND <this>_at IS NULL`. Never re-run a completed stage.

---

## `config/channels.yaml` format

```yaml
channels:
  - id: jeff-nippard
    url: https://www.youtube.com/@JeffNippard
    type: channel       # channel | playlist | video
    poll_every: 7d
  - id: single-video-test
    url: https://www.youtube.com/watch?v=XXXXXXXXXXX
    type: video
    poll_every: never
```

---

## LanceDB schema (`data/vectors.lance/`)

Table name: `chunks`

| Field | Type |
|---|---|
| `chunk_id` | `str` — `"{video_id}:{i}"` |
| `video_id` | `str` |
| `start` | `float` — seconds |
| `end` | `float` |
| `text` | `str` |
| `vector` | `list[float]` — 384-dim bge-small-en-v1.5 |

---

## Stories (implement in order — each builds on the previous)

### Story 1.1 — Project Scaffold + Video Discovery

Create `pyproject.toml`, `yt_kg/db.py` (SQLite init), and `yt_kg/discover.py`.

`discover.py` reads `channels.yaml` and uses `yt-dlp`'s Python API to enumerate videos:
- `type: video` → 1 row
- `type: playlist` → 1 row per video in playlist
- `type: channel` → 1 row per video in channel

Insert each as `INSERT OR IGNORE INTO videos (video_id, title, channel_id, url)`. All stage flags start NULL.

**AC:**
- `pip install -e .` succeeds
- `video` entry → exactly 1 row; `channel` entry → N rows; re-run → no duplicates
- All stage flag columns are NULL after discovery

---

### Story 1.2 — Throttled Audio Download

Create `yt_kg/download.py`.

For each `WHERE downloaded_at IS NULL` row:
- Use `yt-dlp` with `format='bestaudio[abr<=96]/bestaudio'`, postprocessor to extract m4a, output to `data/audio/{video_id}.m4a`
- Pass `--sleep-requests 2 --max-sleep-interval 5` (or equivalent yt-dlp Python options) to throttle
- On success: set `downloaded_at = utcnow()`
- On any exception: write `last_error` + `error_stage='download'`; continue to next video

**AC:**
- `data/audio/{video_id}.m4a` exists at ≤96 kbps after download
- `downloaded_at` set on success; row with `downloaded_at IS NOT NULL` is skipped on re-run
- Download failure logs error and continues

---

### Story 1.3 — Local Transcription with faster-whisper

Create `yt_kg/transcribe.py`.

For each `WHERE downloaded_at IS NOT NULL AND transcribed_at IS NULL`:
- Load `WhisperModel` — try `("medium", device="cuda", compute_type="float16")`; if CUDA unavailable fall back to `("small", device="cpu", compute_type="int8")`
- Call `model.transcribe(audio_path, vad_filter=True)` — VAD is mandatory
- Write `data/transcripts/{video_id}.json`:
  ```json
  {"language": "en", "duration": 3600.0, "segments": [{"start": 0.0, "end": 5.2, "text": "..."}]}
  ```
- On success: delete `data/audio/{video_id}.m4a`, set `transcribed_at`
- On failure: set `last_error` + `error_stage='transcribe'`; do NOT delete audio

**AC:**
- Transcript JSON has `language`, `duration`, `segments[]` with `start`/`end`/`text`
- Audio deleted only on success
- CPU fallback loads automatically if no CUDA

---

### Story 1.4 — Time-Aware Chunking and LanceDB Embedding

Create `yt_kg/chunk.py` and `yt_kg/embed.py`.

**`chunk.py`** — for each `WHERE transcribed_at IS NOT NULL AND chunked_at IS NULL`:
- Load `data/transcripts/{video_id}.json`
- Slide a window of ~2400 chars with ~300-char overlap over the segments array
- Each chunk carries `start` = first segment's start, `end` = last segment's end
- Return list of dicts: `{chunk_id, video_id, start, end, text}`

**`embed.py`**:
- Load `SentenceTransformer("BAAI/bge-small-en-v1.5")` on CPU (no `device="cuda"`)
- Batch-encode all chunk texts for the video
- Upsert into LanceDB `chunks` table (create table on first run)
- Set `chunked_at` on success

**AC:**
- Chunks are ~2400 chars with overlap; each carries valid `start`/`end`
- LanceDB `chunks` table contains rows with correct schema
- `chunked_at` set; re-run skips (idempotent)
- Vector search on a relevant query returns rows including `https://youtu.be/{video_id}?t={int(start)}`

---

### Story 1.5 — Pipeline Orchestrator and Smoke Test

Create `scripts/run_pipeline.py`.

```python
# Pseudocode — implement with real imports
discover()   # always runs — adds new videos only
download()   # WHERE downloaded_at IS NULL
transcribe() # WHERE downloaded_at IS NOT NULL AND transcribed_at IS NULL
chunk()      # WHERE transcribed_at IS NOT NULL AND chunked_at IS NULL
embed()      # same gate as chunk, or combined
```

Each stage call catches exceptions per-video and logs them without crashing the loop.

End with a smoke test vector search:
```python
results = table.search(embed_query("posterior chain exercises")).limit(5).to_list()
for r in results:
    print(f"https://youtu.be/{r['video_id']}?t={int(r['start'])}")
```

**AC:**
- Fresh run processes a single video end-to-end; `downloaded_at`, `transcribed_at`, `chunked_at` all set
- Interrupted after transcription → restart runs only chunk+embed, no duplicate transcript or LanceDB rows
- One video fails download → others proceed
- All stages complete → re-run executes no stages (all WHERE clauses empty)
- Smoke test returns ≥1 result with valid timestamp URL

---

## Key constraints

- **No concurrent GPU use.** Whisper and the embedding model must not run at the same time. Embeddings are CPU-only (`bge-small-en-v1.5` on CPU is fast enough).
- **VAD is mandatory** on every Whisper call — `vad_filter=True` always.
- **Throttle yt-dlp** — use sleep between requests; no burst downloading.
- **Audio deleted after successful transcription** — no persistent audio storage.
- **Idempotent everywhere** — `INSERT OR IGNORE`, upsert in LanceDB, stage flag checks.
- **No framework** — plain Python loop, no Prefect, no Celery.
- **Error isolation** — one video failing must never block others; always log `last_error`/`error_stage` and continue.
