# Finload Dev Setup (Windows)
# Run from PowerShell: .\scripts\setup-dev.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== Finload Dev Setup (Windows) ===" -ForegroundColor Cyan

# ── mpv ────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "→ Checking for mpv (required for audio playback)..." -ForegroundColor Yellow
$mpvDll = $null
$mpvSearchPaths = @(
    "C:\Program Files\mpv\mpv-2.dll",
    "C:\Program Files (x86)\mpv\mpv-2.dll",
    "C:\ProgramData\chocolatey\lib\mpv.install\tools\mpv-2.dll",
    "C:\ProgramData\scoop\apps\mpv\current\mpv-2.dll"
)
foreach ($p in $mpvSearchPaths) {
    if (Test-Path $p) { $mpvDll = $p; break }
}
if (-not $mpvDll) {
    Write-Host "  mpv not found. Installing via winget..." -ForegroundColor Yellow
    winget install --id mpv.mpv -e --silent
    # Re-check after install
    foreach ($p in $mpvSearchPaths) {
        if (Test-Path $p) { $mpvDll = $p; break }
    }
}
if ($mpvDll) {
    Write-Host "  Found mpv-2.dll at: $mpvDll" -ForegroundColor Green
} else {
    Write-Host "  WARNING: mpv-2.dll still not found. Audio will not work in the bundled app." -ForegroundColor Red
    Write-Host "  Install mpv manually from https://mpv.io/installation/ and add it to PATH." -ForegroundColor Red
}

# ── Rust ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "→ Checking for Rust..." -ForegroundColor Yellow
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "  Rust not found. Download and run rustup from https://rustup.rs/" -ForegroundColor Red
    Write-Host "  After installing Rust, re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host "  Rust found: $(cargo --version)" -ForegroundColor Green

# ── WebView2 ───────────────────────────────────────────────────────────────────
# WebView2 is bundled with Windows 11 and recent Windows 10 builds.
# Tauri will prompt the user to install it if missing at runtime.

# ── Python venv ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "→ Setting up Python virtual environment..." -ForegroundColor Yellow
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
python -m venv src-backend\.venv
& src-backend\.venv\Scripts\pip install --upgrade pip -q
& src-backend\.venv\Scripts\pip install -r src-backend\requirements.txt -q
Write-Host "  Done." -ForegroundColor Green

# ── Node packages ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "→ Installing Node.js packages..." -ForegroundColor Yellow
npm install
Write-Host "  Done." -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start development:"
Write-Host "  npm run dev:tauri"
