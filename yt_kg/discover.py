"""Video discovery from YouTube channels, playlists, and individual videos."""
import logging
from pathlib import Path
from typing import Any

import yaml
import yt_dlp

from .db import init_db

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config/channels.yaml")


def load_channels_config() -> list[dict[str, Any]]:
    path = Path(__file__).parent.parent / "config" / "channels.yaml"
    if not path.exists():
        path = CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("channels", [])


def _ydl_opts() -> dict:
    return {"quiet": True, "no_warnings": True, "extract_flat": True}


def discover() -> None:
    channels = load_channels_config()
    conn = init_db()

    for ch in channels:
        url = ch["url"]
        channel_id = ch.get("id", "unknown")
        entry_type = ch.get("type", "channel")
        logger.info("Discovering %s: %s", entry_type, url)

        try:
            with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
                info = ydl.extract_info(url, download=False)

            if entry_type == "video":
                entries = [info]
            else:
                entries = info.get("entries", []) or []

            for entry in entries:
                if not entry or "id" not in entry:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO videos (video_id, title, channel_id, url)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        entry["id"],
                        entry.get("title", ""),
                        channel_id,
                        entry.get("webpage_url") or entry.get("url") or url,
                    ),
                )

        except Exception:
            logger.exception("Error discovering %s", url)

    conn.commit()
    conn.close()
    logger.info("Discovery complete")
