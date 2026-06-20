# Sprint Fix S1: db-robustness

**Branch:** `fix/db-robustness`  
**Files:** `yt_kg/db.py` only

## Issues to fix

### Fix 1 — Enable WAL mode to prevent concurrent writer deadlocks

`yt_kg/db.py`, `init_db()` function.

The pipeline runs downloads with `workers > 1`. Multiple threads each call `init_db()` and get
their own SQLite connection. Without WAL mode, concurrent writes (`UPDATE videos SET downloaded_at`)
contend for an exclusive lock and raise `sqlite3.OperationalError: database is locked`, which is
caught by the caller's bare `except` and recorded as a spurious download error for a video that
actually downloaded successfully.

**Fix:** Add `conn.execute("PRAGMA journal_mode=WAL")` immediately after `conn.row_factory = sqlite3.Row`
in `init_db()`.

### Fix 2 — Migration exception handling catches too broadly

`yt_kg/db.py`, lines 39–44.

```python
try:
    conn.execute("ALTER TABLE videos ADD COLUMN skipped INTEGER DEFAULT 0")
    conn.execute("ALTER TABLE videos ADD COLUMN skip_reason TEXT")
    conn.commit()
except Exception:
    pass
```

The intent is to silently ignore "duplicate column" errors from SQLite when the column already
exists. But `except Exception: pass` also swallows disk-full errors, permission errors, and
connection failures. If the first `ALTER` succeeds but the second fails (disk full), `skipped`
exists but `skip_reason` does not — `filter.py` then fails every run with no visible root cause.

**Fix:** Catch only `sqlite3.OperationalError` and re-raise unless it's a duplicate-column error:

```python
except sqlite3.OperationalError as e:
    if "duplicate column" not in str(e).lower():
        raise
```

## Current file content

```python
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
    
    try:
        conn.execute("ALTER TABLE videos ADD COLUMN skipped INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE videos ADD COLUMN skip_reason TEXT")
        conn.commit()
    except Exception:
        pass

    conn.commit()
    return conn


def utcnow() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()
```

## Acceptance criteria

- `PRAGMA journal_mode=WAL` is set in `init_db()` before any table creation
- Migration catches only `sqlite3.OperationalError` with "duplicate column" in the message; all other errors propagate
- No other changes to the file
