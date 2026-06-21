# Installation Guide — Gym Knowledge Repository

**Hardware target:** RTX 5060 Ti (16 GB VRAM) + 32 GB DDR5 RAM, Windows 11, PowerShell 5.1+

Work top-to-bottom. Each section is idempotent — re-run safely.

> **Post "Reset this PC > Keep my files" note:**
> User files under `C:\Users\` survive the reset — that includes:
> - This repo at `C:\Users\User\Documents\Code\gym-knowledge-repository\`
> - Ollama model weights at `C:\Users\User\.ollama\models\` (~64 GB — **no re-download needed**)
> - Claude Code config and memory at `C:\Users\User\.claude\`
>
> What's gone: installed apps (Git, Python, Node, VS Code, Docker, Ollama app, NVIDIA drivers).
> Reinstall in order below; start with NVIDIA drivers.

---

## 0. NVIDIA Drivers (do this first)

GPU drivers are removed by the reset. Without them nothing GPU-related works (Docker passthrough, CUDA transcription, Ollama GPU offload).

⚠️ **User action:** Download and install the latest Game Ready or Studio driver for the RTX 5060 Ti from `nvidia.com/drivers`. Run the installer, choose Express install, reboot when prompted.

Verify after reboot:
```powershell
nvidia-smi
```
Should show the RTX 5060 Ti with 16 GB VRAM. Do not proceed until this passes.

---

## 1. System Prerequisites

### 1.1 Git

```powershell
winget install --id Git.Git -e
```
Restart terminal after. Verify: `git --version`

### 1.2 Node.js (LTS)
```powershell
winget install --id OpenJS.NodeJS.LTS -e
```
Restart terminal. Verify: `node --version`, `npm --version`

### 1.3 FFmpeg
```powershell
winget install --id Gyan.FFmpeg -e
```
> The VS Code settings expect it at the winget-managed path; no manual PATH edits needed if installed via winget.

Verify: `ffmpeg -version`

### 1.4 Miniconda (Python 3.11+)
```powershell
winget install --id Anaconda.Miniconda3 -e
```
Install to `C:\ProgramData\miniconda3` (the path baked into `.vscode/settings.json`).

Restart terminal. Verify: `conda --version`

### 1.5 Docker Desktop
Required to run Ollama in a container with GPU passthrough.

```powershell
winget install --id Docker.DockerDesktop -e
```
> Accept the license during install (user action required).

After install: launch Docker Desktop, go to **Settings → Resources → WSL Integration** and enable it, then **Settings → Docker Engine** and verify it starts. Keep it running in the tray.

Verify: `docker --version`, `docker compose version`

### 1.6 NVIDIA Container Toolkit (GPU passthrough for Docker)
Docker needs this to hand the GPU to containers.

In Docker Desktop: **Settings → Resources → GPU** — enable **"Use GPU with WSL 2"**.

If that setting is missing, install WSL 2 first:
```powershell
wsl --install
wsl --update
```
Then restart Docker Desktop.

Verify inside a container:
```powershell
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```
Should print your RTX 5060 Ti details.

---

## 2. Python Environment

### 2.1 Create conda env
```powershell
conda create -n gym-kg python=3.11 -y
conda activate gym-kg
```

### 2.2 Install project + dependencies
From the repo root:
```powershell
pip install -e ".[dev]"
```

This installs (from `pyproject.toml`):
| Package | Purpose |
|---|---|
| `yt-dlp` | YouTube video/audio discovery & download |
| `faster-whisper` | Local speech-to-text (CTranslate2 backend) |
| `sentence-transformers` | Local text embeddings (`bge-small-en-v1.5`) |
| `lancedb` | Embedded vector store |
| `pyyaml` | Config file parsing |
| `fastmcp` | MCP server SDK |
| `kuzu` | Embedded graph DB (Cypher-compatible) |
| `instructor` | Structured LLM output via Pydantic |
| `httpx` | HTTP client (OpenAlex API calls) |
| `pyarrow` | Columnar data (LanceDB + Parquet) |
| `gliner` | GLiNER NER model |
| `tqdm` | Progress bars |
| `pytest` / `black` / `ruff` | Dev tooling |

Verify:
```powershell
python -c "import faster_whisper, lancedb, kuzu, instructor; print('OK')"
```

### 2.3 CUDA support for faster-whisper
For GPU transcription (`float16`), you need the CUDA build of CTranslate2. If `pip install -e .` pulled the CPU build, reinstall:
```powershell
pip install ctranslate2 --extra-index-url https://download.pytorch.org/whl/cu121
```
Then verify:
```python
from faster_whisper import WhisperModel
m = WhisperModel("small", device="cuda", compute_type="float16")
print("GPU OK")
```

---

## 3. Ollama (Local LLM via Docker)

### 3.1 Start the container
From the repo root:
```powershell
docker compose up -d
```
This starts Ollama at `http://localhost:11434` with GPU passthrough and performance env vars already set (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`).

Verify:
```powershell
docker ps  # should show container "ollama" running
curl http://localhost:11434/api/tags
```

### 3.2 Pull models + build custom coder variant

> **After "Reset this PC > Keep my files":** model weights survived in `C:\Users\User\.ollama\models\`.
> Check first — if all 5 models are listed, skip the pull entirely:
> ```powershell
> ollama list  # or: docker exec ollama ollama list
> ```
> Only run `ollama_init.ps1` if models are missing.

> **Fresh machine / models missing:** downloads ~64 GB. Run on a fast connection.

```powershell
.\scripts\ollama_init.ps1
```

This pulls:
| Model | Role |
|---|---|
| `nomic-embed-text` | Embeddings for entity resolution |
| `gpt-oss:20b` | Research / reasoning model |
| `qwen3.6:35b-a3b` | Primary extraction model |
| `qwen2.5-coder:7b` | Code tasks |
| `qwen-coder-32768` | Custom variant: 32k context, tuned params |

Verify:
```powershell
docker exec ollama ollama list
```
All 5 entries should appear.

### 3.3 Smoke test
```powershell
docker exec ollama ollama run gpt-oss:20b "Reply with exactly: OK"
docker exec ollama ollama run qwen-coder-32768 "Reply with exactly: OK"
docker exec ollama ollama ps
```
In `ollama ps`, the research model should be ~100% GPU; the coder should show a CPU/GPU split (offload working). If either fails, check `docker logs ollama`.

---

## 4. Claude Code CLI

### 4.1 Install
```powershell
npm install -g @anthropic-ai/claude-code
```
Verify: `claude --version`

### 4.2 MCP server — Sequential Thinking
Structured multi-step reasoning. Fully local, no token required.
```powershell
claude mcp add sequential-thinking --scope user -- npx -y @modelcontextprotocol/server-sequential-thinking
```

### 4.3 MCP server — this project
Register the gym-kg MCP server so Claude can query the knowledge graph:
```powershell
claude mcp add gym-kg --scope project -- python scripts/start_mcp.py
```
> Run from the repo root. The server exposes `vector_search`, `cypher_query`, `expand_entity`, and `papers_for_topic` tools.

Verify both:
```powershell
claude mcp list
```

### 4.4 Plugins (optional but used in this project)
```powershell
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install superpowers@claude-plugins-official
claude plugin install duckdb-skills@claude-plugins-official
claude plugin install context7@claude-plugins-official
claude plugin install feature-dev@claude-plugins-official
claude plugin install code-review@claude-plugins-official
```
Verify: `claude plugin list`

---

## 5. VS Code

Install from `https://code.visualstudio.com/` if not present.

Required extension:
- **Python** (Microsoft) — `ms-python.python`

The workspace settings in `.vscode/settings.json` are already committed and point to the conda env at `C:\ProgramData\miniconda3`. After installing Miniconda to that path and creating the `gym-kg` env, VS Code should pick up the interpreter automatically.

Open the repo, then: `Ctrl+Shift+P` → **Python: Select Interpreter** → choose `gym-kg`.

---

## 6. Final Verification Checklist

```powershell
# System tools
git --version
node --version
ffmpeg -version
conda --version
docker --version

# Python env
conda activate gym-kg
python -c "import faster_whisper, lancedb, kuzu, instructor, gliner; print('Python deps OK')"

# Ollama
docker exec ollama ollama list

# Claude Code
claude --version
claude mcp list
```

Expected: no errors on any line.

---

## 7. Running the Pipeline

After all the above:

```powershell
conda activate gym-kg
python scripts/run_pipeline.py
```

Logs go to `pipeline.log` and `pipeline_err.log` in the repo root. Watch progress with `tqdm` in the terminal — do not run as a background process.

---

## Port / Endpoint Reference

| Service | Endpoint |
|---|---|
| Ollama | `http://localhost:11434/v1` (OpenAI-compatible) |
| Gym-KG MCP server | stdio (Claude Code manages the process) |
