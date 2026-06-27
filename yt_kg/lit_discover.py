"""Independent literature discovery: seed from KG entities → PubMed E-utilities."""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import truststore
    truststore.inject_into_ssl()  # use OS cert store — handles antivirus MITM certs
except ImportError:
    pass

import kuzu

from .db import init_db

logger = logging.getLogger(__name__)

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_KUZU_PATH = "data/graph.kuzu"
_UA = {"User-Agent": "gym-knowledge-graph/1.0 (research; contact: dim.tagkoulis@gmail.com)"}

# Hardcoded exercise-science seeds that may not appear as KG entity names
_BASE_SEEDS = [
    "muscle hypertrophy resistance training",
    "progressive overload strength training",
    "squat biomechanics",
    "deadlift technique spine",
    "bench press pectoralis major",
    "posterior chain hip hinge",
    "protein synthesis skeletal muscle",
    "range of motion exercise performance",
    "RPE rating perceived exertion training",
    "periodization strength hypertrophy",
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
            if name and len(name) > 3 and name.lower() not in {"gym", "a-side joint", "research", "new research"}:
                names.append(name)
        return names
    except Exception as e:
        logger.warning("lit_discover: kuzu query failed — %s", e)
        return []


def _fetch(url: str) -> dict | None:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning("lit_discover: PubMed rate limited — sleeping 10s")
            time.sleep(10)
        else:
            logger.warning("lit_discover: HTTP %s for %s", e.code, url[:80])
        return None
    except Exception as e:
        logger.warning("lit_discover: request failed — %s", e)
        return None


def _search_pubmed(query: str, limit: int = 10) -> list[dict]:
    """Return list of paper dicts with doi, title, abstract, year, authors, venue."""
    # Step 1: get PMIDs
    params = urllib.parse.urlencode({
        "db": "pubmed", "term": query, "retmax": limit,
        "retmode": "json", "sort": "relevance",
    })
    data = _fetch(f"{_ESEARCH}?{params}")
    if not data:
        return []
    pmids = data.get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    time.sleep(0.4)  # brief pause between search and fetch

    # Step 2: fetch summaries
    params2 = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(pmids), "retmode": "json"})
    summary = _fetch(f"{_ESUMMARY}?{params2}")
    if not summary:
        return []

    papers = []
    for uid, art in summary.get("result", {}).items():
        if uid == "uids":
            continue
        # Extract DOI from articleids list
        doi = ""
        for aid in art.get("articleids", []):
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "").lower().strip()
                break
        if not doi:
            continue
        authors = json.dumps([a.get("name", "") for a in art.get("authors", [])])
        papers.append({
            "doi": doi,
            "pmid": uid,
            "title": art.get("title", "").rstrip("."),
            "abstract": "",  # esummary doesn't include abstract; acceptable for now
            "year": int(art.get("pubdate", "0")[:4] or 0),
            "citation_count": 0,
            "authors": authors,
            "venue": art.get("source", ""),
        })
    return papers


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
        papers = _search_pubmed(seed, limit=max_per_seed)
        for p in papers:
            doi = p["doi"]
            if conn.execute("SELECT 1 FROM literature WHERE doi=?", (doi,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO literature "
                "(doi, ss_id, title, abstract, year, citation_count, authors, venue, seed_entity, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doi, p["pmid"], p["title"], p["abstract"],
                    p["year"], p["citation_count"], p["authors"],
                    p["venue"], seed,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            added += 1
        conn.commit()
        time.sleep(0.4)  # ponytail: PubMed allows 3 req/sec unauthenticated; 0.4s is safe

    logger.info("lit_discover: added %d papers across %d seeds", added, len(seeds))
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lit_discover()
