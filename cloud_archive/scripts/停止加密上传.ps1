param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$StopFile = Join-Path $BaseDir "manifests\STOP_CLOUD_UPLOADER"
$PidFile = Join-Path $BaseDir "manifests\cloud-uploader.pid"
$Heartbeat = Join-Path $BaseDir "manifests\upload-heartbeat.json"

New-Item -ItemType Directory -Path (Split-Path -Parent $StopFile) -Force | Out-Null
[System.IO.File]::WriteAllText(
    $StopFile,
    "请停止加密上传器`r`n",
    [System.Text.UTF8Encoding]::new($false)
)

$PidValue = $null
if (Test-Path -LiteralPath $PidFile) {
    $PidText = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($PidText -match "^\d+$") {
        $PidValue = [int]$PidText
    }
}

if ($null -eq $PidValue) {
    Write-Host "未发现正在运行的加密上传器；停止标记已保留。"
    exit 0
}

$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
    if (-not $Process) {
        Write-Host "加密上传器已安全停止。"
        exit 0
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $Deadline)

$Phase = "未知"
if (Test-Path -LiteralPath $Heartbeat) {
    try {
        $Phase = (Get-Content -LiteralPath $Heartbeat -Raw | ConvertFrom-Json).phase
    } catch {
    }
}
throw "等待 $TimeoutSeconds 秒后进程仍在运行（阶段：$Phase）。未强制结束，以免打断文件操作。"
