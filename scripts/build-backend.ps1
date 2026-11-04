# Build the Python backend into build\backend-dist\paperdesk-backend (PyInstaller, onedir).
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw "backend venv not found. Run: npm run backend:setup"
}
& $py -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "failed to install pyinstaller" }
}
Push-Location (Join-Path $root 'backend')
try {
    & $py -m PyInstaller paperdesk.spec --noconfirm --distpath (Join-Path $root 'build\backend-dist') --workpath (Join-Path $root 'build\backend-work')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
} finally {
    Pop-Location
}
Write-Host "backend built: build\backend-dist\paperdesk-backend"
