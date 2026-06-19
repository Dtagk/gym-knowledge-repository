"""Embedding stage: encodes transcript chunks with bge-small and stores in LanceDB."""
import logging
from pathlib import Path

import lancedb
import pyarrow as pa
from sentence_transformers import SentenceTransformer

from .chunk import chunk
from .db import init_db, utcnow

logger = logging.getLogger(__name__)

SCHEMA = pa.schema([
    ("chunk_id", pa.string()),
    ("video_id", pa.string()),
    ("start", pa.float64()),
    ("end", pa.float64()),
    ("text", pa.string()),
    ("vector", pa.list_(pa.float32(), 384)),
])


def embed() -> None:
    Path("data/vectors.lance").mkdir(parents=True, exist_ok=True)
    conn = init_db()
    rows = conn.execute(
        "SELECT * FROM videos WHERE transcribed_at IS NOT NULL AND chunked_at IS NULL"
    ).fetchall()

    if not rows:
        return

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")  # CPU, no device= arg
    db = lancedb.connect("data/vectors.lance")

    # Open or create table
    if "chunks" in db.table_names():
        table = db.open_table("chunks")
    else:
        table = db.create_table("chunks", schema=SCHEMA)

    for row in rows:
        video = dict(row)
        video_id = video["video_id"]
        try:
            chunks = chunk(video)
            if not chunks:
                logger.warning("no chunks for %s", video_id)
                continue

            vectors = model.encode(
                [c["text"] for c in chunks], batch_size=32
            ).tolist()

            records = [
                {**c, "vector": v} for c, v in zip(chunks, vectors)
            ]

            # Delete existing chunks for this video then re-add (upsert by video)
            try:
                table.delete(f"video_id = '{video_id}'")
            except Exception:
                pass
            table.add(records)

            conn.execute(
                "UPDATE videos SET chunked_at=? WHERE video_id=?",
                (utcnow(), video_id),
            )
            conn.commit()
            logger.info("embedded %d chunks for %s", len(chunks), video_id)
        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='chunk' WHERE video_id=?",
                (str(e), video_id),
            )
            conn.commit()
            logger.error("embed failed %s: %s", video_id, e)
