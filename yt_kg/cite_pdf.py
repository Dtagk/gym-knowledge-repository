import logging
from pathlib import Path

import httpx

from yt_kg.db import init_db, utcnow

logger = logging.getLogger(__name__)


def _doi_slug(doi: str) -> str:
    return doi.replace("/", "_").replace(".", "_")


def download_pdf(doi: str, oa_url: str) -> bool:
    slug = _doi_slug(doi)
    dest = Path(f"data/papers/{slug}.pdf")
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.get(oa_url, timeout=30, follow_redirects=True)
        if response.status_code == 200:
            dest.write_bytes(response.content)
            return True
        logger.warning("Failed to download PDF for DOI %s: HTTP %s", doi, response.status_code)
        return False
    except Exception as e:
        logger.warning("Error downloading PDF for DOI %s: %s", doi, e)
        return False


def cite_pdf(video_id: str) -> None:
    conn = init_db()
    citations = conn.execute(
        "SELECT doi, oa_url FROM resolved_citations WHERE video_id=? AND doi IS NOT NULL AND oa_url IS NOT NULL",
        (video_id,),
    ).fetchall()

    for row in citations:
        if not download_pdf(row["doi"], row["oa_url"]):
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='cite_pdf' WHERE video_id=?",
                (f"PDF download failed for DOI: {row['doi']}", video_id),
            )
            conn.commit()

    conn.execute("UPDATE videos SET cited_at=? WHERE video_id=?", (utcnow(), video_id))
    conn.commit()


def cite_pdf_stage() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id FROM videos WHERE graphed_at IS NOT NULL AND cited_at IS NULL"
    ).fetchall()
    for row in rows:
        try:
            cite_pdf(row["video_id"])
        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='cite_pdf' WHERE video_id=?",
                (str(e), row["video_id"]),
            )
            conn.commit()
