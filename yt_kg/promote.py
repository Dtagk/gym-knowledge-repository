"""Promote stage (Epic 5, Story 5.2): score candidate videos and promote the
relevant ones to 'approved' so they enter download/transcribe.

Scoring is reference-based and LLM-free: we build a "relevance centroid" from
signals already in the repo —

  1. the synthesis questions (what the corpus is *for*), and
  2. the names of high-value entities already in the graph (what it already
     knows about), weighted by how many videos mention them.

Each candidate's title (+ query that surfaced it) is embedded and scored by
cosine similarity to that centroid. Candidates at or above --threshold are
promoted; the score is persisted to videos.relevance_score either way so you
can audit and re-threshold without re-embedding.

    python -m yt_kg.promote                 # score + promote at default 0.35
    python -m yt_kg.promote --threshold 0.4
    python -m yt_kg.promote --dry-run       # score only, promote nothing
    python -m yt_kg.promote --top 20        # promote only the 20 best above threshold
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import yaml

from yt_kg.db import init_db

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_SYNTH_CONFIG = Path(__file__).parent.parent / "config" / "synthesis_questions.yaml"
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_KUZU_PATH = "data/graph.kuzu"
DEFAULT_THRESHOLD = 0.35


def _load_questions() -> list[str]:
    if not _SYNTH_CONFIG.exists():
        return []
    with open(_SYNTH_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f).get("questions", []) or []


def _high_value_entity_texts(limit: int = 200) -> list[str]:
    """Entity names weighted by video count — the corpus's established interests.

    Returns names repeated proportionally to APPEARS_IN degree (capped) so the
    centroid leans toward well-covered topics without an explicit weight vector.
    """
    try:
        import kuzu
    except ImportError:
        return []
    try:
        db = kuzu.Database(_KUZU_PATH, read_only=True)
        conn = kuzu.Connection(db)
        res = conn.execute(
            "MATCH (e:Entity)-[:APPEARS_IN]->(v:Video) "
            "RETURN e.name AS name, count(v) AS deg ORDER BY deg DESC LIMIT $lim",
            {"lim": limit},
        )
    except Exception as exc:  # graph may not exist yet on a cold repo
        logger.warning("entity centroid skipped: %s", exc)
        return []
    texts: list[str] = []
    while res.has_next():
        name, deg = res.get_next()
        if name:
            texts.extend([name] * min(int(deg), 3))  # cap influence of any one entity
    return texts


def _centroid(model: SentenceTransformer) -> np.ndarray | None:
    refs = _load_questions() + _high_value_entity_texts()
    if not refs:
        logger.warning("no reference signals (no questions, no graph) — cannot score")
        return None
    vecs = model.encode(refs, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
    c = np.mean(vecs, axis=0)
    n = np.linalg.norm(c)
    return c / n if n else c


def promote(threshold: float = DEFAULT_THRESHOLD, dry_run: bool = False, top: int | None = None) -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id, title, query FROM videos "
        "WHERE status = 'candidate' AND skipped = 0"
    ).fetchall()

    if not rows:
        logger.info("no candidates to score")
        conn.close()
        return

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(_MODEL_NAME)
    centroid = _centroid(model)
    if centroid is None:
        conn.close()
        return

    # Embed each candidate as "title. query" (query gives context when titles are terse).
    texts = [f"{r['title'] or ''}. {r['query'] or ''}".strip(". ") for r in rows]
    vecs = model.encode(texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
    scores = vecs @ centroid  # cosine, both normalized

    scored = sorted(
        ({"video_id": r["video_id"], "title": r["title"], "score": float(s)}
         for r, s in zip(rows, scores)),
        key=lambda x: x["score"],
        reverse=True,
    )

    # Persist every score (for audit / re-thresholding) regardless of dry-run.
    for item in scored:
        conn.execute(
            "UPDATE videos SET relevance_score = ? WHERE video_id = ?",
            (round(item["score"], 4), item["video_id"]),
        )
    conn.commit()

    eligible = [s for s in scored if s["score"] >= threshold]
    if top is not None:
        eligible = eligible[:top]

    logger.info("scored %d candidates; %d at/above threshold %.2f%s",
                len(scored), len(eligible), threshold,
                f" (capped to top {top})" if top else "")

    if dry_run:
        for s in scored[:15]:
            mark = "PROMOTE" if s in eligible else "  keep "
            logger.info("[%s] %.3f  %s", mark, s["score"], (s["title"] or "")[:70])
        logger.info("dry-run: no changes written to status")
        conn.close()
        return

    for s in eligible:
        conn.execute(
            "UPDATE videos SET status = 'approved' WHERE video_id = ? AND status = 'candidate'",
            (s["video_id"],),
        )
    conn.commit()
    conn.close()
    logger.info("promoted %d candidates to approved", len(eligible))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Score and promote candidate videos")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true", help="score only; promote nothing")
    ap.add_argument("--top", type=int, default=None, help="promote at most N best above threshold")
    args = ap.parse_args()
    promote(threshold=args.threshold, dry_run=args.dry_run, top=args.top)


if __name__ == "__main__":
    main()
