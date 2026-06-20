# Sprint Fix S2: cite-pipeline

**Branch:** `fix/cite-pipeline`  
**Files:** `yt_kg/cite_extract.py`, `yt_kg/cite_resolve.py`

## Issues to fix

### Fix 1 — `cited_at` is never written, causing infinite re-processing

`yt_kg/cite_extract.py`, `cite_extract()` and `extract_citations()`.

`cite_extract()` selects videos where `cited_at IS NULL`. `extract_citations()` inserts into
`raw_citations` and commits, but **never updates `cited_at` in the `videos` table**. The column
is only written much later in `cite_pdf.py`. This means every pipeline run re-fetches the YouTube
description via yt-dlp and re-calls the LLM for every already-processed video.

**Fix:** At the end of `extract_citations()`, after `conn.commit()`, add:

```python
conn.execute(
    "UPDATE videos SET cited_at = ? WHERE video_id = ?",
    (utcnow(), video_id),
)
conn.commit()
```

### Fix 2 — DOI suffix stripping regex destroys valid DOI suffixes

`yt_kg/cite_resolve.py`, `_resolve_openalex()`, lines 46–48.

```python
raw_ref = re.sub(r'/[a-zA-Z]+$', '', raw_ref)
```

This is too broad. A DOI like `10.1152/japplphysiol.00234.abc` would have `abc` stripped, turning
it into a different or non-existent DOI. The intent is to strip URL navigation suffixes
(`/full`, `/abstract`, `/pdf`, `/html`) that get scraped along with the DOI from video
descriptions. Only strip known nav suffixes, not any alphabetic trailing segment.

**Fix:** Replace the broad regex with an explicit allowlist:

```python
raw_ref = re.sub(r'/(full|abstract|pdf|html|article|text)$', '', raw_ref, flags=re.IGNORECASE)
```

## Current file contents

### cite_extract.py

```python
import json
import logging
import os
import re
import uuid

import requests
import yt_dlp

from yt_kg.db import init_db, utcnow

logger = logging.getLogger(__name__)


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_citations (
            citation_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_ref TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(video_id, raw_ref)
        )
    """)
    conn.commit()


def extract_citations(video_id: str) -> None:
    conn = init_db()
    _init_table(conn)

    video_row = conn.execute("SELECT url FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    if not video_row or not video_row["url"]:
        logger.warning("No URL for video_id %s, skipping.", video_id)
        return

    description = ""
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(video_row["url"], download=False)
            if info and isinstance(info, dict):
                description = info.get("description", "") or ""
    except Exception as e:
        logger.error("Failed to fetch description for %s: %s", video_id, e)

    doi_pattern = re.compile(r'10\.\d{4,}/\S+')
    arxiv_pattern = re.compile(r'arXiv:\d{4}\.\d{4,5}')
    ts = utcnow()

    for ref in doi_pattern.findall(description):
        conn.execute(
            "INSERT OR IGNORE INTO raw_citations (citation_id, video_id, source, raw_ref, created_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), video_id, "description", ref.strip(), ts),
        )
    for ref in arxiv_pattern.findall(description):
        conn.execute(
            "INSERT OR IGNORE INTO raw_citations (citation_id, video_id, source, raw_ref, created_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), video_id, "description", ref.strip(), ts),
        )

    transcript_path = os.path.join("data", "transcripts", f"{video_id}.json")
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", []) if isinstance(data, dict) else data
            full_text = " ".join(s.get("text", "") for s in segments if isinstance(s, dict))

            prompt = (
                "List all academic paper references (DOIs, arXiv IDs, author/year citations) "
                f"from this text. One per line, nothing else:\n\n{full_text[:8000]}"
            )
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "qwen2.5-coder:7b", "prompt": prompt, "stream": False},
                timeout=60,
            )
            resp.raise_for_status()
            for line in resp.json().get("response", "").splitlines():
                ref = line.strip()
                if ref and (re.search(r'10\.\d{4,}/', ref) or re.search(r'(?i)arxiv:', ref) or re.search(r'\b(19|20)\d\d\b', ref)):
                    conn.execute(
                        "INSERT OR IGNORE INTO raw_citations (citation_id, video_id, source, raw_ref, created_at) VALUES (?,?,?,?,?)",
                        (str(uuid.uuid4()), video_id, "transcript", ref, ts),
                    )
        except Exception as e:
            logger.error("LLM extraction failed for %s: %s", video_id, e)

    conn.commit()


def cite_extract() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id FROM videos WHERE graphed_at IS NOT NULL AND cited_at IS NULL"
    ).fetchall()
    for row in rows:
        try:
            extract_citations(row["video_id"])
        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='cite' WHERE video_id=?",
                (str(e), row["video_id"]),
            )
            conn.commit()
```

### cite_resolve.py (relevant section — _resolve_openalex only)

```python
def _resolve_openalex(raw_ref: str) -> dict | None:
    if raw_ref.startswith("10."):
        raw_ref = re.sub(r'/[a-zA-Z]+$', '', raw_ref)
    if raw_ref.startswith("10."):
        url = f"https://api.openalex.org/works/doi:{raw_ref}"
    elif re.match(r'(?i)arxiv:', raw_ref):
        arxiv_id = re.sub(r'(?i)^arxiv:', '', raw_ref).strip()
        url = f"https://api.openalex.org/works/arxiv:{arxiv_id}"
    else:
        url = f"https://api.openalex.org/works?search={quote(raw_ref)}&per-page=1"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None
        data = resp.json()
        if "results" in data:
            if not data["results"]:
                return None
            return _parse_work(data["results"][0])
        return _parse_work(data)
    except Exception:
        return None
```

## Acceptance criteria

- `extract_citations()` writes `cited_at` to `videos` after committing citations
- `_resolve_openalex` strips only `/full`, `/abstract`, `/pdf`, `/html`, `/article`, `/text` suffixes (case-insensitive), not arbitrary alphabetic segments
- No other changes to either file
