$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$base = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'
$directory = Join-Path $PSScriptRoot 'models\kokoro'
New-Item -ItemType Directory -Force -Path $directory | Out-Null

foreach ($name in 'kokoro-v1.0.onnx', 'voices-v1.0.bin') {
    $target = Join-Path $directory $name
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 0) {
        Write-Host "==> $name already present, skipping"
        continue
    }
    Write-Host "==> Downloading $name"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $target
}

Get-ChildItem $directory | Format-Table Name, Length
