import re
from pathlib import Path

import kuzu
import lancedb
from fastmcp import FastMCP
from sentence_transformers import SentenceTransformer

_ROOT = Path(__file__).parent.parent

_model = SentenceTransformer("BAAI/bge-small-en-v1.5")
_lance_db = lancedb.connect(str(_ROOT / "data/vectors.lance"))
# Primary write protection: the database itself is opened read-only, so any
# mutating statement fails at the engine level regardless of how it is phrased.
# The regex below is only a secondary, fail-fast guard for a friendlier message.
_kuzu_db = kuzu.Database(str(_ROOT / "data/graph.kuzu"), read_only=True)

# Allow the read-only statement starters Kuzu supports. WITH is now included to
# match the error message and permit multi-stage read pipelines.
_READ_PREFIX = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN|CALL)\b", re.IGNORECASE)

mcp = FastMCP("gym-knowledge-repository")


@mcp.tool()
def vector_search(query: str, limit: int = 5) -> list[dict]:
    vector = _model.encode([query]).tolist()[0]
    table = _lance_db.open_table("chunks")
    rows = table.search(vector).limit(limit).to_list()
    results = []
    for row in rows:
        results.append({
            "chunk_id": row["chunk_id"],
            "video_id": row["video_id"],
            "start": row["start"],
            "end": row["end"],
            "text": row["text"],
            "url": f"https://youtu.be/{row['video_id']}?t={int(row['start'])}",
        })
    return results


@mcp.tool()
def cypher_query(query: str) -> list[dict] | str:
    # Engine-level read_only=True is the real guard; this just gives a clear
    # message instead of a raw Kuzu error for obviously-mutating queries.
    if not _READ_PREFIX.match(query):
        return "Error: only read queries (MATCH, OPTIONAL MATCH, WITH, UNWIND, RETURN, CALL) are permitted."
    try:
        conn = kuzu.Connection(_kuzu_db)
        result = conn.execute(query)
        columns = result.get_column_names()
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append(dict(zip(columns, row)))
        return rows
    except Exception as exc:
        return str(exc)


@mcp.tool()
def expand_entity(name: str, depth: int = 1) -> list[dict]:
    conn = kuzu.Connection(_kuzu_db)
    if depth <= 1:
        cypher = (
            "MATCH (e:Entity {name: $name})-[r:RELATED]->(e2:Entity) "
            "RETURN e2.name AS name, e2.entity_type AS type, r.predicate AS predicate, r.evidence AS evidence"
        )
        try:
            result = conn.execute(cypher, {"name": name})
        except Exception:
            return []
        rows = []
        while result.has_next():
            row = result.get_next()
            rows.append({"name": row[0], "type": row[1], "predicate": row[2], "evidence": row[3]})
        return rows
    else:
        # Kuzu variable-length rels return list objects for r; iterate depth=1 hops manually.
        visited = set()
        frontier = [name]
        all_rows = []
        for _ in range(depth):
            next_frontier = []
            for entity_name in frontier:
                cypher = (
                    "MATCH (e:Entity {name: $name})-[r:RELATED]->(e2:Entity) "
                    "RETURN e2.name AS name, e2.entity_type AS type, r.predicate AS predicate, r.evidence AS evidence"
                )
                try:
                    result = conn.execute(cypher, {"name": entity_name})
                except Exception:
                    continue
                while result.has_next():
                    row = result.get_next()
                    neighbor = row[0]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
                        all_rows.append({
                            "name": neighbor,
                            "type": row[1],
                            "predicate": row[2],
                            "evidence": row[3],
                        })
            frontier = next_frontier
            if not frontier:
                break
        return all_rows


@mcp.tool()
def papers_for_topic(topic: str) -> list[dict]:
    vector = _model.encode([topic]).tolist()[0]
    table = _lance_db.open_table("chunks")
    rows = table.search(vector).limit(5).to_list()
    chunk_ids = [row["chunk_id"] for row in rows]
    if not chunk_ids:
        return []

    cypher = (
        "MATCH (c:Chunk)-[:REFERENCES]->(p:Paper) "
        "WHERE c.chunk_id IN $chunk_ids "
        "RETURN DISTINCT p.doi AS doi, p.title AS title, p.authors AS authors, p.year AS year"
    )
    try:
        conn = kuzu.Connection(_kuzu_db)
        result = conn.execute(cypher, {"chunk_ids": chunk_ids})
        papers = []
        while result.has_next():
            row = result.get_next()
            papers.append({"doi": row[0], "title": row[1], "authors": row[2], "year": row[3]})
        return papers
    except Exception:
        return []


if __name__ == "__main__":
    mcp.run()
