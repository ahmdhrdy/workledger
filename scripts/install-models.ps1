$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv-ai\Scripts\python.exe')) {
    throw 'Run scripts/install-local.ps1 first to create the Python 3.12 AI runtime.'
}
& .\.venv-ai\Scripts\python.exe -m workledger.vision.downloads
if ($LASTEXITCODE -ne 0) { throw 'Model installation failed; no partial model will be used.' }
& .\.venv-ai\Scripts\python.exe -m workledger.vision.selftest
