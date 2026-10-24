<#
Undo scripts\windows_setup.ps1: stop and remove the three PackCheck tasks and the firewall rule.
Run from an elevated PowerShell:

    powershell -ExecutionPolicy Bypass -File scripts\windows_uninstall.ps1

Nothing else is touched: the project folder, the database, the backups and the logs stay.
#>

$ErrorActionPreference = "Stop"

foreach ($name in "PackCheck server", "PackCheck backup", "PackCheck prune") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "removed task '$name'"
    }
}
if (Get-NetFirewallRule -DisplayName "PackCheck" -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName "PackCheck"
    Write-Host "removed firewall rule 'PackCheck'"
}
