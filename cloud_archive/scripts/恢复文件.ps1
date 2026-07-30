param(
    [Parameter(Mandatory = $true)]
    [string]$RemotePath,
    [Parameter(Mandatory = $true)]
    [string]$DestinationDirectory,
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$Rclone = Join-Path $BaseDir "rclone\rclone.exe"
$Config = Join-Path $BaseDir "config\rclone.conf"
if (-not (Test-Path -LiteralPath $Rclone)) {
    throw "找不到 rclone：$Rclone"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "尚未完成百度网盘授权和 rclone 配置：$Config"
}

New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
& $Rclone --config $Config copy "baidu_crypt:$RemotePath" $DestinationDirectory --transfers 1 --checkers 2 --ignore-existing --progress
if ($LASTEXITCODE -ne 0) {
    throw "恢复失败，rclone 退出码 $LASTEXITCODE。"
}
