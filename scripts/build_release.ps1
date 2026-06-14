$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Version = "0.1.1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$ReleaseDir = Join-Path $Root "release"
$PortableName = "TelegramMediaArchive-$Version-windows-x86_64"
$PortableDir = Join-Path $ReleaseDir $PortableName
$SourceStage = Join-Path $ReleaseDir "source-stage"
$SourceZip = Join-Path $ReleaseDir "TelegramMediaArchive-$Version-source.zip"
$PortableZip = Join-Path $ReleaseDir "$PortableName.zip"

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Remove-Item -LiteralPath (Join-Path $Root "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $Root "dist") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PortableDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $SourceStage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $SourceZip -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PortableZip -Force -ErrorAction SilentlyContinue

& $Python -m pip install -r (Join-Path $Root "requirements-opentele.txt")
& $Python -m pip install -r (Join-Path $Root "requirements-build.txt")
& $Python -m unittest discover -s (Join-Path $Root "tests") -v

Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name TelegramMediaArchive --collect-all telethon --collect-all opentele --collect-all pystray --collect-all PIL --hidden-import tgcrypto --hidden-import tzdata --add-data "docs;docs" --add-data "README.md;." tg_media_app.py
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --name TelegramMediaArchiveCLI --collect-all telethon --collect-all opentele --hidden-import tgcrypto --hidden-import tzdata tg_media_cli.py
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $PortableDir | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "dist\TelegramMediaArchive.exe") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "dist\TelegramMediaArchiveCLI.exe") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "docs") -Destination $PortableDir -Recurse
Remove-Item -LiteralPath (Join-Path $PortableDir "docs\superpowers") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "requirements.txt") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "requirements-opentele.txt") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "run_app.bat") -Destination $PortableDir

$excludeDirs = @(".git", ".venv", "__pycache__", "build", "dist", "release", "state", "media", "logs")
$excludeFiles = @("*.session", "*.sqlite3", "*.sqlite3-*", "*.part", "*.pyc", "*.pyo")
New-Item -ItemType Directory -Force -Path $SourceStage | Out-Null
robocopy $Root $SourceStage /E /XD $excludeDirs /XF $excludeFiles | Out-Null
$robocopyCode = $LASTEXITCODE
if ($robocopyCode -gt 7) {
    throw "robocopy failed with exit code $robocopyCode"
}
$global:LASTEXITCODE = 0

Compress-Archive -Path (Join-Path $SourceStage "*") -DestinationPath $SourceZip -Force
Compress-Archive -LiteralPath $PortableDir -DestinationPath $PortableZip -Force
Remove-Item -LiteralPath $SourceStage -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Built:"
Write-Host "  $PortableDir"
Write-Host "  $PortableZip"
Write-Host "  $SourceZip"
