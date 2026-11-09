$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$RuntimeDll = Join-Path $env:SystemRoot 'System32\vcruntime140_1.dll'
$RuntimeUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}

function Get-PythonPath {
    $launchers = @(@('py', '-3.12'), @('py', '-3.13'), @('py', '-3.11'), @('python'))
    foreach ($launcher in $launchers) {
        $command = Get-Command $launcher[0] -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $extra = @($launcher | Select-Object -Skip 1)
        $path = & $command.Source @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($path) { return $path }
    }
    return $null
}

$python = if ($env:PYTHON) { $env:PYTHON } else { Get-PythonPath }
if (-not $python) {
    throw 'Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and run this again, or set PYTHON to its path.'
}
Write-Host "Using $(& $python --version) at $python"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is needed for the desktop app and is not installed. Install Node.js from https://nodejs.org/ and run this again.'
}

Write-Host '==> Creating .venv'
if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
Invoke-Checked $python @('-m', 'venv', '.venv')
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip', 'wheel')

Write-Host '==> Installing Python dependencies (~500 MB the first time)'
Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', 'server\requirements-windows.txt')

Write-Host '==> Fetching the Kokoro voice model (~340 MB)'
& (Join-Path $PSScriptRoot 'download-kokoro.ps1')

Write-Host '==> Installing desktop app dependencies'
Push-Location app
try { Invoke-Checked 'npm' @('install') } finally { Pop-Location }

Write-Host '==> Settings'
if (-not (Test-Path arty_config.yaml)) {
    Copy-Item arty_config.example.yaml arty_config.yaml
}
$configText = Get-Content arty_config.yaml -Raw
if ($env:ARTY_API_KEY) {
    $pattern = '(?m)^(\s*api_key:)\s*"[^"]*"(.*)$'
    $replacement = '${1} "' + $env:ARTY_API_KEY + '"${2}'
    $updated = [regex]::new($pattern).Replace($configText, $replacement, 1)
    Set-Content -Path arty_config.yaml -Value $updated -NoNewline
    Write-Host '   saved the Arty API key'
} elseif ($configText -match 'api_key: ""') {
    Write-Host '   arty_config.yaml has no API key yet: Arty asks for it when it starts (or run again with ARTY_API_KEY set)'
}

if (-not (Test-Path $RuntimeDll)) {
    Write-Host ''
    Write-Host 'The Microsoft Visual C++ runtime is missing, which the speech engine needs. Install it from:'
    Write-Host "  $RuntimeUrl"
}

Write-Host ''
Write-Host 'Done. Start Arty with:'
Write-Host '  cd app; npm start'
