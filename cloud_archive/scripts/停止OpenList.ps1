param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$Exe = Join-Path $BaseDir "openlist.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "找不到 OpenList：$Exe"
}

$Stop = Start-Process -FilePath $Exe -ArgumentList @("stop", "--data", "data") -WorkingDirectory $BaseDir -WindowStyle Hidden -PassThru -Wait
if ($Stop.ExitCode -ne 0) {
    throw "OpenList 停止命令失败，退出码 $($Stop.ExitCode)。"
}

$Deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 300
    $Listener = Get-NetTCPConnection -State Listen -LocalPort 5244 -ErrorAction SilentlyContinue
} while ($Listener -and (Get-Date) -lt $Deadline)

if ($Listener) {
    throw "OpenList 在停止命令后仍占用端口 5244，请查看 data\log。"
}
