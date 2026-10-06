param(
    [string]$BaseDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"

function Read-KeyValueFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到凭证文件：$Path"
    }

    $values = @{}
    Get-Content -LiteralPath $Path | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
            $values[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
    return $values
}

function Get-RcloneObscured(
    [string]$RclonePath,
    [string]$PlainText
) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $RclonePath
    $startInfo.Arguments = "obscure -"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "无法启动 rclone obscure。"
    }

    $process.StandardInput.WriteLine($PlainText)
    $process.StandardInput.Close()
    $output = $process.StandardOutput.ReadToEnd().Trim()
    $null = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0 -or -not $output) {
        throw "rclone obscure 执行失败。"
    }
    return $output
}

$rclone = Join-Path $BaseDir "rclone\rclone.exe"
$recoveryPath = Join-Path $BaseDir "credentials\恢复凭证（明文）.txt"
$configPath = Join-Path $BaseDir "config\rclone.conf"

if (-not (Test-Path -LiteralPath $rclone -PathType Leaf)) {
    throw "找不到 rclone：$rclone"
}

$recovery = Read-KeyValueFile $recoveryPath
$required = @(
    "OPENLIST_ADMIN_PASSWORD",
    "OPENLIST_WEBDAV_TOKEN",
    "RCLONE_CRYPT_PASSWORD",
    "RCLONE_CRYPT_SALT"
)
foreach ($key in $required) {
    if (-not $recovery[$key]) {
        throw "恢复凭证中缺少：$key"
    }
}

$cryptPassword = Get-RcloneObscured $rclone $recovery.RCLONE_CRYPT_PASSWORD
$cryptSalt = Get-RcloneObscured $rclone $recovery.RCLONE_CRYPT_SALT

$config = @(
    "# TelegramMediaArchive encrypted Baidu stack",
    "# Generated locally. Password fields are rclone-obscured, not plaintext.",
    "",
    "[openlist_webdav]",
    "type = webdav",
    "url = http://127.0.0.1:5244/dav/baidu-encrypted-archive/",
    "vendor = other",
    "bearer_token = $($recovery.OPENLIST_WEBDAV_TOKEN)",
    "",
    "[baidu_chunks]",
    "type = chunker",
    "remote = openlist_webdav:/TelegramMediaArchiveEncrypted-v1",
    "chunk_size = 256Mi",
    "hash_type = md5all",
    "meta_format = simplejson",
    "transactions = rename",
    "",
    "[baidu_crypt]",
    "type = crypt",
    "remote = baidu_chunks:",
    "password = $cryptPassword",
    "password2 = $cryptSalt",
    "filename_encryption = standard",
    "directory_name_encryption = true",
    ""
) -join "`r`n"

$configDirectory = Split-Path -Parent $configPath
New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
[System.IO.File]::WriteAllText(
    $configPath,
    $config,
    [System.Text.UTF8Encoding]::new($false)
)

$remotes = & $rclone --config $configPath listremotes
if ($LASTEXITCODE -ne 0) {
    throw "rclone 配置校验失败。"
}

$rawConfig = Get-Content -LiteralPath $configPath -Raw
if ($rawConfig.Contains($recovery.RCLONE_CRYPT_PASSWORD) -or
    $rawConfig.Contains($recovery.OPENLIST_ADMIN_PASSWORD)) {
    throw "配置中意外出现了明文口令。"
}

[pscustomobject]@{
    配置文件 = $configPath
    远端 = ($remotes -join ", ")
    明文口令检查 = "通过"
} | Format-List
