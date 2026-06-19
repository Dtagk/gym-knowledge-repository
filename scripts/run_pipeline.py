import logging
from pathlib import Path

import lancedb
from sentence_transformers import SentenceTransformer

from yt_kg.discover import discover
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
    _run_stage(discover, "discover")
    _run_stage(download, "download")
    _run_stage(transcribe, "transcribe")
    _run_stage(embed, "embed")
    _run_stage(extract, "extract")
    _run_stage(resolve, "resolve")
    _run_stage(graph, "graph")
    _run_stage(cite_extract, "cite_extract")
    _run_stage(cite_resolve, "cite_resolve")
    _run_stage(cite_pdf_stage, "cite_pdf")

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
