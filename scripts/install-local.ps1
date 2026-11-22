$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:UV_PROJECT_ENVIRONMENT = '.venv-ai'
uv sync --frozen --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'AI runtime installation failed.' }
pnpm --dir apps/web install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
& .\scripts\install-models.ps1
