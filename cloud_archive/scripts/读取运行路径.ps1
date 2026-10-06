param([string]$BaseDir)
$PathConfig = Join-Path $BaseDir "runtime-paths.json"
if (Test-Path -LiteralPath $PathConfig -PathType Leaf) {
    $RuntimePaths = Get-Content -LiteralPath $PathConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $ArchiveRoot -and $RuntimePaths.archive_root) { $ArchiveRoot = [string]$RuntimePaths.archive_root }
    if (-not $AppDir -and $RuntimePaths.app_dir) { $AppDir = [string]$RuntimePaths.app_dir }
}
if (-not $ArchiveRoot) { $ArchiveRoot = Join-Path $HOME "Downloads\TelegramMediaArchive" }
if (-not $AppDir) { $AppDir = "D:\Tools\TelegramMediaArchive" }
