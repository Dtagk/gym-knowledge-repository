# Sprint Fix S3: extraction-robustness

**Branch:** `fix/extraction-robustness`  
**Files:** `yt_kg/extract.py` only

## Issues to fix

### Fix 1 — `extracted_at` written even on 100% chunk failure

`yt_kg/extract.py`, lines 162–172.

After processing all chunks for a video, `extracted_at` is unconditionally written to the database
regardless of failure rate. The 3% threshold only adds an error note, but the video still gets
marked as extracted and moves to the graph stage — which then builds a graph with missing or empty
relations because Ollama was down.

**Fix:** Only write `extracted_at` when the failure rate is within the acceptable threshold.
When failures exceed 3%, write the error but leave `extracted_at` NULL so the video retries:

```python
failure_rate = failures / len(chunks) if chunks else 0.0
if failure_rate > 0.03:
    conn.execute(
        "UPDATE videos SET last_error = ?, error_stage = ? WHERE video_id = ?",
        (f"{failures}/{len(chunks)} chunks failed extraction", "extract", video_id),
    )
else:
    conn.execute(
        "UPDATE videos SET extracted_at = ? WHERE video_id = ?",
        (utcnow(), video_id),
    )
conn.commit()
```

### Fix 2 — `video_id` interpolated into LanceDB filter string (SQL injection)

`yt_kg/extract.py`, line 124.

```python
chunks = tbl.search([0.0] * 384).where(
    f"video_id = '{video_id}'", prefilter=True
).limit(100000).to_list()
```

`video_id` comes from the SQLite `videos` table (originally from yt-dlp). LanceDB `.where()` does
not support parameterized queries, so `video_id` must be validated before interpolation. YouTube
video IDs are 11 alphanumeric+hyphen+underscore characters, so a strict allowlist is safe and
sufficient.

**Fix:** Add validation at the top of the chunk-fetching block, before the `.where()` call:

```python
import re
if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id):
    raise ValueError(f"Unexpected video_id format: {video_id!r}")
```

The same pattern also appears in the fallback branches (pandas query and the unfiltered scan
fallback). Add the validation once, before all three branches.

## Current file content

```python
import sqlite3
from pydantic import BaseModel
from typing import Optional

import instructor
import lancedb
from gliner import GLiNER
from openai import OpenAI

from config.extraction_schema import Entity, Extraction, Relation
from yt_kg.db import init_db, utcnow

_LABELS = [
    "fitness exercise or movement",
    "muscle group or body part",
    "scientific study or research paper",
    "fitness equipment or machine",
    "training technique or programming method",
    "fitness trainer, researcher or athlete",
    "gym, company or organization",
    "nutritional concept or supplement",
]
_LABEL_MAP = {
    "fitness exercise or movement": "Method",
    "muscle group or body part": "Concept",
    "scientific study or research paper": "Paper",
    "fitness equipment or machine": "Tool",
    "training technique or programming method": "Method",
    "fitness trainer, researcher or athlete": "Person",
    "gym, company or organization": "Organization",
    "nutritional concept or supplement": "Concept",
}
_PRONOUNS = {"i", "me", "we", "us", "you", "he", "she", "they", "them", "it", "my", "your", "our"}

_gliner_model: Optional[GLiNER] = None


def _gliner() -> GLiNER:
    global _gliner_model
    if _gliner_model is None:
        _gliner_model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
    return _gliner_model


_llm = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)


class _Relations(BaseModel):
    relations: list[Relation]


def _init_extractions_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_extractions (
            chunk_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            extraction_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _extract_chunk(text: str) -> Extraction:
    # Entities: GLiNER — fast, no LLM, runs on CPU
    spans = _gliner().predict_entities(text, _LABELS, threshold=0.4)
    seen: set[tuple[str, str]] = set()
    entities: list[Entity] = []
    for s in spans:
        if s["text"].lower().strip() in _PRONOUNS:
            continue
        key = (s["text"].lower(), s["label"])
        if key not in seen:
            seen.add(key)
            entities.append(Entity(name=s["text"], type=_LABEL_MAP[s["label"]], description=""))

    # Relations: LLM only when ≥2 entities found
    relations: list[Relation] = []
    if len(entities) >= 2:
        names = [e.name for e in entities]
        prompt = (
            f"Entities found in this fitness transcript excerpt: {names}\n\n"
            f"{text}\n\n"
            f"Return JSON with a 'relations' array. Each item has: "
            f"subject, predicate, object (from the entity list), evidence (exact quote from text)."
        )
        try:
            r = _llm.chat.completions.create(
                model="qwen2.5-coder:7b",
                messages=[{"role": "user", "content": prompt}],
                response_model=_Relations,
                max_retries=1,
                timeout=60.0,
            )
            relations = r.relations
        except Exception as exc:
            print(f"[extract] relations failed: {exc}")

    return Extraction(entities=entities, relations=relations)


def extract() -> None:
    conn = init_db()
    _init_extractions_table(conn)

    rows = conn.execute(
        "SELECT video_id FROM videos WHERE chunked_at IS NOT NULL AND extracted_at IS NULL"
    ).fetchall()

    db = lancedb.connect("data/vectors.lance")
    tbl = db.open_table("chunks")

    existing_ids = {
        r[0] for r in conn.execute("SELECT chunk_id FROM raw_extractions").fetchall()
    }

    for row in rows:
        video_id = row["video_id"]

        try:
            chunks = tbl.search([0.0] * 384).where(
                f"video_id = '{video_id}'", prefilter=True
            ).limit(100000).to_list()
        except Exception:
            try:
                import pandas as pd  # noqa: F401
                chunks = tbl.to_pandas().query(f"video_id == '{video_id}'").to_dict("records")
            except Exception:
                all_chunks = tbl.search([0.0] * 384).limit(100000).to_list()
                chunks = [c for c in all_chunks if c.get("video_id") == video_id]

        if not chunks:
            conn.execute(
                "UPDATE videos SET extracted_at = ? WHERE video_id = ?",
                (utcnow(), video_id),
            )
            conn.commit()
            continue

        failures = 0

        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id in existing_ids:
                continue

            try:
                result = _extract_chunk(chunk["text"])
                conn.execute(
                    "INSERT OR REPLACE INTO raw_extractions (chunk_id, video_id, extraction_json, created_at) VALUES (?, ?, ?, ?)",
                    (chunk_id, video_id, result.model_dump_json(), utcnow()),
                )
                conn.commit()
                existing_ids.add(chunk_id)
            except Exception as exc:
                print(f"[extract] chunk {chunk_id} failed: {exc}")
                failures += 1

        conn.execute(
            "UPDATE videos SET extracted_at = ? WHERE video_id = ?",
            (utcnow(), video_id),
        )
        failure_rate = failures / len(chunks) if chunks else 0.0
        if failure_rate > 0.03:
            conn.execute(
                "UPDATE videos SET last_error = ?, error_stage = ? WHERE video_id = ?",
                (f"{failures}/{len(chunks)} chunks failed extraction", "extract", video_id),
            )
        conn.commit()
```

## Acceptance criteria

- `extracted_at` is only written when `failure_rate <= 0.03`; on high failure, only the error is written and `extracted_at` stays NULL
- `video_id` is validated with `re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id)` before any LanceDB filter interpolation; a `ValueError` is raised for invalid IDs
- Validation happens once, before the try/except block with the three fallback branches
- No other changes to the file
