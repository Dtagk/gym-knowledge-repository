"""Throttled audio download stage: fetches audio for un-downloaded videos."""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .db import init_db, utcnow

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
AUDIO_DIR = _ROOT / "data/audio"
_COOKIES_FILE = _ROOT / "cookies.txt"

_FFMPEG_DIRS = [
    Path(r"C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"),
]
_FFMPEG_LOCATION = next((str(p) for p in _FFMPEG_DIRS if (p / "ffmpeg.exe").exists()), None)

_YDL_OPTS = {
    "format": "bestaudio[abr<=96]/bestaudio/best",
    "outtmpl": str(AUDIO_DIR / "%(id)s.%(ext)s"),
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
    **( {"cookiefile": str(_COOKIES_FILE)} if _COOKIES_FILE.exists()
        else {"cookiesfrombrowser": ("chrome",)} ),
    "sleep_interval": 5,
    "sleep_interval_requests": 8,
    "max_sleep_interval": 15,
    "quiet": True,
    "no_warnings": True,
    **({"ffmpeg_location": _FFMPEG_LOCATION} if _FFMPEG_LOCATION else {}),
}


def _download_one(video_id: str, url: str) -> None:
    import yt_dlp  # ponytail: deferred import — yt_dlp is heavy
    conn = init_db()
    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            ydl.download([url])
        conn.execute(
            "UPDATE videos SET downloaded_at=?, last_error=NULL, error_stage=NULL WHERE video_id=?",
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
        "SELECT video_id, url FROM videos WHERE downloaded_at IS NULL AND skipped = 0 AND status = 'approved'"
    ).fetchall()
    conn.close()

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pool.map(lambda r: _download_one(r["video_id"], r["url"]), rows)


if __name__ == "__main__":
    download()
