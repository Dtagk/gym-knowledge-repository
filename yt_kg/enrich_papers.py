"""Paper enrichment stage.

Turns the citation layer from metadata stubs into first-class, searchable
knowledge — using ONLY sources already fetched by earlier stages:

  1. PDF mining: read the OA PDFs already downloaded to data/papers/ by the
     cite_pdf stage, pull the abstract/body text, chunk + embed it into the same
     LanceDB table as transcripts (tagged source='paper'), and run the existing
     entity/relation extraction over it. Papers become answerable content.

  2. OpenAlex relations: re-read the OpenAlex Work record (concepts,
     referenced_works) — fields returned for free in the same API response the
     resolver already makes — to add Paper-[ABOUT]->Concept and intra-corpus
     Paper-[CITES]->Paper edges. Cached per DOI so this re-fetch is cheap.

  3. Paper↔exercise linking: the entities extracted from a paper's text are
     resolved against the SAME canonical entity table as the videos, producing
     Paper-[DISCUSSES]->Entity edges that connect the research layer to the
     coaching layer.

No new corpus is fetched here: PDFs and DOIs already exist on disk / in
resolved_citations. Run after the cite_pdf stage:

    python -m yt_kg.enrich_papers
    python -m yt_kg.enrich_papers --no-pdf      # relations + concepts only
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import requests

from yt_kg.db import init_db, utcnow
from yt_kg.graph import _init_graph

logger = logging.getLogger(__name__)

_PAPERS_DIR = Path("data/papers")
_OA_CACHE = Path("data/papers/_openalex_cache")
_HEADERS = {"User-Agent": "gym-kg-pipeline/1.0 (paper enrichment)"}
_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def _doi_slug(doi: str) -> str:
    return doi.replace("/", "_").replace(".", "_")


# ---------------------------------------------------------------------------
# Schema additions (idempotent)
# ---------------------------------------------------------------------------

def _ensure_schema(kuzu_conn) -> None:
    stmts = [
        "CREATE NODE TABLE IF NOT EXISTS Concept (concept_id STRING, name STRING, PRIMARY KEY (concept_id))",
        "CREATE REL TABLE IF NOT EXISTS ABOUT (FROM Paper TO Concept, score DOUBLE)",
        "CREATE REL TABLE IF NOT EXISTS CITES (FROM Paper TO Paper)",
        "CREATE REL TABLE IF NOT EXISTS DISCUSSES (FROM Paper TO Entity, evidence STRING)",
    ]
    for s in stmts:
        try:
            kuzu_conn.execute(s)
        except Exception as exc:
            logger.debug("schema stmt skipped: %s", exc)


def _track_table(sql_conn) -> None:
    sql_conn.execute(
        "CREATE TABLE IF NOT EXISTS enriched_papers ("
        "doi TEXT PRIMARY KEY, text_mined INTEGER DEFAULT 0, "
        "relations_added INTEGER DEFAULT 0, enriched_at TEXT)"
    )
    sql_conn.commit()


# ---------------------------------------------------------------------------
# 1. PDF text extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_path: Path, max_pages: int = 8) -> str:
    """Plain-text extraction. Caps pages: abstract+intro+methods carry the
    signal; full-paper text mostly adds noise and embedding cost."""
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("pypdf not installed; cannot mine PDFs")
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        pages = reader.pages[:max_pages]
        text = "\n".join(p.extract_text() or "" for p in pages)
        # collapse whitespace; strip reference-list tail if present
        text = re.sub(r"\s+", " ", text).strip()
        text = re.split(r"\bReferences\b|\bBibliography\b", text, maxsplit=1)[0]
        return text
    except Exception as exc:
        logger.warning("PDF extract failed for %s: %s", pdf_path.name, exc)
        return ""


def _chunk_text(text: str, size: int = 1800, overlap: int = 200) -> list[str]:
    """Simple char-window chunker mirroring the transcript chunker's sizing."""
    if not text:
        return []
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + size])
        nxt = i + size - overlap
        i = nxt if nxt > i else i + size
    return chunks


# ---------------------------------------------------------------------------
# 2. OpenAlex relational metadata (cached)
# ---------------------------------------------------------------------------

def _openalex_work(doi: str) -> dict | None:
    _OA_CACHE.mkdir(parents=True, exist_ok=True)
    cache = _OA_CACHE / f"{_doi_slug(doi)}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = f"https://api.openalex.org/works/doi:{doi}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception as exc:
        logger.warning("OpenAlex fetch failed for %s: %s", doi, exc)
        return None


def _add_concepts_and_citations(kuzu_conn, doi: str, work: dict, corpus_dois: set[str],
                                id_to_doi: dict[str, str]) -> int:
    added = 0
    # Concepts (ABOUT)
    for c in work.get("concepts", []):
        if c.get("score", 0) < 0.3:  # OpenAlex confidence threshold
            continue
        cid = c.get("id", "")
        name = c.get("display_name", "")
        if not cid or not name:
            continue
        try:
            kuzu_conn.execute(
                "MERGE (c:Concept {concept_id: $cid}) SET c.name = $name", {"cid": cid, "name": name}
            )
            kuzu_conn.execute(
                "MATCH (p:Paper {doi: $doi}), (c:Concept {concept_id: $cid}) "
                "MERGE (p)-[r:ABOUT]->(c) SET r.score = $score",
                {"doi": doi, "cid": cid, "score": float(c.get("score", 0))},
            )
            added += 1
        except Exception:
            pass

    # Intra-corpus citations (CITES): only link to papers we actually have.
    for ref_id in work.get("referenced_works", []):
        ref_doi = id_to_doi.get(ref_id)
        if ref_doi and ref_doi in corpus_dois and ref_doi != doi:
            try:
                kuzu_conn.execute(
                    "MATCH (a:Paper {doi: $a}), (b:Paper {doi: $b}) MERGE (a)-[:CITES]->(b)",
                    {"a": doi, "b": ref_doi},
                )
                added += 1
            except Exception:
                pass
    return added


def _build_id_to_doi_index() -> dict[str, str]:
    """One-pass reverse index: OpenAlex work-id -> DOI, from cached Work records.
    Built once per run so citation resolution is O(1) per referenced work rather
    than re-scanning the whole cache directory each time."""
    index: dict[str, str] = {}
    if not _OA_CACHE.exists():
        return index
    for f in _OA_CACHE.glob("*.json"):
        try:
            w = json.loads(f.read_text(encoding="utf-8"))
            wid = w.get("id")
            doi = w.get("doi", "")
            if wid and doi:
                index[wid] = doi.replace("https://doi.org/", "")
        except Exception:
            continue
    return index


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def enrich_papers(mine_pdfs: bool = True) -> None:
    sql_conn = init_db()
    _track_table(sql_conn)
    kuzu_conn = _init_graph()
    _ensure_schema(kuzu_conn)

    # All resolved papers with a DOI (the universe we can enrich).
    papers = sql_conn.execute(
        "SELECT DISTINCT doi, video_id, title FROM resolved_citations "
        "WHERE doi IS NOT NULL"
    ).fetchall()
    corpus_dois = {p["doi"] for p in papers}
    already = {
        r["doi"] for r in sql_conn.execute("SELECT doi FROM enriched_papers").fetchall()
    }

    logger.info("%d papers in corpus, %d already enriched", len(corpus_dois), len(already))

    # Lazy heavy imports only if mining.
    model = None
    table = None
    extract_fn = None
    if mine_pdfs:
        try:
            import lancedb
            from sentence_transformers import SentenceTransformer
            from yt_kg.extract import _extract_chunk  # reuse transcript extractor

            model = SentenceTransformer(_MODEL_NAME)
            extract_fn = _extract_chunk
            ldb = lancedb.connect("data/vectors.lance")
            table = ldb.open_table("chunks") if "chunks" in ldb.table_names() else None
            if table is not None:
                _ensure_source_column(table)
        except Exception as exc:
            logger.error("PDF-mining deps unavailable, skipping mining: %s", exc)
            mine_pdfs = False

    # Pass 1: fetch+cache every paper's OpenAlex Work record up front, so the
    # work-id -> DOI index is complete before we resolve CITES edges (a paper
    # may reference another whose record hasn't been fetched yet).
    to_process = [p for p in papers if p["doi"] not in already]
    for paper in to_process:
        _openalex_work(paper["doi"])  # populates _OA_CACHE (no-op if cached)
    id_to_doi = _build_id_to_doi_index()

    enriched = 0
    for paper in to_process:
        doi = paper["doi"]
        text_mined = 0
        rel_added = 0

        # ---- 2 & 3 (cheap): OpenAlex concepts + intra-corpus citations ----
        work = _openalex_work(doi)  # served from cache
        if work:
            rel_added = _add_concepts_and_citations(kuzu_conn, doi, work, corpus_dois, id_to_doi)

        # ---- 1: PDF mining + entity linking ----
        if mine_pdfs and table is not None:
            pdf_path = _PAPERS_DIR / f"{_doi_slug(doi)}.pdf"
            if pdf_path.exists():
                text = _extract_pdf_text(pdf_path)
                pieces = _chunk_text(text)
                if pieces:
                    _index_paper_chunks(table, model, doi, pieces)
                    _link_paper_entities(kuzu_conn, sql_conn, doi, pieces, extract_fn)
                    text_mined = 1

        sql_conn.execute(
            "INSERT OR REPLACE INTO enriched_papers (doi, text_mined, relations_added, enriched_at) "
            "VALUES (?,?,?,?)",
            (doi, text_mined, rel_added, utcnow()),
        )
        sql_conn.commit()
        enriched += 1
        if enriched % 10 == 0:
            logger.info("enriched %d papers...", enriched)

    sql_conn.close()
    logger.info("done: enriched %d papers", enriched)


def _ensure_source_column(table) -> None:
    """Add a 'source' column to the LanceDB chunks table if missing, so paper
    chunks can be distinguished from transcript chunks at query time."""
    try:
        if "source" not in table.schema.names:
            table.add_columns({"source": "'transcript'"})  # backfill existing rows
    except Exception as exc:
        logger.debug("source column add skipped (may already exist): %s", exc)


def _index_paper_chunks(table, model, doi: str, pieces: list[str]) -> None:
    slug = _doi_slug(doi)
    vectors = model.encode(pieces, batch_size=16, show_progress_bar=False).tolist()
    records = [
        {
            "chunk_id": f"paper:{slug}:{i}",
            "video_id": f"paper:{slug}",  # namespaced so it never collides with a real video
            "start": 0.0,
            "end": 0.0,
            "text": piece,
            "vector": vec,
            "source": "paper",
        }
        for i, (piece, vec) in enumerate(zip(pieces, vectors))
    ]
    try:
        table.delete(f"video_id = 'paper:{slug}'")
    except Exception:
        pass
    table.add(records)


def _link_paper_entities(kuzu_conn, sql_conn, doi: str, pieces: list[str], extract_fn) -> None:
    """Extract entities from paper text and link resolved ones to the Paper."""
    alias_rows = sql_conn.execute(
        "SELECT alias, canonical_id, type FROM entity_aliases"
    ).fetchall()
    name_to_cid: dict[str, str] = {}
    for r in alias_rows:
        name_to_cid.setdefault(r["alias"].lower(), r["canonical_id"])

    seen: set[str] = set()
    # Cap to first few chunks: abstract/intro hold the studied exercises.
    for piece in pieces[:4]:
        try:
            extraction = extract_fn(piece)
        except Exception:
            continue
        for ent in extraction.entities:
            cid = name_to_cid.get(ent.name.lower())
            if not cid or cid in seen:
                continue
            seen.add(cid)
            try:
                kuzu_conn.execute(
                    "MATCH (p:Paper {doi: $doi}), (e:Entity {canonical_id: $cid}) "
                    "MERGE (p)-[r:DISCUSSES]->(e) SET r.evidence = $ev",
                    {"doi": doi, "cid": cid, "ev": ent.name},
                )
            except Exception:
                pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Enrich the KG from already-fetched papers")
    ap.add_argument("--no-pdf", action="store_true", help="skip PDF mining; relations + concepts only")
    args = ap.parse_args()
    enrich_papers(mine_pdfs=not args.no_pdf)


if __name__ == "__main__":
    main()
