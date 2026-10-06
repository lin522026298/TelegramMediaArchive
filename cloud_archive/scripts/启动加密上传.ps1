param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [string]$ArchiveRoot = "",
    [string]$BandwidthLimit = "off",
    [int]$PollInterval = 300
)

$ErrorActionPreference = "Stop"
if (-not $ArchiveRoot) { . (Join-Path $PSScriptRoot "读取运行路径.ps1") -BaseDir $BaseDir }
$Uploader = Join-Path $BaseDir "TelegramCloudUploader.exe"
$StartOpenList = Join-Path $PSScriptRoot "启动OpenList.ps1"
$StopFile = Join-Path $BaseDir "manifests\STOP_CLOUD_UPLOADER"
$PidFile = Join-Path $BaseDir "manifests\cloud-uploader.pid"
$Heartbeat = Join-Path $BaseDir "manifests\upload-heartbeat.json"

if (-not (Test-Path -LiteralPath $Uploader -PathType Leaf)) {
    throw "找不到加密上传器：$Uploader"
}
if (-not (Test-Path -LiteralPath $StartOpenList -PathType Leaf)) {
    throw "找不到 OpenList 启动脚本：$StartOpenList"
}
if ($PollInterval -lt 10) {
    throw "轮询间隔不能小于 10 秒。"
}

& $StartOpenList -BaseDir $BaseDir

if (Test-Path -LiteralPath $PidFile) {
    $ExistingPidText = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($ExistingPidText -match "^\d+$") {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPidText) -ErrorAction SilentlyContinue
        if ($ExistingProcess -and $ExistingProcess.Path) {
            $ExpectedPath = [System.IO.Path]::GetFullPath($Uploader)
            $ActualPath = [System.IO.Path]::GetFullPath($ExistingProcess.Path)
            if ($ActualPath -eq $ExpectedPath) {
                Write-Host "加密上传器已经在后台运行，PID：$ExistingPidText"
                exit 0
            }
        }
    }
}

if (Test-Path -LiteralPath $StopFile) {
    Remove-Item -LiteralPath $StopFile -Force
}

$StartedAt = (Get-Date).ToUniversalTime()
$Arguments = @(
    "--base-dir", "`"$BaseDir`"",
    "--archive-root", "`"$ArchiveRoot`"",
    "run",
    "--delete-local",
    "--bwlimit", $BandwidthLimit,
    "--poll-interval", $PollInterval
)
$Process = Start-Process `
    -FilePath $Uploader `
    -ArgumentList $Arguments `
    -WorkingDirectory $BaseDir `
    -WindowStyle Hidden `
    -PassThru

$Deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    $Process.Refresh()
    if ($Process.HasExited) {
        throw "加密上传器启动后立即退出，退出码 $($Process.ExitCode)。请查看 logs\cloud-uploader.log。"
    }
    if (Test-Path -LiteralPath $Heartbeat) {
        try {
            $Status = Get-Content -LiteralPath $Heartbeat -Raw | ConvertFrom-Json
            $HeartbeatAt = [datetimeoffset]::Parse($Status.updated_at).UtcDateTime
            if ($HeartbeatAt -ge $StartedAt.AddSeconds(-2) -and $Status.phase -ne "stopped") {
                Write-Host "加密上传器已在后台运行，PID：$($Status.pid)，阶段：$($Status.phase)"
                Write-Host "可关闭本窗口；上传和校验会继续。"
                exit 0
            }
        } catch {
        }
    }
} while ((Get-Date) -lt $Deadline)

throw "45 秒内未取得有效心跳。请查看 logs\cloud-uploader.log。"
