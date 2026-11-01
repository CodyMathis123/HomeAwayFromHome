# Installs a restricted non-admin local Windows pcctl SSH account.
# Requires an administrator PowerShell session. Read the runbook before executing.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
  [Parameter(Mandatory=$true)][ValidateScript({try { [ipaddress]::Parse($_).AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork } catch { $false }})][string]$PiAddress,
  [Parameter(Mandatory=$true)][ValidateScript({Test-Path -LiteralPath $_ -PathType Leaf})][string]$PiPublicKeyFile
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]$identity
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'Run in an elevated Windows PowerShell session'
}
$home = Join-Path $env:ProgramData 'pcctl'
$config = Join-Path $env:ProgramData 'ssh\sshd_config'
if (-not (Test-Path $config)) {
  throw 'Install the Windows OpenSSH Server optional feature first'
}
$current = Get-Content -LiteralPath $config -Raw
if ($current -match '(?im)^\s*Match\s+User\s+pcctl\s*$') {
  throw 'An existing pcctl Match block exists. Do not overwrite it: audit the existing restriction manually.'
}
$key = (Get-Content -LiteralPath $PiPublicKeyFile -Raw).Trim()
if ($key -notmatch '^(ssh-ed25519|ecdsa-sha2-[^ ]+) [A-Za-z0-9+/=]+(?:\s+.*)?$') {
  throw 'Expected a full OpenSSH public key for the Pi account'
}
if ($PSCmdlet.ShouldProcess('Windows SSH', 'Install constrained pcctl account, keys, dispatcher, and Pi-only firewall rule')) {
  if (-not (Get-LocalUser -Name 'pcctl' -ErrorAction SilentlyContinue)) {
    $password = Read-Host 'Create a strong, unique Windows pcctl account password (SSH password auth will be disabled)' -AsSecureString
    New-LocalUser -Name 'pcctl' -Password $password -Description 'HomeAway restricted non-admin computer power commands' | Out-Null
  }
  New-Item -ItemType Directory -Path $home -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dispatch.ps1') -Destination (Join-Path $home 'dispatch.ps1') -Force
  [IO.File]::WriteAllText((Join-Path $home 'authorized_keys'), $key + [Environment]::NewLine)
  # Explicit ACLs: only account pcctl, SYSTEM, and Administrators can read files.
  & icacls.exe $home /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'pcctl:(OI)(CI)RX' | Out-Null
  & icacls.exe (Join-Path $home 'authorized_keys') /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' 'pcctl:R' | Out-Null
  & icacls.exe (Join-Path $home 'dispatch.ps1') /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' 'pcctl:RX' | Out-Null
  $backup = "$config.homeaway.$((Get-Date).ToString('yyyyMMdd-HHmmss')).bak"
  Copy-Item -LiteralPath $config -Destination $backup
  @'

# HomeAway restricted automation account. Keep this as the LAST Match block.
Match User pcctl
    AuthorizedKeysFile __PROGRAMDATA__/pcctl/authorized_keys
    PasswordAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    ForceCommand C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:/ProgramData/pcctl/dispatch.ps1
'@ | Add-Content -LiteralPath $config
  $sshd = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'
  & $sshd -t -f $config
  if ($LASTEXITCODE -ne 0) {
    Copy-Item -LiteralPath $backup -Destination $config -Force
    throw "OpenSSH rejected config. Previous config restored from $backup"
  }
  # The stock OpenSSH firewall rule can accept all remote hosts.
  # Restrict it to the Pi deliberately: review this if you SSH from elsewhere.
  $existing = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
  if ($existing) {
    $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $PiAddress
  } else {
    New-NetFirewallRule -Name 'HomeAway-Pcctl-In-TCP' -DisplayName 'HomeAway restricted SSH from Pi' -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress $PiAddress | Out-Null
  }
  Set-Service -Name sshd -StartupType Automatic
  Restart-Service -Name sshd
  Write-Host 'pcctl SSH restrictions installed. Confirm firewall rules, account rights, and PsShutdown separately.'
  Write-Host "sshd_config backup: $backup"
}
