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
        # Accumulate segments until ~2400 chars
        window, length = [], 0
        j = i
        while j < len(segments) and length < 2400:
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

        # ~300 char overlap: step back enough segments to re-cover ~300 chars
        overlap, back = 0, 0
        for seg in reversed(window):
            seg_len = len(seg.get("text", ""))
            if overlap + seg_len > 300:
                break
            overlap += seg_len
            back += 1

        # Advance at least one segment to avoid infinite loop
        i = j - max(back, 0) if j - max(back, 0) > i else i + 1

    return chunks
