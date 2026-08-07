param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [string]$AppDir = "D:\Tools\TelegramMediaArchive",
    [string]$ArchiveRoot = "E:\电报视频导出_断点续传",
    [string]$BandwidthLimit = "2M",
    [int]$UploadPollInterval = 60
)

$ErrorActionPreference = "Stop"
$AppExe = Join-Path $AppDir "TelegramMediaArchive.exe"
$CliExe = Join-Path $AppDir "TelegramMediaArchiveCLI.exe"
$StartUploader = Join-Path $PSScriptRoot "启动加密上传.ps1"
$TelegramStopFile = Join-Path $ArchiveRoot "state\STOP_TELEGRAM_SYNC"
$LogDir = Join-Path $ArchiveRoot "logs"

foreach ($RequiredFile in @($AppExe, $CliExe, $StartUploader)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "找不到必要文件：$RequiredFile"
    }
}

& $StartUploader `
    -BaseDir $BaseDir `
    -ArchiveRoot $ArchiveRoot `
    -BandwidthLimit $BandwidthLimit `
    -PollInterval $UploadPollInterval

if (Test-Path -LiteralPath $TelegramStopFile) {
    Remove-Item -LiteralPath $TelegramStopFile -Force
}

$ExpectedCli = [System.IO.Path]::GetFullPath($CliExe)
$RunningCli = @(Get-CimInstance Win32_Process -Filter "Name='TelegramMediaArchiveCLI.exe'" | Where-Object {
    $_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $ExpectedCli
})
if ($RunningCli.Count -gt 0) {
    Write-Host "Telegram 连续归档已经运行，PID：$($RunningCli[0].ProcessId)"
    exit 0
}

$ExpectedApp = [System.IO.Path]::GetFullPath($AppExe)
$RunningApp = @(Get-CimInstance Win32_Process -Filter "Name='TelegramMediaArchive.exe'" | Where-Object {
    $_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $ExpectedApp
})

if ($RunningApp.Count -eq 0) {
    Start-Process `
        -FilePath $AppExe `
        -ArgumentList @("--root", "`"$ArchiveRoot`"", "--auto-sync") `
        -WorkingDirectory $AppDir | Out-Null
    Write-Host "已打开 Telegram 归档 APP，并请求自动开始连续归档。"
} else {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $StdoutLog = Join-Path $LogDir "continuous-sync.stdout.log"
    $StderrLog = Join-Path $LogDir "continuous-sync.stderr.log"
    Start-Process `
        -FilePath $CliExe `
        -ArgumentList @(
            "--root", "`"$ArchiveRoot`"",
            "resume",
            "--workers", "3",
            "--watch",
            "--poll-interval", "300",
            "--sync-new",
            "--index-interval", "300"
        ) `
        -WorkingDirectory $AppDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog | Out-Null
    Write-Host "APP 已经打开；连续归档 CLI 已在后台启动。"
}

$Deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 1
    $RunningCli = @(Get-CimInstance Win32_Process -Filter "Name='TelegramMediaArchiveCLI.exe'" | Where-Object {
        $_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $ExpectedCli
    })
} while ($RunningCli.Count -eq 0 -and (Get-Date) -lt $Deadline)

if ($RunningCli.Count -eq 0) {
    throw "60 秒内未检测到 Telegram 连续归档进程。请查看 APP 日志或 $LogDir。"
}

Write-Host "连续归档已运行，PID：$($RunningCli[0].ProcessId)"
Write-Host "流程：Telegram 增量索引/下载 -> 本地完整文件 -> 文件名和内容加密 -> 百度网盘 -> 校验 -> 删除本地明文。"
Write-Host "本脚本没有创建开机启动项、计划任务或 Windows 服务。"
