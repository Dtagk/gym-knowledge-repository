import json
import uuid
import logging
from pathlib import Path

import numpy as np
import requests
import yaml

from yt_kg.db import init_db

logger = logging.getLogger(__name__)

OVERRIDES_PATH = Path("config/entity_overrides.yaml")
OLLAMA_MODEL = "qwen2.5-coder:7b"
SIMILARITY_THRESHOLD = 0.85


def _init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            canonical_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS entity_aliases (
            alias TEXT NOT NULL,
            type TEXT NOT NULL,
            canonical_id TEXT NOT NULL REFERENCES entities(canonical_id),
            PRIMARY KEY (alias, type)
        );
    """)
    conn.commit()


def _cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def _llm_verify(name_a, name_b, entity_type):
    prompt = (
        f"Do '{name_a}' and '{name_b}' (type: {entity_type}) refer to the same "
        f"real-world entity? Reply with only 'yes' or 'no'."
    )
    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=30,
        )
        text = resp.json().get("response", "").strip().lower()
        return text.startswith("yes")
    except Exception:
        return False


def _load_overrides():
    if not OVERRIDES_PATH.exists():
        return []
    with open(OVERRIDES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return (data or {}).get("merges", []) or []


def _upsert_entity(conn, canonical_id, name, entity_type, description):
    conn.execute(
        "INSERT OR IGNORE INTO entities (canonical_id, name, type, description) VALUES (?, ?, ?, ?)",
        (canonical_id, name, entity_type, description),
    )


def _upsert_alias(conn, alias, entity_type, canonical_id):
    conn.execute(
        "INSERT OR IGNORE INTO entity_aliases (alias, type, canonical_id) VALUES (?, ?, ?)",
        (alias, entity_type, canonical_id),
    )


def _find(merged_into: dict, x: int) -> int:
    depth = 0
    while x in merged_into:
        parent = merged_into[x]
        if parent in merged_into:
            merged_into[x] = merged_into[parent]  # path compression
        x = merged_into[x]
        depth += 1
        if depth > 10000:
            raise RuntimeError(f"Union-Find cycle detected at node {x}")
    return x


def resolve():
    conn = init_db()
    _init_tables(conn)

    overrides = _load_overrides()

    rows = conn.execute("SELECT video_id, extraction_json FROM raw_extractions").fetchall()

    seen = {}
    for row in rows:
        try:
            data = json.loads(row["extraction_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        entities = data if isinstance(data, list) else data.get("entities", [])
        for ent in entities:
            name = (ent.get("name") or "").strip()
            etype = (ent.get("type") or "").strip()
            desc = (ent.get("description") or "").strip()
            if not name or not etype:
                continue
            key = (name, etype)
            if key not in seen:
                seen[key] = {"name": name, "type": etype, "description": desc}

    for merge in overrides:
        alias = (merge.get("alias") or "").strip()
        canonical_name = (merge.get("canonical") or "").strip()
        etype = (merge.get("type") or "").strip()
        if not alias or not canonical_name or not etype:
            continue

        existing = conn.execute(
            "SELECT canonical_id FROM entities WHERE name = ? AND type = ?",
            (canonical_name, etype),
        ).fetchone()

        if existing:
            canonical_id = existing["canonical_id"]
        else:
            canonical_id = str(uuid.uuid4())
            desc = seen.get((canonical_name, etype), {}).get("description", "")
            _upsert_entity(conn, canonical_id, canonical_name, etype, desc)

        _upsert_alias(conn, alias, etype, canonical_id)
        _upsert_alias(conn, canonical_name, etype, canonical_id)

    conn.commit()

    already_aliased = set(
        (r["alias"], r["type"])
        for r in conn.execute("SELECT alias, type FROM entity_aliases").fetchall()
    )

    by_type = {}
    for key, ent in seen.items():
        if key in already_aliased:
            continue
        etype = ent["type"]
        by_type.setdefault(etype, []).append(ent)

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")

    total_entities = 0
    total_merges = 0

    for etype, group in by_type.items():
        if len(group) <= 1:
            for ent in group:
                cid = str(uuid.uuid4())
                _upsert_entity(conn, cid, ent["name"], etype, ent["description"])
                _upsert_alias(conn, ent["name"], etype, cid)
                total_entities += 1
            conn.commit()
            continue

        texts = [f"{e['name']} {e['description']}" for e in group]
        embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)

        merged_into = {}

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                sim = _cosine(embeddings[i], embeddings[j])
                if sim > SIMILARITY_THRESHOLD:
                    if _llm_verify(group[i]["name"], group[j]["name"], etype):
                        root_i = _find(merged_into, i)
                        root_j = _find(merged_into, j)
                        if root_i != root_j:
                            if group[root_i]["name"] <= group[root_j]["name"]:
                                merged_into[root_j] = root_i
                            else:
                                merged_into[root_i] = root_j
                            total_merges += 1

        canonical_ids = {}
        for idx, ent in enumerate(group):
            root = _find(merged_into, idx)
            if root not in canonical_ids:
                cid = str(uuid.uuid4())
                canonical_ids[root] = cid
                _upsert_entity(conn, cid, group[root]["name"], etype, group[root]["description"])
                total_entities += 1
            _upsert_alias(conn, ent["name"], etype, canonical_ids[root])

        conn.commit()

    logger.info("resolve: %d canonical entities, %d merges applied", total_entities, total_merges)
    conn.close()


if __name__ == "__main__":
    resolve()
