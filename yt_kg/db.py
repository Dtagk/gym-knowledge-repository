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
    
    conn.commit()
    return conn


def utcnow() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()
