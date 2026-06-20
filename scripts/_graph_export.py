"""Export the Kuzu graph to a flat JSON for the standalone inspector.

Inspection only — not part of the pipeline deliverable. Reads the graph
read-only and writes data/graph_export.json, which graph_viewer.html loads.

Usage:
    python scripts/_graph_export.py [--db data/graph.kuzu] [--out data/graph_export.json]
                                    [--min-degree 1] [--limit-edges 5000]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import kuzu

ROOT = Path(__file__).parent.parent


def export(db_path: Path, out_path: Path, min_degree: int, limit_edges: int) -> None:
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)

    # Entity nodes
    nodes: dict[str, dict] = {}
    res = conn.execute(
        "MATCH (e:Entity) RETURN e.canonical_id, e.name, e.entity_type, e.entity_desc"
    )
    while res.has_next():
        cid, name, etype, desc = res.get_next()
        nodes[cid] = {
            "id": cid,
            "name": name or cid,
            "type": etype or "unknown",
            "desc": desc or "",
            "degree": 0,
        }

    # Entity↔Entity RELATED edges (carry the provenance we care about)
    edges: list[dict] = []
    degree: dict[str, int] = defaultdict(int)
    res = conn.execute(
        "MATCH (a:Entity)-[r:RELATED]->(b:Entity) "
        "RETURN a.canonical_id, b.canonical_id, r.predicate, r.evidence, r.video_id "
        f"LIMIT {int(limit_edges)}"
    )
    while res.has_next():
        a, b, pred, evidence, vid = res.get_next()
        if a not in nodes or b not in nodes:
            continue
        edges.append({
            "source": a,
            "target": b,
            "predicate": pred or "related",
            "evidence": evidence or "",
            "video_id": vid or "",
        })
        degree[a] += 1
        degree[b] += 1

    for cid, d in degree.items():
        nodes[cid]["degree"] = d

    # Prune isolated / low-degree nodes for a readable inspection view
    kept = {cid: n for cid, n in nodes.items() if n["degree"] >= min_degree}
    edges = [e for e in edges if e["source"] in kept and e["target"] in kept]

    types = sorted({n["type"] for n in kept.values()})
    out = {
        "nodes": list(kept.values()),
        "edges": edges,
        "types": types,
        "stats": {
            "entities_total": len(nodes),
            "entities_shown": len(kept),
            "edges_shown": len(edges),
        },
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False))
    s = out["stats"]
    print(f"Exported {s['entities_shown']}/{s['entities_total']} entities, "
          f"{s['edges_shown']} edges → {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/graph.kuzu"))
    ap.add_argument("--out", default=str(ROOT / "data/graph_export.json"))
    ap.add_argument("--min-degree", type=int, default=1,
                    help="drop entities with fewer than this many RELATED edges")
    ap.add_argument("--limit-edges", type=int, default=5000)
    args = ap.parse_args()
    export(Path(args.db), Path(args.out), args.min_degree, args.limit_edges)


if __name__ == "__main__":
    main()
