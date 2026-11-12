# Install MinerU into its own Python 3.12 venv (.local\mineru), separate from backend\.venv.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\setup-mineru.ps1
$ErrorActionPreference = 'Stop'
python -m pip install -q uv
python -m uv venv --python 3.12 .local\mineru
python -m uv pip install --python .local\mineru\Scripts\python.exe -U "mineru>=4.0,<5"
Write-Host "Done. Start the service with:"
Write-Host "  .local\mineru\Scripts\mineru-kit.exe api-server --host 127.0.0.1 --port 8765 --tier standard"
Write-Host "First parse downloads models (a few minutes). PaperDesk falls back to the built-in parser when the service is not running."
