# Create cache dirs on fast SSD (default G:\BelenerCache).
# Run: .\scripts\setup_ssd_cache.ps1

param(
    [string]$Root = $(if ($env:BELENER_SSD_ROOT) { $env:BELENER_SSD_ROOT } else { "G:\BelenerCache" })
)

$dirs = @(
    "$Root\zone_render",
    "$Root\paddle_models",
    "$Root\surya_models",
    "$Root\ollama",
    "$Root\yolo_cache",
    "$Root\tmp"
)

foreach ($d in $dirs) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    Write-Host "OK $d"
}

$envFile = Join-Path $PSScriptRoot "..\.env"
if (Test-Path $envFile) {
    $content = Get-Content $envFile -Raw
    if ($content -notmatch "BELENER_SSD_ROOT=") {
        $line = "BELENER_SSD_ROOT=$($Root -replace '\\','/')"
        Add-Content $envFile $line
        Write-Host "Added BELENER_SSD_ROOT to .env"
    }
}

Write-Host ""
Write-Host "Start GOST stack (SSD, no vision):"
Write-Host "  .\scripts\up_gost_stack.ps1"
Write-Host ""
Write-Host "Start full stack (SSD + vision):"
Write-Host "  .\scripts\up_fast_stack.ps1 -PullVision"
