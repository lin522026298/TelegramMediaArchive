param(
    [string]$ArchiveRoot = "",
    [string]$AppDir = "",
    [string]$MirrorDir = "",
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not $ArchiveRoot -or -not $AppDir) { . (Join-Path $PSScriptRoot "读取运行路径.ps1") -BaseDir $BaseDir }
if (-not $MirrorDir) { $MirrorDir = Join-Path $BaseDir "state-backups" }
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
