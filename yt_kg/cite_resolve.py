import logging
import re
from urllib.parse import quote

import requests

from yt_kg.db import init_db, utcnow
from yt_kg.graph import _init_graph

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "gym-kg-pipeline/1.0"}


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resolved_citations (
            citation_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            doi TEXT,
            title TEXT,
            authors TEXT,
            year INTEGER,
            oa_url TEXT,
            resolved_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _parse_work(work: dict) -> dict:
    doi = work.get("doi")
    title = work.get("title", "")
    authors = ", ".join(
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])
        if a.get("author", {}).get("display_name")
    )
    year = work.get("publication_year")
    oa = work.get("open_access", {})
    oa_url = oa.get("oa_url") if isinstance(oa, dict) else None
    return {"doi": doi, "title": title, "authors": authors, "year": year, "oa_url": oa_url}


def _resolve_openalex(raw_ref: str) -> dict | None:
    if raw_ref.startswith("10."):
        raw_ref = re.sub(r'/[a-zA-Z]+$', '', raw_ref)
    if raw_ref.startswith("10."):
        url = f"https://api.openalex.org/works/doi:{raw_ref}"
    elif re.match(r'(?i)arxiv:', raw_ref):
        arxiv_id = re.sub(r'(?i)^arxiv:', '', raw_ref).strip()
        url = f"https://api.openalex.org/works/arxiv:{arxiv_id}"
    else:
        url = f"https://api.openalex.org/works?search={quote(raw_ref)}&per-page=1"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None
        data = resp.json()
        # search response has "results"; direct lookup returns bare Work object
        if "results" in data:
            if not data["results"]:
                return None
            return _parse_work(data["results"][0])
        return _parse_work(data)
    except Exception:
        return None


def _resolve_semantic_scholar(raw_ref: str) -> dict | None:
    url = f"https://api.semanticscholar.org/graph/v1/paper/{quote(raw_ref)}?fields=title,authors,year,openAccessPdf"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
        authors = ", ".join(a.get("name", "") for a in data.get("authors", []) if a.get("name"))
        oa_pdf = data.get("openAccessPdf")
        oa_url = oa_pdf.get("url") if isinstance(oa_pdf, dict) else None
        return {
            "doi": None,
            "title": data.get("title", ""),
            "authors": authors,
            "year": data.get("year"),
            "oa_url": oa_url,
        }
    except Exception:
        return None


def resolve_citations(video_id: str) -> None:
    conn = init_db()
    _init_table(conn)

    rows = conn.execute(
        "SELECT citation_id, raw_ref FROM raw_citations WHERE video_id=? "
        "AND citation_id NOT IN (SELECT citation_id FROM resolved_citations)",
        (video_id,),
    ).fetchall()

    for row in rows:
        cid, ref = row["citation_id"], row["raw_ref"]
        meta = _resolve_openalex(ref) or _resolve_semantic_scholar(ref)
        if meta:
            conn.execute(
                "INSERT OR IGNORE INTO resolved_citations "
                "(citation_id, video_id, doi, title, authors, year, oa_url, resolved_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, video_id, meta["doi"], meta["title"], meta["authors"], meta["year"], meta["oa_url"], utcnow()),
            )
            conn.commit()
        else:
            logger.warning("Could not resolve citation %s (%s) for video %s", cid, ref, video_id)
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='cite' WHERE video_id=?",
                (f"unresolved: {ref}", video_id),
            )
            conn.commit()


def load_paper_nodes(video_id: str) -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT doi, title, authors, year FROM resolved_citations WHERE video_id=? AND doi IS NOT NULL",
        (video_id,),
    ).fetchall()

    kuzu_conn = _init_graph()
    for row in rows:
        doi = row["doi"]
        try:
            kuzu_conn.execute(
                "MERGE (p:Paper {doi: $doi}) SET p.title=$title, p.authors=$authors, p.year=$year",
                {"doi": doi, "title": row["title"] or "", "authors": row["authors"] or "", "year": row["year"] or 0},
            )
        except Exception as e:
            logger.error("Error upserting Paper %s: %s", doi, e)
        try:
            kuzu_conn.execute(
                "MATCH (c:Chunk {video_id: $vid}), (p:Paper {doi: $doi}) MERGE (c)-[:REFERENCES]->(p)",
                {"vid": video_id, "doi": doi},
            )
        except Exception as e:
            logger.error("Error creating REFERENCES for Paper %s: %s", doi, e)


def cite_resolve() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id FROM videos WHERE graphed_at IS NOT NULL AND cited_at IS NULL"
    ).fetchall()
    for row in rows:
        try:
            resolve_citations(row["video_id"])
            load_paper_nodes(row["video_id"])
        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='cite' WHERE video_id=?",
                (str(e), row["video_id"]),
            )
            conn.commit()
