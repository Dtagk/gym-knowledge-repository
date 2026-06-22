"""RAG server: FastAPI app exposing /health and /ask endpoints."""
from pathlib import Path

import httpx
import kuzu
import lancedb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

import os

from yt_kg.db import init_db

_ROOT = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent))
_LANCE_PATH = str(_ROOT / "data/vectors.lance")
_KUZU_PATH = str(_ROOT / "data/graph.kuzu")
_OLLAMA_URL = "http://localhost:11434"
_OLLAMA_MODEL = "gpt-oss:20b"

app = FastAPI(title="Gym Knowledge RAG Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy singletons — loaded once on first request.
_model: SentenceTransformer | None = None
_lance_db = None
_kuzu_db: kuzu.Database | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    return _model


def _get_lance():
    global _lance_db
    if _lance_db is None:
        _lance_db = lancedb.connect(_LANCE_PATH)
    return _lance_db


def _get_kuzu() -> kuzu.Database:
    global _kuzu_db
    if _kuzu_db is None:
        _kuzu_db = kuzu.Database(_KUZU_PATH, read_only=True)
    return _kuzu_db


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    ollama_ok = False
    lance_ok = False

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{_OLLAMA_URL}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass

    try:
        db = _get_lance()
        lance_ok = "chunks" in db.table_names()
    except Exception:
        pass

    return {"status": "ok", "ollama": ollama_ok, "lancedb": lance_ok}


# ---------------------------------------------------------------------------
# /search — semantic ANN search, no LLM call
# ---------------------------------------------------------------------------

@app.get("/search")
async def search(q: str, limit: int = 10):
    if not q.strip():
        return []
    model = _get_model()
    query_vector = model.encode([q])[0].tolist()
    db = _get_lance()
    table = db.open_table("chunks")
    rows = table.search(query_vector).limit(limit * 3).to_list()  # oversample, then dedup by video

    # Group by video_id, keep best score per video
    best: dict[str, dict] = {}
    for row in rows:
        vid = row["video_id"]
        if vid not in best or row["_distance"] < best[vid]["_distance"]:
            best[vid] = row

    # Sort by score ascending (_distance = cosine distance, lower = better)
    ranked = sorted(best.values(), key=lambda r: r["_distance"])[:limit]

    # Look up titles from SQLite
    sql_conn = init_db()
    results = []
    for row in ranked:
        vid = row["video_id"]
        db_row = sql_conn.execute("SELECT title FROM videos WHERE video_id = ?", (vid,)).fetchone()
        title = db_row["title"] if db_row else vid
        score = round(1.0 - float(row["_distance"]), 4)  # convert distance to similarity
        results.append({
            "video_id": vid,
            "title": title,
            "score": score,
            "excerpt": row["text"][:300],
            "url": f"https://youtu.be/{vid}",
        })
    return results


# ---------------------------------------------------------------------------
# /ask
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    query: str
    limit: int = 5
    graph_expand: bool = True  # pull in chunks via graph neighbors, not just ANN


@app.post("/ask")
async def ask(req: AskRequest):
    # 1. Embed query
    model = _get_model()
    query_vector = model.encode([req.query])[0].tolist()

    # 2. LanceDB ANN search
    db = _get_lance()
    table = db.open_table("chunks")
    lance_rows = table.search(query_vector).limit(req.limit).to_list()

    # 3. Kuzu enrichment
    kuzu_conn = kuzu.Connection(_get_kuzu())
    entities_by_id: dict[str, dict] = {}

    for row in lance_rows:
        chunk_id = row["chunk_id"]
        try:
            res = kuzu_conn.execute(
                "MATCH (c:Chunk {chunk_id: $cid})-[:MENTIONS]->(e:Entity)-[:RELATED]->(e2:Entity) "
                "RETURN e.canonical_id, e.name, e.entity_type, e.entity_desc, e2.name, e2.entity_type",
                {"cid": chunk_id},
            )
            while res.has_next():
                r = res.get_next()
                cid_val, e_name, e_type, e_desc, e2_name, e2_type = r
                if cid_val not in entities_by_id:
                    entities_by_id[cid_val] = {
                        "canonical_id": cid_val,
                        "name": e_name,
                        "type": e_type,
                        "evidence": e_desc or "",
                        "related": [],
                    }
                entities_by_id[cid_val]["related"].append(
                    {"name": e2_name, "type": e2_type}
                )
        except Exception:
            pass

    # 3b. Graph-guided recall (graph-native retrieval): the enrichment above
    # surfaced RELATED neighbor entities. Find additional chunks that MENTION
    # those neighbors — videos vector search alone may have ranked just below
    # the cutoff but that the graph says are topically connected. These are
    # appended (deduped) so the LLM sees a graph-expanded context, not only the
    # ANN top-k.
    seen_chunk_ids = {r["chunk_id"] for r in lance_rows}
    if req.graph_expand and entities_by_id:
        neighbor_names = {
            rel["name"]
            for e in entities_by_id.values()
            for rel in e["related"]
            if rel.get("name")
        }
        # cap fan-out so a hub entity can't flood the context
        for name in list(neighbor_names)[:10]:
            try:
                res = kuzu_conn.execute(
                    "MATCH (e:Entity {name: $name})<-[:MENTIONS]-(c:Chunk) "
                    "RETURN c.chunk_id, c.video_id, c.start, c.text LIMIT 3",
                    {"name": name},
                )
            except Exception:
                continue
            while res.has_next():
                cid, vid, start, text = res.get_next()
                if cid in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(cid)
                lance_rows.append(
                    {"chunk_id": cid, "video_id": vid, "start": start or 0.0, "text": text or ""}
                )

    # 4. Build Ollama prompt
    chunk_excerpts = "\n\n".join(
        f"[{r['video_id']} @{int(r['start'])}s] {r['text'][:500]}" for r in lance_rows
    )
    entity_lines = "\n".join(
        f"- {e['name']} ({e['type']}): {e['evidence']}" for e in entities_by_id.values()
    )

    system_prompt = (
        "You are an expert fitness coach and sports scientist. "
        "Answer the user's question based on the YouTube video excerpts and knowledge-graph entities provided. "
        "Be specific and cite timestamps where relevant. "
        "If the context does not contain enough information, say so honestly."
    )
    context_block = f"VIDEO EXCERPTS:\n{chunk_excerpts}"
    if entity_lines:
        context_block += f"\n\nKNOWLEDGE GRAPH ENTITIES:\n{entity_lines}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context_block}\n\nQUESTION: {req.query}"},
    ]

    # 5. Call Ollama
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_OLLAMA_URL}/api/chat",
                json={"model": _OLLAMA_MODEL, "messages": messages, "stream": False},
            )
        data = resp.json()
        answer = data["message"]["content"]
    except (httpx.ConnectError, httpx.TimeoutException):
        return JSONResponse(
            status_code=503,
            content={"error": f"Ollama unreachable at {_OLLAMA_URL}"},
        )

    # 6. Assemble sources (one per unique video)
    sql_conn = init_db()
    seen_videos: set[str] = set()
    sources = []
    for row in lance_rows:
        vid = row["video_id"]
        if vid in seen_videos:
            continue
        seen_videos.add(vid)
        db_row = sql_conn.execute(
            "SELECT title FROM videos WHERE video_id = ?", (vid,)
        ).fetchone()
        title = db_row["title"] if db_row else vid
        sources.append(
            {
                "video_id": vid,
                "title": title,
                "url": f"https://youtu.be/{vid}?t={int(row['start'])}",
                "excerpt": row["text"][:300],
            }
        )

    # 7. Assemble entities response list
    entities_out = [
        {
            "name": e["name"],
            "type": e["type"],
            "evidence": e["evidence"],
        }
        for e in entities_by_id.values()
    ]

    return {"answer": answer, "sources": sources, "entities": entities_out}


# ---------------------------------------------------------------------------
# /technique — timestamped coaching cues for an exercise
# ---------------------------------------------------------------------------

@app.get("/technique")
async def technique(exercise: str, kind: str | None = None, limit: int = 50):
    """Return technique cues for an exercise, each with a timestamped deep link.

    `exercise` is resolved through the alias table so 'lat raise', 'side raise',
    etc. all map to the canonical entity. `kind` optionally filters to one of:
    mistake, cue, setup, tempo, breathing, range-of-motion.
    """
    exercise = exercise.strip()
    if not exercise:
        return []

    # Resolve the search term to canonical entity name(s) via the alias table.
    sql_conn = init_db()
    alias_rows = sql_conn.execute(
        "SELECT e.name FROM entity_aliases a "
        "JOIN entities e ON e.canonical_id = a.canonical_id "
        "WHERE LOWER(a.alias) = LOWER(?) AND a.type = 'Method'",
        (exercise,),
    ).fetchall()
    canonical_names = {r["name"] for r in alias_rows} or {exercise}

    kuzu_conn = kuzu.Connection(_get_kuzu())
    results: list[dict] = []
    for name in canonical_names:
        cypher = (
            "MATCH (e:Entity {name: $name})-[h:HAS_TECHNIQUE]->(t:TechniqueCue) "
            + ("WHERE t.kind = $kind " if kind else "")
            + "RETURN t.text, t.kind, h.video_id, h.start ORDER BY h.start"
        )
        params = {"name": name}
        if kind:
            params["kind"] = kind
        try:
            res = kuzu_conn.execute(cypher, params)
        except Exception:
            continue
        while res.has_next():
            text, ckind, vid, start = res.get_next()
            results.append(
                {
                    "exercise": name,
                    "cue": text,
                    "kind": ckind,
                    "video_id": vid,
                    "start": int(start or 0),
                    "url": f"https://youtu.be/{vid}?t={int(start or 0)}",
                }
            )

    # Title lookup + dedup identical cues, keep earliest timestamp.
    seen: dict[tuple, dict] = {}
    for r in results:
        key = (r["cue"], r["video_id"])
        if key not in seen or r["start"] < seen[key]["start"]:
            seen[key] = r
    out = sorted(seen.values(), key=lambda r: (r["video_id"], r["start"]))[:limit]
    for r in out:
        row = sql_conn.execute(
            "SELECT title FROM videos WHERE video_id = ?", (r["video_id"],)
        ).fetchone()
        r["title"] = row["title"] if row else r["video_id"]
    return out


# ---------------------------------------------------------------------------
# /related — pure graph traversals (no LLM, no vector search)
# ---------------------------------------------------------------------------

@app.get("/related/co-cited")
async def co_cited(video_id: str, limit: int = 10):
    """Videos that REFERENCE at least one paper in common with the given video."""
    conn = kuzu.Connection(_get_kuzu())
    try:
        res = conn.execute(
            "MATCH (c1:Chunk {video_id: $vid})-[:REFERENCES]->(p:Paper)<-[:REFERENCES]-(c2:Chunk) "
            "WHERE c2.video_id <> $vid "
            "RETURN c2.video_id AS vid, count(DISTINCT p.doi) AS shared "
            "ORDER BY shared DESC LIMIT $lim",
            {"vid": video_id, "lim": limit},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    sql_conn = init_db()
    out = []
    while res.has_next():
        vid, shared = res.get_next()
        row = sql_conn.execute("SELECT title FROM videos WHERE video_id = ?", (vid,)).fetchone()
        out.append({
            "video_id": vid,
            "title": row["title"] if row else vid,
            "shared_papers": int(shared),
            "url": f"https://youtu.be/{vid}",
        })
    return out


@app.get("/related/exercises")
async def related_exercises(entity: str, limit: int = 15):
    """Entities connected to the given one via RELATED (either direction)."""
    conn = kuzu.Connection(_get_kuzu())
    try:
        res = conn.execute(
            "MATCH (e:Entity {name: $name})-[r:RELATED]-(e2:Entity) "
            "RETURN DISTINCT e2.name AS name, e2.entity_type AS type, r.predicate AS predicate "
            "LIMIT $lim",
            {"name": entity, "lim": limit},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    out = []
    while res.has_next():
        name, etype, predicate = res.get_next()
        out.append({"name": name, "type": etype, "predicate": predicate})
    return out
