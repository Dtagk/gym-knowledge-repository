<#
.SYNOPSIS
    Seeds all required Ollama models into the running container.
.DESCRIPTION
    Run once after: docker compose up -d
    Total download: ~64 GB on first run; subsequent runs skip already-pulled models.
#>

$container = "ollama"

Write-Host "Waiting for Ollama to be ready..." -ForegroundColor Cyan
$tries = 0
do {
    Start-Sleep 2
    $tries++
    $out = docker exec $container ollama list 2>&1
} until ($LASTEXITCODE -eq 0 -or $tries -ge 30)

if ($tries -ge 30) {
    Write-Error "Ollama did not become ready in time. Is the container running?"
    exit 1
}

Write-Host "Pulling models (this will take a while on first run)..." -ForegroundColor Cyan

docker exec $container ollama pull nomic-embed-text
docker exec $container ollama pull gpt-oss:20b
docker exec $container ollama pull qwen3.6:35b-a3b
docker exec $container ollama pull qwen2.5-coder:7b

Write-Host "Building custom coder variant (32k context)..." -ForegroundColor Cyan
docker cp "$PSScriptRoot\..\docker\qwen-coder.Modelfile" "${container}:/tmp/qwen-coder.Modelfile"
docker exec $container ollama create qwen-coder-32768 -f /tmp/qwen-coder.Modelfile

Write-Host "`nDone. Installed models:" -ForegroundColor Green
docker exec $container ollama list
