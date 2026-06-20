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
            # our transcript format: {"language":..., "segments": [{text, start, end}, ...]}
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
