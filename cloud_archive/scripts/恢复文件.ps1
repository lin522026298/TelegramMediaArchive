param(
    [Parameter(Mandatory = $true)]
    [string]$RemotePath,
    [Parameter(Mandatory = $true)]
    [string]$DestinationDirectory,
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$Uploader = Join-Path $BaseDir "TelegramCloudUploader.exe"
$Config = Join-Path $BaseDir "config\rclone.conf"
if (-not (Test-Path -LiteralPath $Uploader)) {
    throw "找不到恢复工具：$Uploader"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "尚未完成百度网盘授权和 rclone 配置：$Config"
}

if ((Test-Path -LiteralPath $DestinationDirectory) -and @(Get-ChildItem -LiteralPath $DestinationDirectory -Force).Count -gt 0) {
    throw "请选择空的恢复目录。不会跳过或覆盖已有文件。"
}
& $Uploader --base-dir $BaseDir restore --remote-path $RemotePath --destination $DestinationDirectory
if ($LASTEXITCODE -ne 0) {
    throw "恢复或校验失败，退出码 $LASTEXITCODE；保留文件供检查，不代表恢复成功。"
}
Write-Host "恢复完成，文件集合、大小、SHA-256 和云端 cryptcheck 校验通过。"
