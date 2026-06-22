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

from yt_kg.db import init_db

_ROOT = Path(__file__).parent.parent
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
# /ask
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    query: str
    limit: int = 5


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
