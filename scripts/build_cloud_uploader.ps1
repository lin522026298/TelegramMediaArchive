param(
    [string]$Destination = "D:\Cloud Storage\Openlist"
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = "python"
}

$BuildRoot = Join-Path $Root "build-cloud-uploader"
$DistRoot = Join-Path $Root "dist-cloud-uploader"
foreach ($Path in @($BuildRoot, $DistRoot)) {
    $Resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $Resolved.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理工作区之外的路径：$Resolved"
    }
    if (Test-Path -LiteralPath $Resolved) {
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}

& $Python -m unittest discover -s (Join-Path $Root "tests") -v
if ($LASTEXITCODE -ne 0) {
    throw "测试失败。"
}

Push-Location $Root
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name TelegramCloudUploader `
        --workpath $BuildRoot `
        --distpath $DistRoot `
        cloud_uploader.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败。"
    }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $DistRoot "TelegramCloudUploader.exe") `
    -Destination (Join-Path $Destination "TelegramCloudUploader.exe") `
    -Force

Write-Host "已部署：$(Join-Path $Destination 'TelegramCloudUploader.exe')"
