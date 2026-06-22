#!/usr/bin/env python3
"""Drive Epic 1 stories sequentially — one aider agent per story.

Prereq: aider in PATH (uv tool install aider-chat), Ollama running with qwen-coder-32768

Usage:
  python scripts/run_epic1_aider.py
  python scripts/run_epic1_aider.py --model ollama/qwen-coder-32768:latest
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
PROMPT_DIR = REPO_ROOT / "_bmad-output" / "dev-briefs" / "story-prompts"

STORIES = [
    {"prompt": "1.2-download.md",     "files": ["yt_kg/download.py"]},
    {"prompt": "1.3-transcribe.md",   "files": ["yt_kg/transcribe.py"]},
    {"prompt": "1.4-chunk-embed.md",  "files": ["yt_kg/chunk.py", "yt_kg/embed.py"]},
    {"prompt": "1.5-orchestrator.md", "files": ["scripts/run_pipeline.py", "scripts/__init__.py"]},
]


def main() -> None:
    p = argparse.ArgumentParser(description="Run Epic 1 stories with aider")
    p.add_argument("--model", default="ollama/qwen-coder-32768:latest", help="Aider model")
    args = p.parse_args()

    for story in STORIES:
        prompt_path = PROMPT_DIR / story["prompt"]
        if not prompt_path.exists():
            print(f"[!] Prompt not found: {prompt_path}", file=sys.stderr)
            sys.exit(1)

        message = prompt_path.read_text(encoding="utf-8")
        print(f"\n=== Running: {story['prompt']} ===")

        r = subprocess.run(
            ["aider", "--model", args.model, "--yes", "--no-auto-commits",
             "--message", message, *story["files"]],
            cwd=REPO_ROOT,
        )
        if r.returncode != 0:
            print(f"[x] Aider exited with error for {story['prompt']} — stopping.", file=sys.stderr)
            sys.exit(1)

        print(f"=== Done: {story['prompt']} ===")

    print("\nAll Epic 1 stories complete. Run: python scripts/run_pipeline.py")


if __name__ == "__main__":
    main()
