# Run backend tests (npm run backend:test). Keep this file ASCII-only (PowerShell 5.1 + ANSI).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$py = Join-Path $backend '.venv\Scripts\python.exe'
Push-Location $backend
try { & $py -m pytest -q } finally { Pop-Location }
