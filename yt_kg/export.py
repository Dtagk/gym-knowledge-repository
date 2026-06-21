"""Export classified videos and their graph entities to docs/data/ for the frontend."""
import json
import os
from datetime import datetime, timezone

import kuzu

from yt_kg.db import init_db

_KUZU_PATH = "data/graph.kuzu"
_DOCS_DATA_DIR = "docs/data"


def _get_entities_for_video(video_id: str) -> list[dict]:
    """Query Kuzu for all entities that appear in the given video."""
    db = kuzu.Database(_KUZU_PATH)
    kuzu_conn = kuzu.Connection(db)
    result = kuzu_conn.execute(
        "MATCH (e:Entity)-[:APPEARS_IN]->(v:Video {video_id: $vid}) "
        "RETURN e.name, e.entity_type",
        {"vid": video_id},
    )
    entities = []
    while result.has_next():
        row = result.get_next()
        entities.append({"name": row[0], "type": row[1]})
    return entities


def export() -> None:
    """Export approved/config videos with classifications to docs/data/."""
    conn = init_db()

    rows = conn.execute(
        "SELECT * FROM videos "
        "WHERE body_parts IS NOT NULL AND skipped = 0 "
        "AND (status = 'approved' OR source = 'config')"
    ).fetchall()

    videos = []
    all_entity_names: set[str] = set()

    for row in rows:
        video_id = row["video_id"]
        entities = _get_entities_for_video(video_id)

        for ent in entities:
            if ent["name"]:
                all_entity_names.add(ent["name"])

        videos.append(
            {
                "video_id": video_id,
                "title": row["title"],
                "channel_id": row["channel_id"],
                "url": f"https://youtu.be/{video_id}",
                "body_parts": json.loads(row["body_parts"] or "[]"),
                "use_cases": json.loads(row["use_cases"] or "[]"),
                "entities": entities,
            }
        )

    conn.close()

    os.makedirs(_DOCS_DATA_DIR, exist_ok=True)

    with open(os.path.join(_DOCS_DATA_DIR, "videos.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(videos, indent=2, ensure_ascii=False))

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "video_count": len(videos),
        "entity_count": len(all_entity_names),
    }
    with open(os.path.join(_DOCS_DATA_DIR, "meta.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    export()
