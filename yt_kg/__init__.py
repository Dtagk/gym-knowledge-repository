"""Package init.

On native Windows the pipeline depends on ffmpeg (faster-whisper) and Node
(some MCP tooling). When those live in non-default WinGet locations they may be
absent from a fresh shell's PATH. We inject them here, but only:

  - on Windows, and
  - when YT_KG_INJECT_PATH is set (opt-in), so importing the package on Linux/CI
    or another machine has no side effects.

Paths are taken from YT_KG_FFMPEG_BIN / YT_KG_NODE_BIN if provided, else fall
back to the original developer defaults.
"""
import os
import platform
from pathlib import Path

_DEFAULTS = {
    "YT_KG_NODE_BIN": r"C:\Program Files\nodejs",
    "YT_KG_FFMPEG_BIN": str(
        Path(
            r"C:\Users\User\AppData\Local\Microsoft\WinGet\Packages"
            r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            r"\ffmpeg-8.1.1-full_build\bin"
        )
    ),
}


def _inject_path() -> None:
    if platform.system() != "Windows":
        return
    if os.environ.get("YT_KG_INJECT_PATH", "").lower() not in ("1", "true", "yes"):
        return
    for var, default in _DEFAULTS.items():
        p = os.environ.get(var, default)
        if p and p not in os.environ.get("PATH", "") and Path(p).exists():
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


_inject_path()
