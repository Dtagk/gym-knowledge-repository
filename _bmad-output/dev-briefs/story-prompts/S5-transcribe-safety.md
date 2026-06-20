# Sprint Fix S5: transcribe-safety

**Branch:** `fix/transcribe-safety`  
**Files:** `yt_kg/transcribe.py` only

## Issue to fix

### Audio file deleted before DB commit — data loss on crash

`yt_kg/transcribe.py` — the section that deletes the audio file after transcription.

Current order of operations (approximately):
```python
audio_path.unlink(missing_ok=True)   # audio deleted
conn.execute("UPDATE videos SET transcribed_at=? ...", ...)
conn.commit()
```

If the process is killed between `unlink` and `commit`, the audio is permanently gone but
`transcribed_at` stays NULL. The next pipeline run will find the video, try to transcribe it,
and fail with a `FileNotFoundError` from Whisper — with no indication the audio was lost rather
than never downloaded. The video is then stuck: `downloaded_at` is set but the audio file doesn't
exist.

**Fix:** Swap the order — commit the DB update first, then delete the audio:

```python
conn.execute("UPDATE videos SET transcribed_at=? WHERE video_id=?", (utcnow(), video_id))
conn.commit()
audio_path.unlink(missing_ok=True)
```

If killed after commit but before unlink, the audio file remains on disk — a harmless orphan
that `download.py` will skip (it checks `downloaded_at`). This is the safe side to fail on.

## How to find the right location

Read `yt_kg/transcribe.py` in full. Look for the call to `audio_path.unlink(missing_ok=True)`
and the surrounding `UPDATE videos SET transcribed_at` and `conn.commit()`. Move the unlink
to after the commit.

## Acceptance criteria

- `conn.commit()` for the `transcribed_at` update executes before `audio_path.unlink()`
- No other changes to the file
