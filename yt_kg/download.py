"""Throttled audio download stage: fetches audio for un-downloaded videos."""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .db import init_db, utcnow

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("data/audio")

_YDL_OPTS = {
    "format": "bestaudio[abr<=96]/bestaudio",
    "outtmpl": str(AUDIO_DIR / "%(id)s.%(ext)s"),
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
    "sleep_interval_requests": 2,
    "max_sleep_interval": 5,
    "quiet": True,
    "no_warnings": True,
}


def _download_one(video_id: str, url: str) -> None:
    import yt_dlp  # ponytail: deferred import — yt_dlp is heavy
    conn = init_db()
    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            ydl.download([url])
        conn.execute(
            "UPDATE videos SET downloaded_at=? WHERE video_id=?",
            (utcnow(), video_id),
        )
        conn.commit()
        logger.info("downloaded %s", video_id)
    except Exception as e:
        conn.execute(
            "UPDATE videos SET last_error=?, error_stage='download' WHERE video_id=?",
            (str(e), video_id),
        )
        conn.commit()
        logger.error("download failed %s: %s", video_id, e)
    finally:
        conn.close()


def download(workers: int = 1) -> None:
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id, url FROM videos WHERE downloaded_at IS NULL AND skipped = 0"
    ).fetchall()
    conn.close()

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pool.map(lambda r: _download_one(r["video_id"], r["url"]), rows)
