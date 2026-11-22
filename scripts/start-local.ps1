$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$logDirectory = Join-Path $projectRoot '.local\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

# The bundled development tools may not be on PATH in an ordinary terminal.
$bundledRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
foreach ($relative in @('node\bin', 'bin\fallback')) {
    $toolDirectory = Join-Path $bundledRuntime $relative
    if (Test-Path -LiteralPath $toolDirectory) {
        $env:PATH = $toolDirectory + [IO.Path]::PathSeparator + $env:PATH
    }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw 'Node and pnpm are required. See README.md for installation instructions.'
}
$shellExecutable = (Get-Process -Id $PID).Path

function Start-LocalService([int]$Port, [string]$Name, [string]$Script) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host "$Name already has a listener on port $Port; leaving it running."
        return
    }
    $scriptPath = Join-Path $PSScriptRoot $Script
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $scriptPath + '"'))
    $process = Start-Process -FilePath $shellExecutable -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "$Name.log") -RedirectStandardError (Join-Path $logDirectory "$Name.error.log") -PassThru
    Write-Host "Started $Name (PID $($process.Id))."
}

Start-LocalService 8000 'api' 'start-api.ps1'
Start-LocalService 5173 'web' 'start-web.ps1'

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 2
        $page = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2
        $proxy = Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/v1/health' -TimeoutSec 2
        if ($health.status -eq 'ok' -and $page.StatusCode -eq 200 -and $proxy.status -eq 'ok') {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    throw "Local services did not become ready. Check logs in $logDirectory."
}
Write-Host 'WorkLedger is ready: http://127.0.0.1:5173'
Write-Host 'Services run in the background. They need starting again after a restart.'
