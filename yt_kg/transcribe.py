"""Local transcription stage: faster-whisper, GPU with CPU fallback."""
import json
import logging
from pathlib import Path

from faster_whisper import WhisperModel

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


def transcribe() -> None:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_db()
    rows = conn.execute(
        "SELECT video_id FROM videos WHERE downloaded_at IS NOT NULL AND transcribed_at IS NULL"
    ).fetchall()

    if not rows:
        return

    model = _load_model()

    for row in rows:
        video_id = row["video_id"]
        audio_path = Path(f"data/audio/{video_id}.m4a")
        try:
            segments, info = model.transcribe(str(audio_path), vad_filter=True)
            transcript = {
                "language": info.language,
                "duration": info.duration,
                "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in segments],
            }
            TRANSCRIPTS_DIR.joinpath(f"{video_id}.json").write_text(
                json.dumps(transcript), encoding="utf-8"
            )
            audio_path.unlink(missing_ok=True)
            conn.execute(
                "UPDATE videos SET transcribed_at=? WHERE video_id=?",
                (utcnow(), video_id),
            )
            conn.commit()
            logger.info("transcribed %s (%.0fs, lang=%s)", video_id, info.duration, info.language)
        except Exception as e:
            conn.execute(
                "UPDATE videos SET last_error=?, error_stage='transcribe' WHERE video_id=?",
                (str(e), video_id),
            )
            conn.commit()
            logger.error("transcribe failed %s: %s", video_id, e)
