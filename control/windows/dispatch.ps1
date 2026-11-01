# Restricted Windows forced command (non-admin pcctl account).
# sshd_config Match User pcctl must ForceCommand THIS script.
$ErrorActionPreference = 'Stop'
$action = [string]$env:SSH_ORIGINAL_COMMAND
if ($action -cnotmatch '^(status|sleep|restart|shutdown)$') {
    Write-Error 'Only status, sleep, restart or shutdown are allowed'
    exit 2
}
try {
    switch ($action) {
        'status' {
            Write-Output 'ready'
            exit 0
        }
        'sleep' {
            $tool = 'C:\ProgramData\pcctl\PsShutdown.exe'
            if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
                throw 'PsShutdown.exe not installed; sleep is unavailable'
            }
            # PsShutdown handles the delayed suspend. Do not run an interactive shell.
            Start-Process -FilePath $tool -ArgumentList '-accepteula','-d','-t','3' -WindowStyle Hidden -ErrorAction Stop
            Write-Output 'Sleep requested'
            exit 0
        }
        'restart' {
            Start-Process -FilePath "$env:SystemRoot\System32\shutdown.exe" -ArgumentList '/r','/t','3' -WindowStyle Hidden -ErrorAction Stop
            Write-Output 'Restart requested'
            exit 0
        }
        'shutdown' {
            Start-Process -FilePath "$env:SystemRoot\System32\shutdown.exe" -ArgumentList '/s','/t','3' -WindowStyle Hidden -ErrorAction Stop
            Write-Output 'Shutdown requested'
            exit 0
        }
    }
} catch {
    Write-Error 'Failed to request power action (check account rights and PsShutdown installation)'
    exit 1
}
