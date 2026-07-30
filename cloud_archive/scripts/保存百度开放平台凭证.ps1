param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

function Read-HiddenValue([string]$Label) {
    $Secure = Read-Host "$Label（输入时不显示字符）" -AsSecureString
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

$AppId = Read-HiddenValue "请输入 AppID"
$AppKey = Read-HiddenValue "请输入 AppKey"
$SecretKey = Read-HiddenValue "请输入 SecretKey"
$SignKey = Read-HiddenValue "请输入 SignKey"

if (-not $AppKey -or -not $SecretKey) {
    throw "AppKey 和 SecretKey 不能为空。"
}

$EnvPath = Join-Path $HOME ".env"
$Existing = if (Test-Path -LiteralPath $EnvPath) {
    Get-Content -LiteralPath $EnvPath | Where-Object {
        $_ -notmatch "^BAIDU_(APP_ID|APP_KEY|SECRET_KEY|SIGN_KEY)="
    }
} else {
    @()
}

$EnvLines = @($Existing) + @(
    "BAIDU_APP_ID=$AppId"
    "BAIDU_APP_KEY=$AppKey"
    "BAIDU_SECRET_KEY=$SecretKey"
    "BAIDU_SIGN_KEY=$SignKey"
)

$CredentialDir = Join-Path $BaseDir "credentials"
$CredentialPath = Join-Path $CredentialDir "百度开放平台凭证（明文）.env"
New-Item -ItemType Directory -Path $CredentialDir -Force | Out-Null
$CredentialLines = @(
    "# 百度开放平台凭证（明文）"
    "# 不要上传到百度网盘或 GitHub。"
    "BAIDU_APP_ID=$AppId"
    "BAIDU_APP_KEY=$AppKey"
    "BAIDU_SECRET_KEY=$SecretKey"
    "BAIDU_SIGN_KEY=$SignKey"
    "BAIDU_CALLBACK=https://api.oplist.org/baiduyun/callback"
)

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($EnvPath, $EnvLines, $Utf8NoBom)
[System.IO.File]::WriteAllLines($CredentialPath, $CredentialLines, $Utf8NoBom)

Remove-Variable AppId, AppKey, SecretKey, SignKey, EnvLines, CredentialLines -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "凭证已保存。本窗口可以关闭。"
Read-Host "按 Enter 退出"
