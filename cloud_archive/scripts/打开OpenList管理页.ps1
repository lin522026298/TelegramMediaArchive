param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "启动OpenList.ps1") -BaseDir $BaseDir -OpenBrowser
