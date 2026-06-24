#!/usr/bin/env python3
"""Seed all required Ollama models into the running Docker container.

Run once after: docker compose up -d
Total download: ~64 GB on first run; subsequent runs skip already-pulled models.

Usage:
  python scripts/ollama_init.py
  python scripts/ollama_init.py --container my-ollama --no-coder
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

MODELS = [
    "nomic-embed-text",
    "gpt-oss:20b",
    "qwen3.6:35b-a3b",
    "qwen2.5-coder:7b",
]
MODELFILE = Path(__file__).resolve().parent.parent / "docker" / "qwen-coder.Modelfile"
CODER_VARIANT = "qwen-coder-32768"


def info(m): print(f"\033[36m[ollama-init] {m}\033[0m", flush=True)
def ok(m):   print(f"\033[32m[ollama-init] ✓ {m}\033[0m", flush=True)
def die(m):  print(f"\033[31m[ollama-init] ✗ {m}\033[0m", flush=True); sys.exit(1)


def wait_for_container(container: str, attempts: int = 30) -> None:
    info("Waiting for Ollama to be ready…")
    for i in range(attempts):
        r = subprocess.run(["docker", "exec", container, "ollama", "list"], capture_output=True)
        if r.returncode == 0:
            ok("Ollama is ready.")
            return
        time.sleep(2)
    die(f"Ollama did not become ready after {attempts * 2}s. Is the container running?")


def pull_models(container: str) -> None:
    info(f"Pulling {len(MODELS)} models (first run downloads ~64 GB)…")
    for model in MODELS:
        info(f"  pulling {model}…")
        subprocess.run(["docker", "exec", container, "ollama", "pull", model], check=True)
        ok(f"  {model} ready.")


def build_coder_variant(container: str) -> None:
    if not MODELFILE.exists():
        die(f"Modelfile not found: {MODELFILE}")
    info(f"Building coder variant '{CODER_VARIANT}' (num_ctx=32768)…")
    subprocess.run(
        ["docker", "cp", str(MODELFILE), f"{container}:/tmp/qwen-coder.Modelfile"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", container, "ollama", "create", CODER_VARIANT, "-f", "/tmp/qwen-coder.Modelfile"],
        check=True,
    )
    ok(f"Built {CODER_VARIANT}.")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed Ollama models into Docker container")
    p.add_argument("--container", default="ollama-local", help="Container name (default: ollama-local)")
    p.add_argument("--no-coder",  action="store_true",   help="Skip building qwen-coder-32768 variant")
    args = p.parse_args()

    wait_for_container(args.container)
    pull_models(args.container)
    if not args.no_coder:
        build_coder_variant(args.container)

    print()
    ok("Done. Installed models:")
    subprocess.run(["docker", "exec", args.container, "ollama", "list"])


if __name__ == "__main__":
    main()
