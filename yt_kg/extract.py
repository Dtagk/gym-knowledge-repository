import json
import sqlite3

import instructor
import lancedb
from openai import OpenAI

from config.extraction_schema import Extraction
from yt_kg.db import init_db, utcnow

_client = instructor.from_openai(
    OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)


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

            prompt = (
                f"Extract entities and relations from this gym/fitness transcript excerpt:\n\n"
                f"{chunk['text']}\n\n"
                f"Return a JSON object with 'entities' and 'relations' arrays."
            )

            try:
                result = _client.chat.completions.create(
                    model="qwen-coder-32768:latest",
                    messages=[{"role": "user", "content": prompt}],
                    response_model=Extraction,
                    max_retries=2,
                    timeout=120.0,
                )
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
