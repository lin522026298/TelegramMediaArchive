param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [string]$AppDir = "D:\Tools\TelegramMediaArchive",
    [string]$ArchiveRoot = "E:\电报视频导出_断点续传",
    [int]$TelegramTimeoutSeconds = 90,
    [int]$UploaderTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$CliExe = Join-Path $AppDir "TelegramMediaArchiveCLI.exe"
$TelegramStopFile = Join-Path $ArchiveRoot "state\STOP_TELEGRAM_SYNC"
$StopUploader = Join-Path $PSScriptRoot "停止加密上传.ps1"
$StopOpenList = Join-Path $PSScriptRoot "停止OpenList.ps1"

New-Item -ItemType Directory -Path (Split-Path -Parent $TelegramStopFile) -Force | Out-Null
[System.IO.File]::WriteAllText(
    $TelegramStopFile,
    "请在当前网络分块结束后安全停止 Telegram 连续归档。`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

$ExpectedCli = [System.IO.Path]::GetFullPath($CliExe)
$Deadline = (Get-Date).AddSeconds($TelegramTimeoutSeconds)
do {
    $RunningCli = @(Get-CimInstance Win32_Process -Filter "Name='TelegramMediaArchiveCLI.exe'" | Where-Object {
        $_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $ExpectedCli
    })
    if ($RunningCli.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $Deadline)

if ($RunningCli.Count -gt 0) {
    throw "等待 $TelegramTimeoutSeconds 秒后 Telegram 下载仍未安全停止。未强制结束，以保护 .part 断点文件。"
}
Write-Host "Telegram 下载已安全停止；断点文件和数据库均已保留。"

& $StopUploader -BaseDir $BaseDir -TimeoutSeconds $UploaderTimeoutSeconds
& $StopOpenList -BaseDir $BaseDir

Write-Host "连续归档链路已全部停止。APP 窗口可能仍在后台，可自行保留或退出。"
