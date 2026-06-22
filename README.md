# Gym Knowledge Repository

A personal knowledge base of gym/training YouTube videos — tagged, graphed, searchable.

**Live browser:** [https://dtagk.github.io/gym-knowledge-repository/](https://dtagk.github.io/gym-knowledge-repository/)]
## What it does

Ingests YouTube videos from curated channels → transcribes → extracts entities (muscles, exercises, concepts) into a knowledge graph (Kuzu) and vector store (LanceDB) → classifies by body part + training goal → exports to a static GitHub Pages browser.

## Pipeline stages

```text
discover → download → transcribe → extract → embed → graph → classify → export → synthesize
```

Run each stage:

```bash
python -m yt_kg.discover
python -m yt_kg.download
python -m yt_kg.transcribe
python -m yt_kg.extract
python -m yt_kg.embed
python -m yt_kg.graph
python -m yt_kg.classify
python -m yt_kg.export
python -m yt_kg.synthesize   # requires RAG server running
```

## RAG Server

Semantic search + Q&A over the corpus:

```bash
# Start (point DATA_DIR at the repo root where data/ lives)
DATA_DIR=/path/to/gym-knowledge-repository \
  uvicorn yt_kg.rag_server:app --reload --port 8000

# Semantic search (no LLM)
curl "http://localhost:8000/search?q=shoulder+mobility&limit=5"

# Q&A with citations (requires Ollama + gpt-oss:20b)
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "best exercises for shoulder mobility", "limit": 5}'
```

## Docker Compose (Ollama)

```bash
docker compose up -d ollama
docker exec ollama ollama pull gpt-oss:20b
```

## Setup

```bash
conda env create -f environment.yml
conda activate gym-kg
pip install -e .
```

## Stack

- **LanceDB** — vector store (384-dim bge-small-en-v1.5 embeddings)
- **Kuzu** — graph DB (Entity, Video, Chunk nodes; MENTIONS, APPEARS_IN, RELATED edges)
- **SQLite** — video metadata and pipeline state
- **Ollama** — local LLM (qwen2.5-coder:7b for classify, gpt-oss:20b for RAG)
- **FastAPI** — RAG server (`/search`, `/ask`)
- **GitHub Pages** — static browser (`docs/`)
