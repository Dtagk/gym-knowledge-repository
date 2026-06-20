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


def load_searches_config() -> list[dict[str, Any]]:
    """Load the optional `searches:` block: list of {query, limit} entries."""
    path = Path(__file__).parent.parent / "config" / "channels.yaml"
    if not path.exists():
        path = CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("searches", []) or []


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

    _discover_searches(conn)

    conn.commit()
    conn.close()
    logger.info("Discovery complete")


def _discover_searches(conn) -> None:
    """Seed candidate videos from YouTube search queries (Story 5.1, FR11/FR12).

    Results are staged as status='candidate', source='search' so they do NOT enter
    download/transcribe until promoted (by scoring in Story 5.2 or manual review).
    """
    searches = load_searches_config()
    if not searches:
        return

    for entry in searches:
        query = (entry.get("query") or "").strip()
        if not query:
            continue
        limit = int(entry.get("limit", 20))
        logger.info("Discovering search: %r (limit %d)", query, limit)

        try:
            with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = info.get("entries", []) or []
        except Exception:
            logger.exception("Error running search %r", query)
            continue

        for e in entries:
            if not e or "id" not in e:
                continue
            # Do not overwrite an existing approved/config row; only insert net-new
            # candidates. INSERT OR IGNORE keys on video_id (PRIMARY KEY).
            conn.execute(
                "INSERT OR IGNORE INTO videos "
                "(video_id, title, channel_id, url, source, status, query) "
                "VALUES (?, ?, ?, ?, 'search', 'candidate', ?)",
                (
                    e["id"],
                    e.get("title", ""),
                    e.get("channel_id") or e.get("uploader_id") or "search",
                    e.get("webpage_url") or e.get("url") or f"https://youtu.be/{e['id']}",
                    query,
                ),
            )
        conn.commit()
