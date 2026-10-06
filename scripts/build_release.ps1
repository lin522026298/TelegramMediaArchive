$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Version = "0.1.7"
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

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $ResolvedPath = [System.IO.Path]::GetFullPath($Path)
    $ResolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (-not $ResolvedPath.StartsWith(
        $ResolvedParent + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "拒绝操作父目录之外的路径：$ResolvedPath"
    }
    return $ResolvedPath
}

function Remove-ChildDirectory([string]$Path, [string]$Parent) {
    $SafePath = Assert-ChildPath $Path $Parent
    if (Test-Path -LiteralPath $SafePath) {
        Remove-Item -LiteralPath $SafePath -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Remove-ChildDirectory (Join-Path $Root "build") $Root
Remove-ChildDirectory (Join-Path $Root "dist") $Root
Remove-ChildDirectory $PortableDir $ReleaseDir
Remove-ChildDirectory $SourceStage $ReleaseDir
Remove-Item -LiteralPath $SourceZip -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PortableZip -Force -ErrorAction SilentlyContinue

& $Python -m pip install -r (Join-Path $Root "requirements-lock-windows.txt")
if ($LASTEXITCODE -ne 0) { throw "安装锁定依赖失败。" }
& $Python -m unittest discover -s (Join-Path $Root "tests") -v
if ($LASTEXITCODE -ne 0) { throw "测试失败，已停止构建。" }

Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name TelegramMediaArchive --collect-all telethon --collect-all opentele --collect-all pystray --collect-all PIL --hidden-import tgcrypto --hidden-import tzdata --add-data "docs;docs" --add-data "README.md;." tg_media_app.py
    if ($LASTEXITCODE -ne 0) { throw "GUI 打包失败。" }
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --name TelegramMediaArchiveCLI --collect-all telethon --collect-all opentele --hidden-import tgcrypto --hidden-import tzdata tg_media_cli.py
    if ($LASTEXITCODE -ne 0) { throw "CLI 打包失败。" }
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --name TelegramCloudUploader cloud_uploader.py
    if ($LASTEXITCODE -ne 0) { throw "云上传器打包失败。" }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $PortableDir | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "dist\TelegramMediaArchive.exe") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "dist\TelegramMediaArchiveCLI.exe") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "dist\TelegramCloudUploader.exe") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "docs") -Destination $PortableDir -Recurse
Copy-Item -LiteralPath (Join-Path $Root "cloud_archive") -Destination $PortableDir -Recurse
Remove-ChildDirectory (Join-Path $PortableDir "docs\superpowers") $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "requirements.txt") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "requirements-opentele.txt") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $Root "run_app.bat") -Destination $PortableDir

Push-Location $Root
try {
    $Changes = @(git status --porcelain)
    if ($Changes.Count -ne 0) { throw "源码必须先提交，防止源码包与 EXE 不一致。" }
    git archive --format=zip --output=$SourceZip HEAD
    if ($LASTEXITCODE -ne 0) { throw "Git 白名单源码打包失败。" }
} finally { Pop-Location }
Compress-Archive -LiteralPath $PortableDir -DestinationPath $PortableZip -Force
& $Python (Join-Path $Root "scripts\privacy_audit.py") --zip $SourceZip --zip $PortableZip --exe (Join-Path $Root "dist\TelegramMediaArchive.exe") --exe (Join-Path $Root "dist\TelegramMediaArchiveCLI.exe") --exe (Join-Path $Root "dist\TelegramCloudUploader.exe")
if ($LASTEXITCODE -ne 0) { throw "发布包隐私或完整性检查失败。" }

Write-Host "Built:"
Write-Host "  $PortableDir"
Write-Host "  $PortableZip"
Write-Host "  $SourceZip"
