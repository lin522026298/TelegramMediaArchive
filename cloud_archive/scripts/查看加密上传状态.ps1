param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [string]$ArchiveRoot = "E:\电报视频导出_断点续传"
)

$ErrorActionPreference = "Stop"
$Uploader = Join-Path $BaseDir "TelegramCloudUploader.exe"
if (-not (Test-Path -LiteralPath $Uploader -PathType Leaf)) {
    throw "找不到加密上传器：$Uploader"
}

$Raw = & $Uploader --base-dir $BaseDir --archive-root $ArchiveRoot status
if ($LASTEXITCODE -ne 0) {
    throw "尚未创建上传状态，或状态读取失败。"
}
$Status = $Raw | Out-String | ConvertFrom-Json
$Heartbeat = $Status.heartbeat

$Counts = @()
if ($Status.counts) {
    $Status.counts.PSObject.Properties | ForEach-Object {
        $Counts += "$($_.Name)=$($_.Value)"
    }
}

[pscustomobject]@{
    进程阶段 = if ($Heartbeat) { $Heartbeat.phase } else { "无心跳" }
    心跳时间 = if ($Heartbeat) { $Heartbeat.updated_at } else { "无" }
    当前文件 = if ($Heartbeat -and $Heartbeat.current) { $Heartbeat.current.local_rel_path } else { "无" }
    队列状态 = if ($Counts) { $Counts -join ", " } else { "空" }
    待清理字节 = $Status.pending_bytes
    下载反压 = if ($Status.backpressure_active) { "已启用" } else { "未启用" }
    最近错误数 = @($Status.latest_errors).Count
} | Format-List

if (@($Status.latest_errors).Count -gt 0) {
    Write-Host "最近错误："
    $Status.latest_errors | Format-Table local_rel_path, attempts, updated_at, last_error -Wrap
}
