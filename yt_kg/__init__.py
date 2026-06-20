import os
from pathlib import Path

_INJECT = [
    r"C:\Program Files\nodejs",
    str(Path(r"C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin")),
]
for _p in _INJECT:
    if _p not in os.environ.get("PATH", "") and Path(_p).exists():
        os.environ["PATH"] = _p + os.pathsep + os.environ["PATH"]
