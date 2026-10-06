param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $BaseDir "data\config.json"
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "OpenList 配置不存在：$ConfigPath"
}

$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$Config.scheme.address = "127.0.0.1"
$Config.tasks.upload.workers = 1
$Config.tasks.transfer.workers = 1
$Config.s3.enable = $false
$Config.ftp.enable = $false
$Config.sftp.enable = $false
$Config.mcp.enable = $false

$Json = $Config | ConvertTo-Json -Depth 100
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $Json + [Environment]::NewLine, $Utf8NoBom)

Write-Output "OpenList 已锁定为 127.0.0.1，上传/传输并发均为 1。"
