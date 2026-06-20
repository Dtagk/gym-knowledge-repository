"""Filter stage: marks videos as skipped based on per-channel config rules."""
import logging
from datetime import datetime
from pathlib import Path

import yaml
import yt_dlp

from .db import init_db

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "channels.yaml"


def filter_videos() -> None:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        channels = yaml.safe_load(f).get("channels", [])

    channel_filters = {ch["id"]: ch.get("filter") for ch in channels if "filter" in ch}

    conn = init_db()
    rows = conn.execute(
        "SELECT video_id, channel_id, url FROM videos WHERE downloaded_at IS NULL AND skipped = 0"
    ).fetchall()

    for row in rows:
        vid_id, chan_id, url = row["video_id"], row["channel_id"], row["url"]
        filt = channel_filters.get(chan_id)
        if not filt:
            continue

        try:
            opts = {"quiet": True, "no_warnings": True, "extract_flat": False}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            duration = info.get("duration") or 0
            upload_date = info.get("upload_date", "")
            views = info.get("view_count") or 0
            title = info.get("title", "")

            reason = None

            if "min_duration_seconds" in filt and duration < filt["min_duration_seconds"]:
                reason = f"duration {duration}s < min {filt['min_duration_seconds']}s"
            elif "max_duration_seconds" in filt and duration > filt["max_duration_seconds"]:
                reason = f"duration {duration}s > max {filt['max_duration_seconds']}s"
            elif "max_age_days" in filt and upload_date:
                try:
                    age = (datetime.now() - datetime.strptime(upload_date, "%Y%m%d")).days
                    if age > filt["max_age_days"]:
                        reason = f"age {age}d > max {filt['max_age_days']}d"
                except ValueError:
                    pass

            if not reason and "min_views" in filt and views < filt["min_views"]:
                reason = f"views {views} < min {filt['min_views']}"

            if not reason and filt.get("title_include"):
                if not any(p.lower() in title.lower() for p in filt["title_include"]):
                    reason = "title does not match any include pattern"

            if not reason and filt.get("title_exclude"):
                matched = next((p for p in filt["title_exclude"] if p.lower() in title.lower()), None)
                if matched:
                    reason = f"title matches exclude pattern '{matched}'"

            if reason:
                logger.info("skipping %s: %s", vid_id, reason)
                conn.execute(
                    "UPDATE videos SET skipped = 1, skip_reason = ? WHERE video_id = ?",
                    (reason, vid_id),
                )
                conn.commit()

        except Exception as exc:
            reason = f"metadata fetch failed: {exc}"
            logger.warning("filter error for %s: %s", vid_id, exc)
            conn.execute(
                "UPDATE videos SET skipped = 1, skip_reason = ? WHERE video_id = ?",
                (reason, vid_id),
            )
            conn.commit()

    conn.close()
    logger.info("filter complete")
