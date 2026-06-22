"""Export classified videos and their graph entities to docs/data/ for the frontend."""
import json
import os
from collections import Counter
from datetime import datetime, timezone

import kuzu

from yt_kg.db import init_db

_KUZU_PATH = "data/graph.kuzu"
_DOCS_DATA_DIR = "docs/data"


def _get_entities_for_video(video_id: str) -> list[dict]:
    """Query Kuzu for all entities that appear in the given video."""
    db = kuzu.Database(_KUZU_PATH, read_only=True)
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


def _export_entities(videos: list[dict]) -> None:
    """Query all Entity nodes and write docs/data/entities.json."""
    try:
        db = kuzu.Database(_KUZU_PATH, read_only=True)
        kuzu_conn = kuzu.Connection(db)

        # Build a lookup map: video_id -> use_cases list
        video_use_cases: dict[str, list[str]] = {
            v["video_id"]: v.get("use_cases", []) for v in videos
        }

        # 1. Fetch all entities
        result = kuzu_conn.execute(
            "MATCH (e:Entity) RETURN e.canonical_id, e.name, e.entity_type"
        )
        all_entities = []
        while result.has_next():
            row = result.get_next()
            all_entities.append({"canonical_id": row[0], "name": row[1], "entity_type": row[2]})

        entities_dict: dict[str, dict] = {}

        for ent in all_entities:
            cid = ent["canonical_id"]
            if not cid:
                continue

            # 2. Video IDs this entity appears in
            vid_result = kuzu_conn.execute(
                "MATCH (e:Entity {canonical_id: $cid})-[:APPEARS_IN]->(v:Video) "
                "RETURN v.video_id",
                {"cid": cid},
            )
            video_ids: list[str] = []
            while vid_result.has_next():
                row = vid_result.get_next()
                video_ids.append(row[0])

            # 3. Related entities (first degree, limit 5)
            rel_result = kuzu_conn.execute(
                "MATCH (e:Entity {canonical_id: $cid})-[:RELATED]->(e2:Entity) "
                "RETURN e2.name, e2.entity_type LIMIT 5",
                {"cid": cid},
            )
            related: list[dict] = []
            while rel_result.has_next():
                row = rel_result.get_next()
                related.append({"name": row[0], "type": row[1]})

            # 4. Compute use_case_counts from the videos list
            use_case_counter: Counter = Counter()
            for vid_id in video_ids:
                for use_case in video_use_cases.get(vid_id, []):
                    use_case_counter[use_case] += 1

            entities_dict[cid] = {
                "name": ent["name"],
                "type": ent["entity_type"],
                "video_ids": video_ids,
                "related": related,
                "use_case_counts": dict(use_case_counter),
            }

        os.makedirs(_DOCS_DATA_DIR, exist_ok=True)
        with open(
            os.path.join(_DOCS_DATA_DIR, "entities.json"), "w", encoding="utf-8"
        ) as f:
            f.write(json.dumps(entities_dict, indent=2, ensure_ascii=False))

    except Exception as exc:  # noqa: BLE001
        print(f"[export] WARNING: entity export failed -- {exc}")


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

    _export_entities(videos)
    _export_graph()


def _export_graph() -> None:
    """Dump the whole KG as nodes + links JSON for the graph explorer page.

    Node types: Entity, Paper, Concept, TechniqueCue, Video.
    Edge types: RELATED, DISCUSSES, ABOUT, CITES, HAS_TECHNIQUE, REFERENCES, APPEARS_IN.
    Kept compact (ids + minimal labels) so the static page loads fast.
    """
    try:
        db = kuzu.Database(_KUZU_PATH, read_only=True)
        conn = kuzu.Connection(db)
    except Exception as exc:
        print(f"[export] WARNING: graph export skipped -- {exc}")
        return

    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def add_node(node_id: str, label: str, ntype: str) -> None:
        if node_id and node_id not in nodes:
            nodes[node_id] = {"id": node_id, "label": label or node_id, "type": ntype}

    # --- nodes ---
    node_queries = [
        ("MATCH (e:Entity) RETURN e.canonical_id, e.name", "Entity"),
        ("MATCH (p:Paper) RETURN p.doi, p.title", "Paper"),
        ("MATCH (c:Concept) RETURN c.concept_id, c.name", "Concept"),
        ("MATCH (t:TechniqueCue) RETURN t.cue_id, t.text", "TechniqueCue"),
    ]
    for q, ntype in node_queries:
        try:
            r = conn.execute(q)
        except Exception:
            continue
        while r.has_next():
            node_id, label = r.get_next()
            if ntype == "Paper" and label:
                label = label[:60]
            add_node(node_id, label, ntype)

    # --- edges ---
    def add_edges(query: str, etype: str, resolve_src=None, resolve_dst=None) -> None:
        try:
            r = conn.execute(query)
        except Exception:
            return
        while r.has_next():
            s, d = r.get_next()
            s = resolve_src(s) if resolve_src else s
            d = resolve_dst(d) if resolve_dst else d
            if s in nodes and d in nodes:
                links.append({"source": s, "target": d, "type": etype})

    add_edges("MATCH (a:Entity)-[:RELATED]->(b:Entity) RETURN a.canonical_id, b.canonical_id", "RELATED")
    add_edges("MATCH (p:Paper)-[:DISCUSSES]->(e:Entity) RETURN p.doi, e.canonical_id", "DISCUSSES")
    add_edges("MATCH (p:Paper)-[:ABOUT]->(c:Concept) RETURN p.doi, c.concept_id", "ABOUT")
    add_edges("MATCH (a:Paper)-[:CITES]->(b:Paper) RETURN a.doi, b.doi", "CITES")
    add_edges("MATCH (e:Entity)-[:HAS_TECHNIQUE]->(t:TechniqueCue) RETURN e.canonical_id, t.cue_id", "HAS_TECHNIQUE")

    graph = {
        "nodes": list(nodes.values()),
        "links": links,
        "counts": {
            "nodes": len(nodes),
            "links": len(links),
            "by_type": dict(Counter(n["type"] for n in nodes.values())),
        },
    }
    os.makedirs(_DOCS_DATA_DIR, exist_ok=True)
    with open(os.path.join(_DOCS_DATA_DIR, "graph.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(graph, ensure_ascii=False))
    print(f"[export] graph.json: {len(nodes)} nodes, {len(links)} links")


if __name__ == "__main__":
    export()