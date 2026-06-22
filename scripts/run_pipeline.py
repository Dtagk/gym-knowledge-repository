import argparse
import logging
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import lancedb
from sentence_transformers import SentenceTransformer

from yt_kg.discover import discover
from yt_kg.filter import filter_videos
from yt_kg.promote import promote
from yt_kg.validate import validate
from yt_kg.download import download
from yt_kg.transcribe import transcribe
from yt_kg.embed import embed
from yt_kg.extract import extract
from yt_kg.resolve import resolve
from yt_kg.graph import graph
from yt_kg.cite_extract import cite_extract
from yt_kg.cite_resolve import cite_resolve
from yt_kg.cite_pdf import cite_pdf_stage
from yt_kg.enrich_papers import enrich_papers
from yt_kg.classify import classify
from yt_kg.export import export

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_OLLAMA_URL      = "http://localhost:11434"
_REQUIRED_MODELS = ["qwen2.5-coder:7b"]


def setup_env(skip: bool = False) -> None:
    """Ensure Docker Desktop + Ollama container + required models are ready."""
    if skip:
        return
    if not shutil.which("docker"):
        raise RuntimeError("docker not found on PATH — install Docker Desktop.")
    r = subprocess.run(["docker", "info"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("Docker daemon is not running — start Docker Desktop.")

    running = subprocess.run(
        ["docker", "ps", "--filter", "name=ollama", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout
    if "ollama" not in running:
        logger.info("Starting Ollama container via docker compose…")
        subprocess.run(
            ["docker", "compose", "up", "-d", "ollama"],
            check=True, cwd=Path(__file__).resolve().parent.parent,
        )

    for _ in range(30):
        try:
            urllib.request.urlopen(f"{_OLLAMA_URL}/api/tags", timeout=2)
            break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("Ollama API not ready — check 'docker logs ollama'.")

    present = subprocess.run(
        ["docker", "exec", "ollama", "ollama", "list"],
        capture_output=True, text=True,
    ).stdout.lower()
    for model in _REQUIRED_MODELS:
        if model.split(":")[0] not in present:
            logger.info("Pulling %s…", model)
            subprocess.run(["docker", "exec", "ollama", "ollama", "pull", model], check=True)
    logger.info("Environment ready.")


def _run_stage(fn, name: str) -> None:
    try:
        logger.info("Running stage: %s", name)
        fn()
        logger.info("Stage completed: %s", name)
    except Exception as e:
        logger.error("Stage failed: %s — %s", name, e, exc_info=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube Fitness Knowledge Graph Pipeline")
    parser.add_argument("--workers",    type=int, default=4,    help="Parallel workers for I/O stages (default: 4)")
    parser.add_argument("--skip-setup", action="store_true",    help="Skip Docker/Ollama environment check")
    args = parser.parse_args()
    w = args.workers

    setup_env(skip=args.skip_setup)

    stages = [
        (discover,                        "discover"),
        (filter_videos,                   "filter"),
        (promote,                         "promote"),
        (lambda: validate(workers=2),     "validate"),
        (lambda: download(workers=w),     "download"),
        (lambda: transcribe(workers=w),   "transcribe"),
        (lambda: embed(workers=w),        "embed"),
        (extract,                         "extract"),
        (resolve,                         "resolve"),
        (graph,                           "graph"),
        (classify,                        "classify"),
        (export,                          "export"),
        (cite_extract,                    "cite_extract"),
        (cite_resolve,                    "cite_resolve"),
        (lambda: cite_pdf_stage(workers=w), "cite_pdf"),
        (enrich_papers,                   "enrich_papers"),
    ]

    for fn, name in stages:
        _run_stage(fn, name)

    # Retry pass — re-runs every stage; each stage's WHERE clause naturally
    # picks up only videos that failed or were skipped the first time.
    logger.info("Retry pass — re-running stages to pick up transient failures")
    for fn, name in stages:
        _run_stage(fn, f"{name} [retry]")

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
