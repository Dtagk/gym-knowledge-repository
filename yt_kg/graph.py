import re

import kuzu
import lancedb

from yt_kg.db import init_db, utcnow

_KUZU_PATH = "data/graph.kuzu"


def _init_graph() -> kuzu.Connection:
    db = kuzu.Database(_KUZU_PATH)
    conn = kuzu.Connection(db)

    ddl = [
        "CREATE NODE TABLE IF NOT EXISTS Entity (canonical_id STRING, name STRING, entity_type STRING, entity_desc STRING, PRIMARY KEY (canonical_id))",
        "CREATE NODE TABLE IF NOT EXISTS Video (video_id STRING, title STRING, channel_id STRING, PRIMARY KEY (video_id))",
        "CREATE NODE TABLE IF NOT EXISTS Chunk (chunk_id STRING, video_id STRING, start DOUBLE, end_time DOUBLE, text STRING, PRIMARY KEY (chunk_id))",
        "CREATE NODE TABLE IF NOT EXISTS Paper (doi STRING, title STRING, authors STRING, year INT64, PRIMARY KEY (doi))",
        "CREATE REL TABLE IF NOT EXISTS MENTIONS (FROM Chunk TO Entity)",
        "CREATE REL TABLE IF NOT EXISTS APPEARS_IN (FROM Entity TO Video)",
        "CREATE REL TABLE IF NOT EXISTS REFERENCES (FROM Chunk TO Paper)",
        "CREATE REL TABLE IF NOT EXISTS RELATED (FROM Entity TO Entity, predicate STRING, evidence STRING, video_id STRING)",
    ]

    for stmt in ddl:
        try:
            conn.execute(stmt)
        except Exception:
            pass

    return conn


def load_video(video_id: str) -> None:
    sql_conn = init_db()

    try:
        kuzu_conn = _init_graph()
        video_row = sql_conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if video_row is None:
            return

        kuzu_conn.execute(
            "MERGE (v:Video {video_id: $vid}) SET v.title = $title, v.channel_id = $cid",
            {"vid": video_id, "title": video_row["title"] or "", "cid": video_row["channel_id"] or ""},
        )

        alias_rows = sql_conn.execute(
            "SELECT alias, type, canonical_id FROM entity_aliases"
        ).fetchall()
        alias_map = {(r["alias"], r["type"]): r["canonical_id"] for r in alias_rows}
        # ponytail: relations lack type info, so resolve by name only; first type wins
        name_to_canonical = {}
        for (alias, _), cid in alias_map.items():
            name_to_canonical.setdefault(alias, cid)

        entity_rows = sql_conn.execute("SELECT * FROM entities").fetchall()
        entities_by_id = {r["canonical_id"]: dict(r) for r in entity_rows}

        db_lance = lancedb.connect("data/vectors.lance")
        tbl = db_lance.open_table("chunks")
        chunks_by_id = {}
        try:
            if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id):
                raise ValueError(f"Unexpected video_id format: {video_id!r}")
            results = (
                tbl.search([0.0] * 384)
                .where(f"video_id = '{video_id}'", prefilter=True)
                .limit(100000)
                .to_list()
            )
            for r in results:
                chunks_by_id[r["chunk_id"]] = r
        except Exception:
            for r in tbl.to_arrow().to_pylist():
                if r["video_id"] == video_id:
                    chunks_by_id[r["chunk_id"]] = r

        extraction_rows = sql_conn.execute(
            "SELECT * FROM raw_extractions WHERE video_id = ?", (video_id,)
        ).fetchall()

        import json

        for ex_row in extraction_rows:
            chunk_id = ex_row["chunk_id"]
            extraction = json.loads(ex_row["extraction_json"])

            chunk_info = chunks_by_id.get(chunk_id, {})
            start = float(chunk_info.get("start", 0.0))
            end_time = float(chunk_info.get("end", 0.0))
            text = chunk_info.get("text", "")

            try:
                kuzu_conn.execute(
                    "MERGE (c:Chunk {chunk_id: $cid}) SET c.video_id = $vid, c.start = $start, c.end_time = $end_time, c.text = $text",
                    {"cid": chunk_id, "vid": video_id, "start": start, "end_time": end_time, "text": text},
                )
            except Exception:
                pass

            for entity in extraction.get("entities", []):
                name = entity.get("name", "")
                etype = entity.get("type", "")
                canonical_id = alias_map.get((name, etype))
                if canonical_id is None:
                    continue

                ent = entities_by_id.get(canonical_id)
                if ent is None:
                    continue

                try:
                    kuzu_conn.execute(
                        "MERGE (e:Entity {canonical_id: $eid}) SET e.name = $name, e.entity_type = $etype, e.entity_desc = $edesc",
                        {
                            "eid": canonical_id,
                            "name": ent["name"] or "",
                            "etype": ent["type"] or "",
                            "edesc": ent.get("description", "") or "",
                        },
                    )
                except Exception:
                    pass

                try:
                    kuzu_conn.execute(
                        "MATCH (c:Chunk {chunk_id: $cid}), (e:Entity {canonical_id: $eid}) MERGE (c)-[:MENTIONS]->(e)",
                        {"cid": chunk_id, "eid": canonical_id},
                    )
                except Exception:
                    pass

                try:
                    kuzu_conn.execute(
                        "MATCH (e:Entity {canonical_id: $eid}), (v:Video {video_id: $vid}) MERGE (e)-[:APPEARS_IN]->(v)",
                        {"eid": canonical_id, "vid": video_id},
                    )
                except Exception:
                    pass

            for relation in extraction.get("relations", []):
                subj_name = relation.get("subject", "")
                obj_name = relation.get("object", "")
                predicate = relation.get("predicate", "")
                evidence = relation.get("evidence", "")

                subj_id = name_to_canonical.get(subj_name)
                obj_id = name_to_canonical.get(obj_name)
                if subj_id is None or obj_id is None:
                    continue

                try:
                    kuzu_conn.execute(
                        "MATCH (s:Entity {canonical_id: $sid}), (o:Entity {canonical_id: $oid}) "
                        "MERGE (s)-[r:RELATED {predicate: $pred}]->(o) "
                        "SET r.evidence = $evidence, r.video_id = $vid",
                        {
                            "sid": subj_id,
                            "oid": obj_id,
                            "pred": predicate,
                            "evidence": evidence,
                            "vid": video_id,
                        },
                    )
                except Exception:
                    pass

        sql_conn.execute(
            "UPDATE videos SET graphed_at = ?, last_error = NULL, error_stage = NULL WHERE video_id = ?",
            (utcnow(), video_id),
        )
        sql_conn.commit()

    except Exception as exc:
        sql_conn.execute(
            "UPDATE videos SET last_error = ?, error_stage = 'graph' WHERE video_id = ?",
            (str(exc), video_id),
        )
        sql_conn.commit()
        raise


def graph() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT * FROM videos WHERE extracted_at IS NOT NULL AND graphed_at IS NULL"
    ).fetchall()
    conn.close()
    for row in rows:
        load_video(row["video_id"])
