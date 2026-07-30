param(
    [string]$ArchiveRoot = "E:\电报视频导出_断点续传",
    [string]$AppDir = "D:\Tools\TelegramMediaArchive",
    [string]$MirrorDir = "D:\Cloud Storage\Openlist\state-backups",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Cli = Join-Path $AppDir "TelegramMediaArchiveCLI.exe"
if (-not (Test-Path -LiteralPath $Cli)) {
    throw "找不到下载器 CLI：$Cli"
}

$Arguments = @("--root", $ArchiveRoot, "snapshot", "--mirror", $MirrorDir)
if ($Force) {
    $Arguments += "--force"
}

Push-Location $AppDir
try {
    & $Cli @Arguments
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($ExitCode -ne 0) {
    throw "SQLite 快照失败，退出码 $ExitCode。"
}
