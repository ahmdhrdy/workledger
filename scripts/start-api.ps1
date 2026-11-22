$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv-ai\Scripts\python.exe')) {
    throw 'Run scripts/install-local.ps1 first. See README.md.'
}
& .\.venv-ai\Scripts\python.exe -m uvicorn workledger.main:app --host 127.0.0.1 --port 8000
