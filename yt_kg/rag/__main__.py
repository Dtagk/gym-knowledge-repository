"""CLI for the RAG server.

Usage:
    python -m yt_kg.rag "question text"
    python -m yt_kg.rag --limit 10 "question text"
"""
import argparse
import sys

import httpx

_RAG_URL = "http://localhost:8000/ask"
_START_HINT = "uvicorn yt_kg.rag_server:app --port 8000"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query the local RAG server for fitness knowledge.",
    )
    parser.add_argument("question", help="Question to ask the RAG server.")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of chunks to retrieve (default: 5).",
    )
    args = parser.parse_args()

    try:
        resp = httpx.post(
            _RAG_URL,
            json={"query": args.question, "limit": args.limit},
            timeout=90.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        print(f"RAG server not running — start it with: {_START_HINT}")
        sys.exit(1)

    data = resp.json()

    print(data["answer"])

    sources = data.get("sources", [])
    if sources:
        print()
        for i, src in enumerate(sources, 1):
            print(f"{i}. {src['title']} — {src['url']}")


if __name__ == "__main__":
    main()
