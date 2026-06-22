"""Time-aware chunking: splits transcript segments into overlapping text windows."""
import json
from pathlib import Path


def chunk(video: dict) -> list[dict]:
    video_id = video["video_id"]
    transcript = json.loads(
        Path(f"data/transcripts/{video_id}.json").read_text(encoding="utf-8")
    )
    segments = transcript.get("segments", [])
    chunks = []
    i = 0

    while i < len(segments):
        # Accumulate segments until ~2400 chars. Always take at least one
        # segment so a single >2400-char segment still forms its own chunk
        # rather than stalling.
        window, length = [], 0
        j = i
        while j < len(segments) and (not window or length < 2400):
            seg = segments[j]
            window.append(seg)
            length += len(seg.get("text", ""))
            j += 1

        if not window:
            break

        chunks.append({
            "chunk_id": f"{video_id}:{len(chunks)}",
            "video_id": video_id,
            "start": float(window[0].get("start", 0)),
            "end": float(window[-1].get("end", 0)),
            "text": " ".join(s.get("text", "") for s in window),
        })

        # ~300 char overlap: step back enough segments to re-cover ~300 chars,
        # but never back past the window's first segment.
        overlap, back = 0, 0
        for seg in reversed(window):
            seg_len = len(seg.get("text", ""))
            if overlap + seg_len > 300 or back >= len(window) - 1:
                break
            overlap += seg_len
            back += 1

        # Guarantee forward progress: advance at least one segment past i.
        next_i = j - back
        i = next_i if next_i > i else i + 1

    return chunks
