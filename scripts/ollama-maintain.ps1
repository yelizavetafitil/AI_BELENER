# Ollama: list / running / unload RAM / prune disk
#   .\scripts\ollama-maintain.ps1 -List
#   .\scripts\ollama-maintain.ps1 -Running
#   .\scripts\ollama-maintain.ps1 -UnloadAll
#   .\scripts\ollama-maintain.ps1 -Prune

param(
    [switch]$List,
    [switch]$Running,
    [switch]$UnloadAll,
    [switch]$Prune
)

$ErrorActionPreference = "Continue"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Ollama-Exec {
    param([Parameter(Mandatory)][string[]]$Cmd)
    docker compose exec -T ollama ollama @Cmd
}

docker compose ps ollama 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker not running. Run: docker compose up -d"
    exit 1
}

$KeepTags = @("qwen2.5vl:7b", "gemma4:e4b")
$PruneTags = @(
    "qwen2.5vl:7b-q8_0",
    "qwen2.5vl:32b",
    "gemma4:26b",
    "gemma3:4b",
    "llava",
    "llama3.2-vision",
    "moondream",
    "bakllava"
)

$did = $false
if ($List) { $did = $true
    Write-Host "`n=== ollama list ===" -ForegroundColor Cyan
    Ollama-Exec -Cmd @("list")
}
if ($Running) { $did = $true
    Write-Host "`n=== ollama ps ===" -ForegroundColor Cyan
    Ollama-Exec -Cmd @("ps")
}
if ($UnloadAll) { $did = $true
    Write-Host "`n=== unload RAM ===" -ForegroundColor Yellow
    $psOut = docker compose exec -T ollama ollama ps 2>&1
    Write-Host $psOut
    $names = @()
    foreach ($line in ($psOut -split "`n")) {
        if ($line -match "NAME\s+ID") { continue }
        if ($line -match "^(\S+:\S+)") {
            $n = $Matches[1]
            if ($names -notcontains $n) { $names += $n }
        }
    }
    foreach ($n in $names) {
        Write-Host "stop: $n"
        Ollama-Exec -Cmd @("stop", $n)
    }
    Write-Host "`n=== ollama ps (after) ===" -ForegroundColor Green
    Ollama-Exec -Cmd @("ps")
}
if ($Prune) { $did = $true
    Write-Host "`n=== prune disk ===" -ForegroundColor Magenta
    $listOut = docker compose exec -T ollama ollama list 2>&1 | Out-String
    Write-Host $listOut
    foreach ($tag in $PruneTags) {
        if ($listOut -match [regex]::Escape($tag)) {
            Write-Host "rm: $tag"
            Ollama-Exec -Cmd @("rm", $tag)
        }
    }
    Write-Host "`nKeep for BelnipAI: $($KeepTags -join ', ')" -ForegroundColor Green
    Ollama-Exec -Cmd @("list")
}

if (-not $did) {
    Write-Host "Usage: -List | -Running | -UnloadAll | -Prune"
    Write-Host "Example: .\scripts\ollama-maintain.ps1 -List -Running"
}
