"""Pre-flight check: mark permanently-unavailable videos as skipped before download.

Calls extract_info(download=False) for each approved/pending video and skips
videos that are deleted, private, members-only, or otherwise undownloadable.
"""
import logging
from pathlib import Path

from .db import init_db

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_COOKIES_FILE = _ROOT / "cookies.txt"

_PERMANENT_PATTERNS = (
    "video unavailable",
    "join this channel",
    "members-only",
    "this video is private",
    "has been removed",
    "not available in your country",
    "this video requires payment",
    "age-restricted",
)

_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    **( {"cookiefile": str(_COOKIES_FILE)} if _COOKIES_FILE.exists()
        else {"cookiesfrombrowser": ("chrome",)} ),
}


def _is_permanent(err: str) -> bool:
    low = err.lower()
    return any(p in low for p in _PERMANENT_PATTERNS)


def _check_one(video_id: str, url: str) -> None:
    import yt_dlp
    conn = init_db()
    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            ydl.extract_info(url, download=False)
    except Exception as e:
        msg = str(e)
        if _is_permanent(msg):
            conn.execute(
                "UPDATE videos SET skipped=1, last_error=?, error_stage='validate' WHERE video_id=?",
                (msg[:300], video_id),
            )
            conn.commit()
            logger.info("pre-skipped %s: %s", video_id, msg[:80])
        else:
            logger.debug("transient error for %s (will retry at download): %s", video_id, msg[:80])
    finally:
        conn.close()


def validate(workers: int = 2) -> None:
    from concurrent.futures import ThreadPoolExecutor
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id, url FROM videos "
        "WHERE downloaded_at IS NULL AND skipped=0 AND status='approved'"
    ).fetchall()
    conn.close()

    if not rows:
        logger.info("validate: nothing to check")
        return

    logger.info("validate: checking %d videos", len(rows))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pool.map(lambda r: _check_one(r["video_id"], r["url"]), rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    validate()
