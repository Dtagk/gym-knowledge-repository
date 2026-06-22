"""Synthesize cross-video answers by calling the RAG server for curated questions."""
import json
import os
from pathlib import Path

import httpx
import yaml

_CONFIG = Path(__file__).parent.parent / "config" / "synthesis_questions.yaml"
_OUTPUT = Path("docs/data/synthesis.json")
_RAG_URL = os.environ.get("RAG_URL", "http://localhost:8000")


def synthesize() -> None:
    with open(_CONFIG, encoding="utf-8") as f:
        questions = yaml.safe_load(f)["questions"]

    results = []
    for q in questions:
        try:
            resp = httpx.post(
                f"{_RAG_URL}/ask",
                json={"query": q, "limit": 5},
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
            sources = [
                {"title": s["title"], "url": s["url"]}
                for s in data.get("sources", [])[:3]
            ]
            results.append({"question": q, "answer": data["answer"], "sources": sources})
            print(f"OK: {q[:60]}")
        except Exception as e:
            print(f"SKIP ({e}): {q[:60]}")

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(results)} synthesis cards to {_OUTPUT}")


if __name__ == "__main__":
    synthesize()
