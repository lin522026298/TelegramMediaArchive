import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
LAUNCHER = Path(__file__).resolve().parents[1] / "cloud_archive" / "scripts" / "\u542f\u52a8OpenList.ps1"


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class OpenListLauncherTests(unittest.TestCase):
    def run_launcher(self, owner, expected_exit):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "daemon").mkdir()
            (root / "openlist.exe").touch()
            (root / "data" / "config.json").write_text("{}", encoding="ascii")
            pid = root / "daemon" / "pid"
            pid.write_text("12345", encoding="ascii")
            script = r"""
$ErrorActionPreference = 'Stop'
$global:TestQueries = 0
$global:TestStarted = 0
function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    $global:TestQueries++
    if ($global:TestQueries -gt 1) { [pscustomobject]@{ LocalAddress='127.0.0.1' } }
}
function Get-Process {
    param($Id, $ErrorAction)
    OWNER
}
function Start-Process {
    param($FilePath, [string[]]$ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    if ($WindowStyle -ne 'Hidden') { throw 'Expected hidden launch' }
    $global:TestStarted++
    [pscustomobject]@{ HasExited=$false }
}
try {
    & $env:TEST_LAUNCHER -BaseDir $env:TEST_BASE
    if ($global:TestStarted -ne 1) { throw 'Expected one launch' }
    if (Test-Path -LiteralPath (Join-Path $env:TEST_BASE 'daemon\pid')) { throw 'Stale PID survived' }
    exit 0
} catch {
    Write-Output $_
    if ($global:TestStarted -ne 0) { exit 2 }
    if (-not (Test-Path -LiteralPath (Join-Path $env:TEST_BASE 'daemon\pid'))) { exit 3 }
    exit 1
}
""".replace("OWNER", owner)
            env = dict(os.environ, TEST_BASE=str(root), TEST_LAUNCHER=str(LAUNCHER))
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-Command", script],
                capture_output=True, text=True, env=env, timeout=15,
            )
            self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)

    def test_missing_process_stale_pid_is_removed(self):
        self.run_launcher("return $null", 0)

    def test_reused_pid_does_not_stop_the_other_process(self):
        self.run_launcher("[pscustomobject]@{ Path=(Join-Path $env:TEST_BASE 'other.exe') }", 0)

    def test_live_openlist_without_listener_is_preserved(self):
        self.run_launcher("[pscustomobject]@{ Path=(Join-Path $env:TEST_BASE 'openlist.exe') }", 1)
