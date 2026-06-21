"""Video discovery from YouTube channels, playlists, and individual videos."""
import logging
import urllib.parse
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


def load_channel_whitelist() -> set[str]:
    """Load static YouTube channel IDs from the channel_whitelist: block."""
    path = Path(__file__).parent.parent / "config" / "channels.yaml"
    if not path.exists():
        path = CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        return set(yaml.safe_load(f).get("channel_whitelist", []) or [])


def _ydl_opts() -> dict:
    return {"quiet": True, "no_warnings": True, "extract_flat": True}


def discover() -> None:
    channels = load_channels_config()
    conn = init_db()
    known_yt_channel_ids: set[str] = load_channel_whitelist()

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
                yt_ch = entry.get("channel_id") or entry.get("uploader_id")
                if yt_ch:
                    known_yt_channel_ids.add(yt_ch)
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

    _discover_searches(conn, known_yt_channel_ids)

    conn.commit()
    conn.close()
    logger.info("Discovery complete")


def _discover_searches(conn, known_yt_channel_ids: set[str] | None = None) -> None:
    """Seed candidate videos from YouTube search queries (Story 5.1, FR11/FR12).

    Results are staged as status='candidate', source='search' so they do NOT enter
    download/transcribe until promoted (by scoring in Story 5.2 or manual review).

    If a search entry has channels_only: true, only results whose YouTube channel_id
    matches a channel seen in the current discovery run are kept.
    """
    searches = load_searches_config()
    if not searches:
        return

    for entry in searches:
        query = (entry.get("query") or "").strip()
        if not query:
            continue
        limit = int(entry.get("limit", 20))
        channels_only = bool(entry.get("channels_only", False))
        logger.info("Discovering search: %r (limit %d, channels_only=%s)", query, limit, channels_only)

        if channels_only and known_yt_channel_ids:
            # Search within each whitelisted channel individually
            inserted = 0
            total = 0
            for ch_id in known_yt_channel_ids:
                ch_url = (
                    f"https://www.youtube.com/channel/{ch_id}"
                    f"/search?query={urllib.parse.quote(query)}"
                )
                try:
                    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
                        ch_info = ydl.extract_info(ch_url, download=False)
                    ch_entries = (ch_info.get("entries") or [])[:limit]
                except Exception:
                    logger.exception("Error searching channel %s for %r", ch_id, query)
                    continue
                total += len(ch_entries)
                for e in ch_entries:
                    if not e or "id" not in e:
                        continue
                    yt_ch = e.get("channel_id") or e.get("uploader_id") or ch_id
                    conn.execute(
                        "INSERT OR IGNORE INTO videos "
                        "(video_id, title, channel_id, url, source, status, query) "
                        "VALUES (?, ?, ?, ?, 'search', 'candidate', ?)",
                        (
                            e["id"],
                            e.get("title", ""),
                            yt_ch,
                            e.get("webpage_url") or e.get("url") or f"https://youtu.be/{e['id']}",
                            query,
                        ),
                    )
                    inserted += 1
            conn.commit()
            logger.info("Search %r (per-channel): %d/%d results inserted", query, inserted, total)
        else:
            with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            entries = info.get("entries", []) or []
            inserted = 0
            for e in entries:
                if not e or "id" not in e:
                    continue
                yt_ch = e.get("channel_id") or e.get("uploader_id")
                conn.execute(
                    "INSERT OR IGNORE INTO videos "
                    "(video_id, title, channel_id, url, source, status, query) "
                    "VALUES (?, ?, ?, ?, 'search', 'candidate', ?)",
                    (
                        e["id"],
                        e.get("title", ""),
                        yt_ch or "search",
                        e.get("webpage_url") or e.get("url") or f"https://youtu.be/{e['id']}",
                        query,
                    ),
                )
                inserted += 1
            conn.commit()
            logger.info("Search %r: %d/%d results kept", query, inserted, len(entries))
