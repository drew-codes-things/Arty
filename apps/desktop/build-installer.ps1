$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Set-Location $PSScriptRoot

$PythonVersion = '3.12.10'
$PythonZip = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZip"
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'
$WhisperModel = 'base.en'
$Cache = 'app\build\cache'
$Stage = 'app\build\backend'
$StagedPython = Join-Path $PSScriptRoot "$Stage\python\python.exe"
$System32 = Join-Path $env:SystemRoot 'System32'
$RuntimePatterns = 'vcruntime140*.dll', 'msvcp140*.dll', 'concrt140.dll', 'vcomp140.dll'
$RuntimeUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$Helper = Join-Path $PSScriptRoot 'prepare-windows-backend.py'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}

function Get-Cached {
    param([string]$Url, [string]$Name)
    $target = Join-Path $Cache $Name
    if (-not (Test-Path $target) -or (Get-Item $target).Length -eq 0) {
        Write-Host "==> Downloading $Name"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $target
    }
    return $target
}

function Copy-RuntimeDlls {
    param([string]$Destination)
    $found = $RuntimePatterns | ForEach-Object { Get-ChildItem -Path $System32 -Filter $_ -ErrorAction SilentlyContinue }
    if (-not ($found | Where-Object { $_.Name -eq 'vcruntime140_1.dll' })) {
        throw "The Microsoft Visual C++ runtime is not installed on this build machine. Install it from $RuntimeUrl and run this again."
    }
    foreach ($file in $found) {
        $signature = Get-AuthenticodeSignature $file.FullName
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            throw "$($file.Name) is not signed by Microsoft; not bundling it."
        }
        Copy-Item $file.FullName $Destination
    }
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is needed to build the installer.' }
if (-not (Test-Path 'models\kokoro\kokoro-v1.0.onnx') -or -not (Test-Path 'models\kokoro\voices-v1.0.bin')) {
    & (Join-Path $PSScriptRoot 'download-kokoro.ps1')
}

New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$zip = Get-Cached $PythonUrl $PythonZip
$getPip = Get-Cached $GetPipUrl 'get-pip.py'

Write-Host '==> Staging the backend'
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Expand-Archive -Path $zip -DestinationPath "$Stage\python"
Invoke-Checked $StagedPython @($Helper, 'enable-site', "$Stage\python")

Write-Host '==> Installing backend dependencies'
Invoke-Checked $StagedPython @($getPip, '--quiet', '--no-warn-script-location')
Invoke-Checked $StagedPython @('-m', 'pip', 'install', '--quiet', '--no-cache-dir', '--no-warn-script-location', '-r', 'server\requirements-windows.txt')

Write-Host "==> Fetching Whisper $WhisperModel"
Invoke-Checked $StagedPython @($Helper, 'whisper', "$Stage\models\whisper\$WhisperModel", $WhisperModel)

Write-Host '==> Trimming what the app will never run'
Invoke-Checked $StagedPython @($Helper, 'trim', "$Stage\python")

Write-Host '==> Bundling the Microsoft Visual C++ runtime next to Python'
Copy-RuntimeDlls "$Stage\python"

Write-Host '==> Copying the backend and its assets'
& robocopy server "$Stage\server" /E /XD __pycache__ tests /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
& robocopy models\kokoro "$Stage\models\kokoro" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
Copy-Item arty_config.example.yaml "$Stage\arty_config.example.yaml"

Write-Host '==> Building the installer'
Push-Location app
try {
    if (-not (Test-Path node_modules)) { Invoke-Checked 'npm' @('install') }
    Invoke-Checked 'npx' @('electron-builder', '--win', 'nsis', '--x64')
} finally {
    Pop-Location
}

$installer = Get-ChildItem app\dist\Arty-Setup-*.exe | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host ''
Write-Host 'Done.'
if ($installer) {
    Write-Host ("  {0} ({1:N0} MB)" -f $installer.FullName, ($installer.Length / 1MB))
} else {
    throw 'No installer was produced.'
}
