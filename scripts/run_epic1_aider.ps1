# Drives Epic 1 stories sequentially — one aider agent per story.
# Usage: .\scripts\run_epic1_aider.ps1
# Prereq: aider in PATH (uv tool install aider-chat), Ollama running with qwen-coder-32768

param(
    [string]$Model = "ollama/qwen-coder-32768:latest",
    [string]$RepoRoot = $PSScriptRoot + "\.."
)

$env:PATH = "C:\Users\User\.local\bin;" + $env:PATH
$promptDir = "$RepoRoot\_bmad-output\dev-briefs\story-prompts"

$stories = @(
    @{ prompt = "1.2-download.md";    files = "yt_kg/download.py" },
    @{ prompt = "1.3-transcribe.md";  files = "yt_kg/transcribe.py" },
    @{ prompt = "1.4-chunk-embed.md"; files = "yt_kg/chunk.py yt_kg/embed.py" },
    @{ prompt = "1.5-orchestrator.md"; files = "scripts/run_pipeline.py scripts/__init__.py" }
)

foreach ($story in $stories) {
    $promptFile = "$promptDir\$($story.prompt)"
    $message = Get-Content $promptFile -Raw

    Write-Host "`n=== Running: $($story.prompt) ===" -ForegroundColor Cyan

    $fileArgs = $story.files -split " "
    & aider --model $Model --yes --no-auto-commits --message $message @fileArgs

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Aider exited with error for $($story.prompt) — stopping." -ForegroundColor Red
        exit 1
    }

    Write-Host "=== Done: $($story.prompt) ===" -ForegroundColor Green
}

Write-Host "`nAll Epic 1 stories complete. Run: python scripts/run_pipeline.py" -ForegroundColor Yellow
