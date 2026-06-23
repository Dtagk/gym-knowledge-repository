"""Export discovered_papers table → docs/data/papers.json for the UI.

    python -m yt_kg.export_papers
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from yt_kg.db import init_db

logger = logging.getLogger(__name__)

_OUT = Path("docs/data/papers.json")


def export_papers() -> None:
    conn = init_db()
    try:
        rows = conn.execute(
            "SELECT doi, title, authors, year, abstract, oa_url, source, query "
            "FROM discovered_papers ORDER BY year DESC, title"
        ).fetchall()
    except Exception:
        logger.info("discovered_papers table not found — run discover_papers first")
        conn.close()
        return

    papers = [dict(r) for r in rows]
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Exported %d papers → %s", len(papers), _OUT)
    conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    export_papers()
