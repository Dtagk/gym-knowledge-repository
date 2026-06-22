"""Local transcription stage: faster-whisper, GPU with CPU fallback."""
import json
import logging
from pathlib import Path

from faster_whisper import WhisperModel
from tqdm import tqdm

from .db import init_db, utcnow

logger = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path("data/transcripts")


def _load_model() -> WhisperModel:
    try:
        import torch
        if torch.cuda.is_available():
            return WhisperModel("medium", device="cuda", compute_type="float16")
    except Exception:
        pass
    return WhisperModel("small", device="cpu", compute_type="int8")


def transcribe(workers: int = 1) -> None:  # ponytail: workers reserved — GPU Whisper is inherently sequential
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id FROM videos WHERE downloaded_at IS NOT NULL AND transcribed_at IS NULL AND skipped = 0"
    ).fetchall()

    if not rows:
        return

    model = _load_model()

    with tqdm(rows, desc="Transcribing", unit="video", position=0) as global_bar:
        for row in global_bar:
            video_id = row["video_id"]
            audio_path = Path(f"data/audio/{video_id}.m4a")
            global_bar.set_postfix(video=video_id)
            try:
                segments, info = model.transcribe(str(audio_path), vad_filter=True)
                duration = info.duration or 1.0

                result_segments = []
                prev_end = 0.0
                with tqdm(
                    total=round(duration),
                    desc=video_id,
                    unit="s",
                    position=1,
                    leave=False,
                ) as file_bar:
                    for seg in segments:
                        result_segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
                        file_bar.update(round(seg.end - prev_end))
                        prev_end = seg.end

                transcript = {
                    "language": info.language,
                    "duration": duration,
                    "segments": result_segments,
                }
                TRANSCRIPTS_DIR.joinpath(f"{video_id}.json").write_text(
                    json.dumps(transcript), encoding="utf-8"
                )
                conn.execute(
                    "UPDATE videos SET transcribed_at=? WHERE video_id=?",
                    (utcnow(), video_id),
                )
                conn.commit()
                audio_path.unlink(missing_ok=True)
                logger.info("transcribed %s (%.0fs, lang=%s)", video_id, duration, info.language)
            except Exception as e:
                conn.execute(
                    "UPDATE videos SET last_error=?, error_stage='transcribe' WHERE video_id=?",
                    (str(e), video_id),
                )
                conn.commit()
                logger.error("transcribe failed %s: %s", video_id, e)


if __name__ == "__main__":
    transcribe()
