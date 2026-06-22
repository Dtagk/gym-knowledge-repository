"""Classify graphed videos by body_parts and use_cases using Ollama."""
import argparse
import json
import re
import sqlite3

import httpx
import kuzu

from yt_kg.db import init_db

_KUZU_PATH = "data/graph.kuzu"

_BODY_PARTS = ["shoulder", "chest", "back", "legs", "arms", "core", "full-body"]
_USE_CASES = ["rehab", "mobility", "strength", "hypertrophy", "general-fitness", "programming"]

_OLLAMA_URL = "http://localhost:11434/api/chat"
_OLLAMA_MODEL = "qwen2.5-coder:7b"


def _migrate(conn: sqlite3.Connection) -> None:
    """Add body_parts and use_cases columns if they don't exist yet."""
    for ddl in (
        "ALTER TABLE videos ADD COLUMN body_parts TEXT",
        "ALTER TABLE videos ADD COLUMN use_cases TEXT",
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _get_top_entities(video_id: str) -> list[str]:
    """Query Kuzu for the top-5 entity names that appear in the given video."""
    db = kuzu.Database(_KUZU_PATH)
    kuzu_conn = kuzu.Connection(db)
    result = kuzu_conn.execute(
        "MATCH (e:Entity)-[:APPEARS_IN]->(v:Video {video_id: $vid}) "
        "RETURN e.name LIMIT 5",
        {"vid": video_id},
    )
    names = []
    while result.has_next():
        row = result.get_next()
        names.append(row[0])
    return names


def _call_ollama(title: str, entity_names: list[str]) -> dict:
    """Call Ollama to classify the video; return parsed dict with body_parts and use_cases."""
    entities_str = ", ".join(entity_names) if entity_names else "(none)"
    user_content = (
        f"Classify this YouTube fitness video.\n"
        f"Title: {title}\n"
        f"Top entities mentioned: {entities_str}\n\n"
        f'Reply with exactly this JSON structure:\n'
        f'{{"body_parts": [...], "use_cases": [...]}}\n\n'
        f'body_parts must be a subset of: {json.dumps(_BODY_PARTS)}\n'
        f'use_cases must be a subset of: {json.dumps(_USE_CASES)}\n'
        f"Both arrays can be empty if nothing applies."
    )

    resp = httpx.post(
        _OLLAMA_URL,
        json={
            "model": _OLLAMA_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a fitness video classifier. Reply with only valid JSON, no explanation.",
                },
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    text = resp.json()["message"]["content"]

    # Strip markdown code fences if present
    text = re.sub(r"^```json\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    parsed = json.loads(text)
    return parsed


def classify(reclassify: bool = False) -> None:
    """Classify all graphed, non-skipped videos by body_parts and use_cases.

    Args:
        reclassify: If True, re-classify videos that already have classifications.
    """
    conn = init_db()
    _migrate(conn)

    rows = conn.execute(
        "SELECT video_id, title FROM videos "
        "WHERE graphed_at IS NOT NULL AND skipped = 0 "
        "AND (body_parts IS NULL OR ?)",
        (reclassify,),
    ).fetchall()

    for row in rows:
        video_id = row["video_id"]
        title = row["title"] or ""

        try:
            entity_names = _get_top_entities(video_id)
            parsed = _call_ollama(title, entity_names)

            body_parts = parsed.get("body_parts", [])
            use_cases = parsed.get("use_cases", [])

            # Filter to only known values
            body_parts = [v for v in body_parts if v in _BODY_PARTS]
            use_cases = [v for v in use_cases if v in _USE_CASES]

            conn.execute(
                "UPDATE videos SET body_parts = ?, use_cases = ? WHERE video_id = ?",
                (json.dumps(body_parts), json.dumps(use_cases), video_id),
            )
            conn.commit()

        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error = ?, error_stage = 'classify' WHERE video_id = ?",
                (str(e), video_id),
            )
            conn.commit()
            continue

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify videos by body_parts and use_cases")
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="Re-classify videos that already have classifications",
    )
    args = parser.parse_args()
    classify(reclassify=args.reclassify)
