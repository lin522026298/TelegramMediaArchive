function ConvertTo-ArchiveArgument([string]$Value) {
    $Escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $Escaped = [regex]::Replace($Escaped, '(\\+)$', '$1$1')
    return '"' + $Escaped + '"'
}

if (-not ('ArchiveLaunchNative' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ArchiveLaunchNative {
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    public static int InJob() {
        bool result;
        return IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out result) ? (result ? 1 : 0) : -1;
    }
}
'@
}

function Invoke-IndependentArchiveHost {
    param([string]$ScriptPath, [System.Collections.IDictionary]$Parameters, [switch]$DetachedHost)
    $InJob = [ArchiveLaunchNative]::InJob()
    if ($InJob -eq 0) { return $false }
    if ($DetachedHost) { throw 'WMI host is still in a Job or membership is unknown; refusing recursive launch.' }
    $Arguments = @('-NoProfile', '-NonInteractive', '-File', $ScriptPath)
    foreach ($Entry in $Parameters.GetEnumerator()) {
        if ($Entry.Key -eq 'DetachedHost') { continue }
        if ($Entry.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($Entry.Value.IsPresent) { $Arguments += '-' + $Entry.Key }
        } else {
            $Arguments += '-' + $Entry.Key
            $Arguments += [string]$Entry.Value
        }
    }
    $Arguments += '-DetachedHost'
    $Shell = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $Shell)) { $Shell = Join-Path $PSHOME 'pwsh.exe' }
    $Command = (@($Shell) + $Arguments | ForEach-Object { ConvertTo-ArchiveArgument $_ }) -join ' '
    $Startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
    $Result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine=$Command; CurrentDirectory=(Split-Path -Parent $ScriptPath); ProcessStartupInformation=$Startup
    }
    if ($Result.ReturnValue -ne 0) { throw ('WMI launch failed: ' + $Result.ReturnValue) }
    $Process = Get-Process -Id $Result.ProcessId -ErrorAction Stop
    $null = $Process.Handle
    if (-not $Process.WaitForExit(150000)) { throw 'Independent startup did not finish within 150 seconds; inspect existing processes before retrying.' }
    if ($Process.ExitCode -ne 0) { throw ('Independent startup failed: ' + $Process.ExitCode + '; inspect the tool logs.') }
    Write-Host ('Independent hidden host completed startup; PID ' + $Result.ProcessId)
    return $true
}
