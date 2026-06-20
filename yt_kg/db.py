"""Database connection and schema initialization for SQLite."""
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/jobs.sqlite")


def get_db_path() -> Path:
    """Get the database file path."""
    return DB_PATH


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database and create tables if they don't exist."""
    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Create videos table if it doesn't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id        TEXT PRIMARY KEY,
            title           TEXT,
            channel_id      TEXT,
            url             TEXT,
            downloaded_at   TEXT,
            transcribed_at  TEXT,
            chunked_at      TEXT,
            extracted_at    TEXT,
            graphed_at      TEXT,
            cited_at        TEXT,
            last_error      TEXT,
            error_stage     TEXT
        )
    """)
    
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN skipped INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE videos ADD COLUMN skip_reason TEXT")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # attempts: per-video failure counter used to promote permanently-failing
    # videos to skipped instead of retrying them on every pipeline run.
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN attempts INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # Discovery staging tier (Epic 5). status defaults to 'approved' so existing
    # config-sourced videos behave exactly as before; search/channel_new
    # candidates are inserted as 'candidate' and gated by scoring.
    for ddl in (
        "ALTER TABLE videos ADD COLUMN status TEXT DEFAULT 'approved'",
        "ALTER TABLE videos ADD COLUMN source TEXT DEFAULT 'config'",
        "ALTER TABLE videos ADD COLUMN query TEXT",
        "ALTER TABLE videos ADD COLUMN relevance_score REAL",
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    conn.commit()
    return conn


def utcnow() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


MAX_ATTEMPTS = 3


def record_failure(conn, video_id: str, stage: str, error: str) -> None:
    """Record a stage failure. After MAX_ATTEMPTS, mark the video skipped so it
    is no longer retried on every pipeline run."""
    conn.execute(
        "UPDATE videos SET last_error = ?, error_stage = ?, attempts = attempts + 1 "
        "WHERE video_id = ?",
        (error, stage, video_id),
    )
    conn.execute(
        "UPDATE videos SET skipped = 1, skip_reason = ? "
        "WHERE video_id = ? AND attempts >= ? AND skipped = 0",
        (f"max attempts exceeded in stage '{stage}': {error}", video_id, MAX_ATTEMPTS),
    )
    conn.commit()


def clear_error(conn, video_id: str) -> None:
    """Clear error state after a stage succeeds."""
    conn.execute(
        "UPDATE videos SET last_error = NULL, error_stage = NULL WHERE video_id = ?",
        (video_id,),
    )
    conn.commit()
