"""Proactive paper discovery from OpenAlex, PubMed, and Semantic Scholar.

Searches configured queries (and optionally KG entity names) against academic
APIs, deduplicates by DOI, and stores results in the discovered_papers table.
Run before enrich_papers to grow the corpus beyond citation-following.

    python -m yt_kg.discover_papers
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

from yt_kg.db import init_db, utcnow

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "papers.yaml"
# polite pool: include mailto so OpenAlex/S2 prioritise our requests
HEADERS = {"User-Agent": "gym-kg/1.0 (mailto:dim.tagkoulis@gmail.com)"}


# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _entity_queries(limit: int, results_per: int) -> list[dict]:
    """Pull top entity names from the KG and return them as supplemental queries."""
    try:
        import kuzu  # lazy — not available in CI test environment
        db = kuzu.Database("data/graph.kuzu", read_only=True)
        conn = kuzu.Connection(db)
        res = conn.execute(f"MATCH (e:Entity) RETURN DISTINCT e.name LIMIT {limit}")
        names = [row[0] for row in res.get_as_pl().rows() if row[0]]
        logger.info("Augmenting with %d KG entity queries", len(names))
        return [{"query": n, "sources": ["openalex"], "limit": results_per} for n in names]
    except Exception:
        logger.debug("KG entity augmentation skipped (no graph yet)")
        return []


# ── DB ────────────────────────────────────────────────────────────────────────

def _init_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_papers (
            doi        TEXT PRIMARY KEY,
            title      TEXT,
            authors    TEXT,
            year       INTEGER,
            abstract   TEXT,
            oa_url     TEXT,
            source     TEXT,
            query      TEXT,
            fetched_at TEXT
        )
    """)
    conn.commit()


# ── Source adapters ───────────────────────────────────────────────────────────

def _search_openalex(query: str, limit: int) -> list[dict]:
    url = (
        f"https://api.openalex.org/works"
        f"?search={quote(query)}&per-page={min(limit, 50)}"
        f"&mailto=dim.tagkoulis@gmail.com"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        works = resp.json().get("results", [])
    except Exception:
        logger.exception("OpenAlex search failed: %r", query)
        return []

    out = []
    for w in works:
        doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        if not doi:
            continue
        oa = w.get("open_access") or {}
        authors = ", ".join(
            a.get("author", {}).get("display_name", "")
            for a in w.get("authorships", [])[:5]
            if a.get("author", {}).get("display_name")
        )
        # reconstruct abstract from inverted index
        aii = w.get("abstract_inverted_index") or {}
        abstract = ""
        if aii:
            words: dict[int, str] = {}
            for word, positions in aii.items():
                for pos in positions:
                    words[pos] = word
            abstract = " ".join(words[i] for i in sorted(words))
        out.append({
            "doi": doi, "title": w.get("title", ""), "authors": authors,
            "year": w.get("publication_year"), "abstract": abstract,
            "oa_url": oa.get("oa_url"), "source": "openalex",
        })
    return out


def _search_pubmed(query: str, limit: int) -> list[dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    try:
        r = requests.get(
            f"{base}/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": limit, "retmode": "json"},
            headers=HEADERS, timeout=20,
        )
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
    except Exception:
        logger.exception("PubMed search failed: %r", query)
        return []

    if not ids:
        return []

    time.sleep(0.35)  # NCBI: 3 req/s without API key
    try:
        import defusedxml.ElementTree as ET  # safe against XXE / billion-laughs
        r2 = requests.get(
            f"{base}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "rettype": "abstract"},
            headers=HEADERS, timeout=30,
        )
        r2.raise_for_status()
        root = ET.fromstring(r2.content)
    except Exception:
        logger.exception("PubMed fetch failed for ids %s", ids)
        return []

    out = []
    for article in root.findall(".//PubmedArticle"):
        try:
            doi = next(
                (el.text.strip() for el in article.findall(".//ArticleId")
                 if el.get("IdType") == "doi" and el.text),
                None,
            )
            if not doi:
                continue
            title = article.findtext(".//ArticleTitle") or ""
            year_el = article.find(".//PubDate/Year")
            year = int(year_el.text) if year_el is not None and year_el.text else None
            authors = ", ".join(
                f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip()
                for a in article.findall(".//Author")[:5]
                if a.findtext("LastName")
            )
            abstract = " ".join(t.text for t in article.findall(".//AbstractText") if t.text)
            out.append({
                "doi": doi, "title": title, "authors": authors,
                "year": year, "abstract": abstract,
                "oa_url": None, "source": "pubmed",
            })
        except Exception:
            logger.exception("Error parsing PubMed article")
    return out


def _search_semantic_scholar(query: str, limit: int) -> list[dict]:
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query, "limit": min(limit, 100),
                "fields": "title,authors,year,abstract,externalIds,openAccessPdf",
            },
            headers=HEADERS, timeout=20,
        )
        resp.raise_for_status()
        papers = resp.json().get("data", [])
    except Exception:
        logger.exception("Semantic Scholar search failed: %r", query)
        return []

    out = []
    for p in papers:
        doi = (p.get("externalIds") or {}).get("DOI")
        if not doi:
            continue
        oa_pdf = p.get("openAccessPdf") or {}
        out.append({
            "doi": doi, "title": p.get("title", ""),
            "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:5]),
            "year": p.get("year"), "abstract": p.get("abstract") or "",
            "oa_url": oa_pdf.get("url"), "source": "semantic_scholar",
        })
    return out


_ADAPTERS = {
    "openalex": _search_openalex,
    "pubmed": _search_pubmed,
    "semantic_scholar": _search_semantic_scholar,
}


# ── Main ──────────────────────────────────────────────────────────────────────

def discover_papers() -> None:
    cfg = _load_config()
    searches: list[dict] = list(cfg.get("paper_searches") or [])

    if cfg.get("include_graph_entities"):
        searches += _entity_queries(
            limit=int(cfg.get("entity_limit", 50)),
            results_per=int(cfg.get("entity_results", 5)),
        )

    if not searches:
        logger.info("No paper searches configured — nothing to do")
        return

    conn = init_db()
    _init_table(conn)
    now = utcnow()
    total = 0

    for entry in searches:
        query = (entry.get("query") or "").strip()
        if not query:
            continue
        limit = int(entry.get("limit", 20))
        sources = entry.get("sources") or ["openalex"]

        candidates: list[dict] = []
        for src in sources:
            fn = _ADAPTERS.get(src)
            if fn:
                candidates += fn(query, limit)
            else:
                logger.warning("Unknown source %r — skipping", src)

        inserted = 0
        for paper in candidates:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered_papers "
                    "(doi, title, authors, year, abstract, oa_url, source, query, fetched_at) "
                    "VALUES (:doi,:title,:authors,:year,:abstract,:oa_url,:source,:query,:now)",
                    {**paper, "query": query, "now": now},
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                logger.exception("Failed to insert %s", paper.get("doi"))
        conn.commit()
        logger.info("Query %r: %d new papers", query, inserted)
        total += inserted

    conn.close()
    logger.info("Paper discovery complete — %d new papers total", total)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    discover_papers()
