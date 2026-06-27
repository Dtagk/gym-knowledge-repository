"""Independent literature discovery: seed from KG entities → Semantic Scholar."""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import kuzu

from .db import init_db

logger = logging.getLogger(__name__)

_SS_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "paperId,title,abstract,year,citationCount,authors,venue,externalIds"
_KUZU_PATH = "data/graph.kuzu"

# Hardcoded exercise-science seeds that may not appear as KG entity names
_BASE_SEEDS = [
    "muscle hypertrophy resistance training",
    "progressive overload strength",
    "squat biomechanics",
    "deadlift technique",
    "bench press pectoralis",
    "posterior chain hip hinge",
    "protein synthesis muscle",
    "range of motion exercise",
    "RPE training intensity",
    "periodization strength training",
]


def _top_entity_seeds(n: int = 30) -> list[str]:
    try:
        db = kuzu.Database(_KUZU_PATH, read_only=True)
        conn = kuzu.Connection(db)
        result = conn.execute(
            "MATCH (e:Entity)-[:APPEARS_IN]->(v:Video) "
            "RETURN e.name, count(v) AS cnt ORDER BY cnt DESC LIMIT $n",
            {"n": n},
        )
        names = []
        while result.has_next():
            row = result.get_next()
            name = row[0]
            # Skip very short / generic names unlikely to yield good SS results
            if name and len(name) > 3 and name.lower() not in {"gym", "a-side joint"}:
                names.append(name)
        return names
    except Exception as e:
        logger.warning("lit_discover: kuzu query failed — %s", e)
        return []


def _search(query: str, limit: int = 10) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "limit": limit, "fields": _FIELDS})
    req = urllib.request.Request(
        f"{_SS_URL}?{params}",
        headers={"User-Agent": "gym-knowledge-graph/1.0 (research; contact: dim.tagkoulis@gmail.com)"},
    )
    for attempt, backoff in enumerate([0, 60, 120]):
        try:
            if backoff:
                logger.info("lit_discover: retry %d for %r after %ds", attempt, query, backoff)
                time.sleep(backoff)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning("lit_discover: rate limited on %r (attempt %d)", query, attempt + 1)
                continue
            logger.warning("lit_discover: HTTP %s for %r", e.code, query)
            return []
        except Exception as e:
            logger.warning("lit_discover: request failed for %r — %s", query, e)
            return []
    logger.warning("lit_discover: giving up on %r after 3 attempts", query)
    return []


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS literature (
            doi            TEXT PRIMARY KEY,
            ss_id          TEXT,
            title          TEXT,
            abstract       TEXT,
            year           INTEGER,
            citation_count INTEGER,
            authors        TEXT,
            venue          TEXT,
            seed_entity    TEXT,
            discovered_at  TEXT
        )
    """)
    conn.commit()


def lit_discover(max_per_seed: int = 10, top_n_entities: int = 30) -> None:
    conn = init_db()
    _ensure_table(conn)

    entity_seeds = _top_entity_seeds(top_n_entities)
    seeds = entity_seeds + _BASE_SEEDS
    logger.info("lit_discover: %d seeds (%d from KG, %d base)", len(seeds), len(entity_seeds), len(_BASE_SEEDS))

    added = 0
    for seed in seeds:
        papers = _search(seed, limit=max_per_seed)
        for p in papers:
            ext = p.get("externalIds") or {}
            doi = (ext.get("DOI") or ext.get("doi") or "").lower().strip()
            if not doi:
                continue
            if conn.execute("SELECT 1 FROM literature WHERE doi=?", (doi,)).fetchone():
                continue
            authors = json.dumps([a.get("name", "") for a in (p.get("authors") or [])])
            conn.execute(
                "INSERT INTO literature "
                "(doi, ss_id, title, abstract, year, citation_count, authors, venue, seed_entity, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doi,
                    p.get("paperId"),
                    p.get("title"),
                    p.get("abstract"),
                    p.get("year"),
                    p.get("citationCount", 0),
                    authors,
                    p.get("venue"),
                    seed,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            added += 1
        conn.commit()
        time.sleep(4.0)  # ponytail: SS unauthenticated is ~100 req/5min ≈ 1/3s; 4s gives headroom

    logger.info("lit_discover: added %d papers across %d seeds", added, len(seeds))
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lit_discover()
