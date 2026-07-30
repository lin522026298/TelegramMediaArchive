param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$Exe = Join-Path $BaseDir "openlist.exe"
$DataDir = Join-Path $BaseDir "data"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "找不到 OpenList：$Exe"
}
if (-not (Test-Path -LiteralPath (Join-Path $DataDir "config.json"))) {
    throw "OpenList 尚未初始化：$DataDir"
}

$Existing = Get-NetTCPConnection -State Listen -LocalPort 5244 -ErrorAction SilentlyContinue
if ($Existing) {
    $Owners = @($Existing | Select-Object -ExpandProperty OwningProcess -Unique)
    $Expected = $false
    foreach ($OwnerPid in $Owners) {
        $Owner = Get-Process -Id $OwnerPid -ErrorAction SilentlyContinue
        if ($Owner -and $Owner.Path -and ([System.IO.Path]::GetFullPath($Owner.Path) -eq [System.IO.Path]::GetFullPath($Exe))) {
            $Expected = $true
        }
    }
    if (-not $Expected) {
        throw "端口 5244 已被其他程序占用。"
    }
} else {
    $Start = Start-Process -FilePath $Exe -ArgumentList @("start", "--data", "data") -WorkingDirectory $BaseDir -WindowStyle Hidden -PassThru

    $Deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $Existing = Get-NetTCPConnection -State Listen -LocalPort 5244 -ErrorAction SilentlyContinue
    } while (-not $Existing -and (Get-Date) -lt $Deadline)
    if (-not $Existing) {
        $ExitDetail = if ($Start.HasExited) { "启动器退出码 $($Start.ExitCode)" } else { "启动器仍在运行" }
        throw "OpenList 未在 30 秒内监听端口 5244；$ExitDetail。"
    }
}

$Unsafe = @($Existing | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") })
if ($Unsafe) {
    throw "OpenList 正在非环回地址监听，已拒绝继续。请先运行 scripts\配置本地OpenList.ps1。"
}

if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:5244"
}
