# Sprint Fix S4: embed-graph-safety

**Branch:** `fix/embed-graph-safety`  
**Files:** `yt_kg/embed.py`, `yt_kg/graph.py`

## Issues to fix

### Fix 1 — `video_id` interpolated into LanceDB filter/delete strings

`yt_kg/embed.py` and `yt_kg/graph.py` both interpolate `video_id` directly into LanceDB
`.where()` and `.delete()` filter strings. LanceDB does not support parameterized queries.
YouTube video IDs are currently 11-char alphanumeric strings, but this should be validated
explicitly before interpolation to prevent any future breakage.

**Fix in embed.py:** Before the `table.delete(f"video_id = '{video_id}'")`  call, add:
```python
import re
if not re.fullmatch(r'[A-Za-z0-9_-]{6,20}', video_id):
    raise ValueError(f"Unexpected video_id format: {video_id!r}")
```

**Fix in graph.py:** The same validation before the `.where(f"video_id = '{video_id}'"` call
in the LanceDB query inside `load_video()`.

### Fix 2 — `graph()` never closes its outer SQLite connection

`yt_kg/graph.py`, `graph()` function (near the bottom of the file).

```python
def graph() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT * FROM videos WHERE extracted_at IS NOT NULL AND graphed_at IS NULL"
    ).fetchall()
    for row in rows:
        load_video(row["video_id"])
```

`conn` is opened, used to fetch the row list, and then never closed. `load_video()` opens its own
connections internally. The outer `conn` stays open for the entire batch, holding a file handle
on Windows and potentially causing `database is locked` with the connections inside `load_video`.

**Fix:** Close the connection after fetching rows (they're already in memory as a list):
```python
def graph() -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT * FROM videos WHERE extracted_at IS NOT NULL AND graphed_at IS NULL"
    ).fetchall()
    conn.close()
    for row in rows:
        load_video(row["video_id"])
```

## Files to read

Read `yt_kg/embed.py` and `yt_kg/graph.py` in full before making changes. Apply only the
minimal changes described above.

## Acceptance criteria

- `embed.py` validates `video_id` with `re.fullmatch(r'[A-Za-z0-9_-]{6,20}', ...)` before any `.delete()` or `.where()` call that interpolates it
- `graph.py` validates `video_id` before any LanceDB `.where()` call that interpolates it
- `graph()` closes its outer SQLite connection immediately after `.fetchall()`
- No other changes to either file
