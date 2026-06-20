import argparse
import logging
from pathlib import Path

import lancedb
from sentence_transformers import SentenceTransformer

from yt_kg.discover import discover
from yt_kg.filter import filter_videos
from yt_kg.download import download
from yt_kg.transcribe import transcribe
from yt_kg.embed import embed
from yt_kg.extract import extract
from yt_kg.resolve import resolve
from yt_kg.graph import graph
from yt_kg.cite_extract import cite_extract
from yt_kg.cite_resolve import cite_resolve
from yt_kg.cite_pdf import cite_pdf_stage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_stage(fn, name: str) -> None:
    try:
        logger.info("Running stage: %s", name)
        fn()
        logger.info("Stage completed: %s", name)
    except Exception as e:
        logger.error("Stage failed: %s — %s", name, e, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube Fitness Knowledge Graph Pipeline")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers for I/O stages (default: 4)")
    args = parser.parse_args()
    w = args.workers

    _run_stage(discover, "discover")
    _run_stage(filter_videos, "filter")
    _run_stage(lambda: download(workers=w), "download")
    _run_stage(lambda: transcribe(workers=w), "transcribe")
    _run_stage(lambda: embed(workers=w), "embed")
    _run_stage(extract, "extract")           # sequential — Ollama bottleneck
    _run_stage(resolve, "resolve")           # sequential — full-batch operation
    _run_stage(graph, "graph")               # sequential — full-batch operation
    _run_stage(cite_extract, "cite_extract")
    _run_stage(cite_resolve, "cite_resolve")
    _run_stage(lambda: cite_pdf_stage(workers=w), "cite_pdf")

    logger.info("All stages done. Running smoke test...")

    db_path = Path(__file__).resolve().parent.parent / "data" / "vectors.lance"
    db = lancedb.connect(str(db_path))
    table = db.open_table("chunks")

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")  # CPU
    query_vector = model.encode("posterior chain exercises").tolist()

    results = table.search(query_vector).limit(5).to_list()
    for row in results:
        print(f"https://youtu.be/{row['video_id']}?t={int(row['start'])}")

    logger.info("Smoke test complete.")


if __name__ == "__main__":
    main()
