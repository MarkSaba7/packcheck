<#
Milestone 7: make PackCheck run by itself on the company's Windows PC.

Run ONCE, from an elevated PowerShell (Start menu -> right-click PowerShell -> Run as administrator),
in the project folder:

    powershell -ExecutionPolicy Bypass -File scripts\windows_setup.ps1 -BackupFolder E:\packcheck-backups

It sets up four things, all with tools built into Windows (no extra program to install):

    "PackCheck server"   a Scheduled Task that starts the web server when the PC starts, even
                         before anyone logs in, and starts it again if it crashes
    "PackCheck backup"   every night at 02:00: scripts\backup.py into -BackupFolder
    "PackCheck prune"    every Sunday at 03:00: scripts\prune_runs.py deletes inspection images
                         older than -KeepDays (the database rows stay)
    firewall rule        lets the phones reach port 8000 (Windows blocks it by default)

Running it again replaces the tasks and the rule, so it is also how settings are changed.
scripts\windows_uninstall.ps1 removes all four.

Why Scheduled Tasks and not a "real" Windows service: a service needs an extra wrapper
program (NSSM, pywin32). A task with an "at startup" trigger does the same job here - start
with the PC, run without anyone logged in, restart on failure - with what Windows already has.

The tasks run as the SYSTEM account (so nobody has to stay logged in). Two consequences:
  - Python must be installed for all users (python.org installer, "Install for all users"),
    or as a .venv inside the project. The Microsoft Store Python only exists for the user who
    installed it; SYSTEM cannot start it. The script refuses it.
  - The project folder must be a normal folder such as C:\PackCheck, not inside OneDrive or a
    user's Documents: those are per-user, and OneDrive would also sync company artwork to a cloud.
#>

param(
    [string]$ProjectFolder = (Split-Path -Parent $PSScriptRoot),   # the folder above scripts\
    [string]$Python = "",                                          # default: .venv, else python on PATH
    [string]$BackupFolder = "",                                    # default: backups\ in the project
    [int]$KeepBackups = 30,
    [int]$KeepDays = 90,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"   # stop at the first error instead of carrying on half set up

# --- checks first: nothing is changed until everything looks right ----------------------

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this from an elevated PowerShell (right-click PowerShell -> Run as administrator)." }

$ProjectFolder = (Resolve-Path $ProjectFolder).Path
if (-not (Test-Path (Join-Path $ProjectFolder "packcheck\server.py"))) {
    throw "$ProjectFolder does not look like the PackCheck folder (no packcheck\server.py)."
}
if ($ProjectFolder -match "OneDrive") {
    throw "The project is inside OneDrive ($ProjectFolder). Move it to a local folder such as C:\PackCheck first (see the top of this script)."
}

if (-not $Python) {
    $venvPython = Join-Path $ProjectFolder ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { $Python = $venvPython }
    else { $Python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
if (-not $Python -or -not (Test-Path $Python)) { throw "Python not found. Pass -Python C:\path\to\python.exe" }
if ($Python -match "WindowsApps") {
    throw "$Python is the Microsoft Store Python, which the SYSTEM account cannot start. Install Python from python.org for all users and pass -Python."
}
# The packages must be there, or the server task would fail silently at every start.
& $Python -c "import uvicorn, fastapi, cv2, rapidocr_onnxruntime" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "$Python is missing packages. Run: & '$Python' -m pip install -r '$ProjectFolder\requirements.txt'"
}

if (-not $BackupFolder) { $BackupFolder = Join-Path $ProjectFolder "backups" }
New-Item -ItemType Directory -Force -Path $BackupFolder | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectFolder "logs") | Out-Null   # the server log config writes there

# --- the three tasks ---------------------------------------------------------------------

$system = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

function Set-PackCheckTask($name, $arguments, $trigger, $settings, $description) {
    # Every task runs Python from the project folder, so relative paths (runs\, logs\) are right.
    $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $ProjectFolder
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue   # re-runnable
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $system -Description $description | Out-Null
    Write-Host "  task '$name' registered"
}

Write-Host "Setting up PackCheck in $ProjectFolder with $Python"

# The server: no time limit (it runs for ever), restarted every minute if it stops with an error.
$serverSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
# One worker: SQLite and the desk sessions assume a single server process (see docs\DEPLOYMENT.md).
Set-PackCheckTask "PackCheck server" `
    "-m uvicorn packcheck.server:app --host 0.0.0.0 --port $Port --workers 1 --log-config scripts\server_logging.json" `
    (New-ScheduledTaskTrigger -AtStartup) $serverSettings "PackCheck web server (port $Port). Logs in logs\."

# -StartWhenAvailable: if the PC was off at 02:00, the backup runs as soon as it is on again.
$jobSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Set-PackCheckTask "PackCheck backup" "scripts\backup.py --dest `"$BackupFolder`" --keep $KeepBackups" `
    (New-ScheduledTaskTrigger -Daily -At "02:00") $jobSettings "Nightly PackCheck backup into $BackupFolder"
Set-PackCheckTask "PackCheck prune" "scripts\prune_runs.py --keep-days $KeepDays" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "03:00") $jobSettings "Deletes inspection images older than $KeepDays days"

# --- the firewall rule -------------------------------------------------------------------

# Without it, phones hang on "loading" for ever: Windows silently drops connections to a new
# listening program (see docs\DEPLOYMENT.md, section 1). -Profile Domain,Private: not on networks
# Windows considers "Public" (cafes, guest Wi-Fi).
Remove-NetFirewallRule -DisplayName "PackCheck" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "PackCheck" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow `
    -Profile Domain,Private | Out-Null
Write-Host "  firewall rule 'PackCheck' (TCP $Port, domain and private networks)"

# --- start now and say how to check ------------------------------------------------------

Start-ScheduledTask -TaskName "PackCheck server"
Write-Host ""
Write-Host "Done. The server is starting; open http://localhost:$Port on this PC in a few seconds."
Write-Host "Check:"
Write-Host "  Get-ScheduledTask -TaskName 'PackCheck*' | Get-ScheduledTaskInfo   # LastTaskResult 0 = it worked"
Write-Host "  Get-Content '$ProjectFolder\logs\server.log' -Tail 20"
Write-Host "  Start-ScheduledTask -TaskName 'PackCheck backup'                   # try a backup now, then look in $BackupFolder"
Write-Host "If the PC's network is marked Public, set it to Private (Settings -> Network) or phones cannot connect."
