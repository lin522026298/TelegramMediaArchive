param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$TestRemote
)

$ErrorActionPreference = "Stop"
$Results = [System.Collections.Generic.List[object]]::new()

function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $Results.Add([pscustomobject]@{
        检查项 = $Name
        结果 = if ($Passed) { "通过" } else { "失败" }
        详情 = $Detail
    })
}

$OpenList = Join-Path $BaseDir "openlist.exe"
$Rclone = Join-Path $BaseDir "rclone\rclone.exe"
$Uploader = Join-Path $BaseDir "TelegramCloudUploader.exe"
$OpenListConfig = Join-Path $BaseDir "data\config.json"
$RcloneConfig = Join-Path $BaseDir "config\rclone.conf"
$PilotGate = Join-Path $BaseDir "manifests\pilot-verification.json"

Add-Check "OpenList 可执行文件" (Test-Path -LiteralPath $OpenList) $OpenList
Add-Check "rclone 可执行文件" (Test-Path -LiteralPath $Rclone) $Rclone
Add-Check "加密上传器" (Test-Path -LiteralPath $Uploader) $Uploader
Add-Check "OpenList 配置" (Test-Path -LiteralPath $OpenListConfig) $OpenListConfig

$Listener = Get-NetTCPConnection -State Listen -LocalPort 5244 -ErrorAction SilentlyContinue
$LoopbackOnly = $Listener -and -not @($Listener | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") })
Add-Check "OpenList 本地监听" ([bool]$LoopbackOnly) ($(if ($Listener) { ($Listener.LocalAddress -join ", ") } else { "未运行" }))

Add-Check "rclone 实际配置" (Test-Path -LiteralPath $RcloneConfig) $RcloneConfig
Add-Check "删除安全门" (Test-Path -LiteralPath $PilotGate) $PilotGate
if ($TestRemote -and (Test-Path -LiteralPath $RcloneConfig)) {
    & $Rclone --config $RcloneConfig lsd "baidu_crypt:" --max-depth 1 | Out-Null
    Add-Check "加密远端可访问" ($LASTEXITCODE -eq 0) "baidu_crypt:"
}

$Results | Format-Table -AutoSize
if ($Results.Where({ $_.结果 -eq "失败" }).Count -gt 0) {
    exit 1
}
