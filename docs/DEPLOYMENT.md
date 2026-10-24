# Deployment (milestone 7)

What it takes to run PackCheck on the company's network for real, and what the IT person
should look at before saying yes. On Windows, `scripts/windows_setup.ps1` does sections 1,
4 and 6 in one go; the rest is a checklist with the plainest option for each point.

## 1. Where it runs, and setting it up

One ordinary PC or small server on the company's network. Everything stays on it: the
artwork, the photos, the database. Nothing is sent to a cloud service. The phones only need
to reach that PC over Wi-Fi.

Setup on a Windows PC, once:

1. **Python 3.10+ from python.org, "Install for all users"** (not the Microsoft Store
   Python: the server runs as the SYSTEM account, which cannot start the Store version).
2. **Copy the project to a local folder such as `C:\PackCheck`**, not OneDrive or a user's
   Documents (per-user folders; OneDrive would also sync company artwork to a cloud).
3. In that folder: `python -m pip install -r requirements.txt`
4. From an **elevated** PowerShell (Start menu -> right-click PowerShell -> Run as administrator):

   ```
   cd C:\PackCheck
   powershell -ExecutionPolicy Bypass -File scripts\windows_setup.ps1 -BackupFolder E:\packcheck-backups
   ```

   It registers three Scheduled Tasks and one firewall rule, then starts the server:

   | Task | When | What |
   |---|---|---|
   | PackCheck server | at PC startup, even with nobody logged in; restarted every minute if it crashes | `uvicorn ... --workers 1`, logs in `logs\` (one file per day, 90 days) |
   | PackCheck backup | every night, 02:00 (or as soon as the PC is on again) | `scripts\backup.py` into `-BackupFolder`, keeps 30 |
   | PackCheck prune | Sundays, 03:00 | `scripts\prune_runs.py --keep-days 90` |

   Options: `-Python`, `-Port`, `-KeepBackups`, `-KeepDays`. Running it again replaces
   everything (that is how a setting is changed). `scripts\windows_uninstall.ps1` removes
   the tasks and the rule and leaves the data alone.
5. Create the workers' logins (section 3) and try a restore (section 4).

Why Scheduled Tasks and not a Windows service: a service needs an extra wrapper program
(NSSM, pywin32). An "at startup" task with restart-on-failure does the same job with what
Windows already has. One worker process because SQLite and the desk sessions assume one.

Check it runs:

```
Get-ScheduledTask -TaskName "PackCheck*" | Get-ScheduledTaskInfo    # LastTaskResult 0 = fine
Get-Content C:\PackCheck\logs\server.log -Tail 20
```

On Linux the same command in a systemd unit, and cron for backup and prune.

**Windows Firewall (a common first-time snag).** The setup script adds the rule, for
"Domain" and "Private" networks only. A network Windows marks "Public" (the usual default
for a fresh Wi-Fi or Ethernet connection) stays blocked: set it to Private in Settings ->
Network. The symptom is a phone's browser hanging on "loading" forever, never a clean
error, because the connection is silently dropped rather than refused.

If phones still cannot connect after that, the PC and the phone may not actually be on the
same network segment even though both say the same Wi-Fi name - some routers and most
"Guest" networks turn on client/AP isolation, which keeps connected devices from reaching
each other on purpose. That is a router setting, not something this app or Windows Firewall
controls; disable it in the router's admin page, or use a network without it.

## 2. HTTPS

Plain HTTP is fine on a trusted internal Wi-Fi, and the phone page works over it because it
uses a file input for the camera. HTTPS is needed if:

- phones must reach the server over a network you do not control, or
- you want a live camera preview later (`getUserMedia` only works on HTTPS pages).

Plainest option: put Caddy (a single-file web server) in front of uvicorn. Caddy handles
the certificate and forwards to port 8000:

```
packcheck.company.local {
    reverse_proxy localhost:8000
    tls internal          # Caddy's own certificate authority; install its root cert on the phones
}
```

If the company already has a certificate for internal names, use that instead of `tls internal`.

## 3. Access control

- From the first user on, inspecting, deciding and editing zones require a login (name +
  PIN, stored as a salted hash; the login is a random ticket in a cookie). In the project folder:

  ```
  python -m packcheck.cli add-user "Sam Tremblay"           # asks for the PIN (at least 4 characters)
  python -m packcheck.cli users                             # who can sign in
  python -m packcheck.cli add-user "Sam Tremblay"           # same name again = new PIN; signs Sam out everywhere
  python -m packcheck.cli remove-user "Sam Tremblay"        # can no longer sign in, signed out everywhere
  ```

  Removing someone keeps their name on their past inspections (the audit trail). The last
  user cannot be removed: with no user left the login would switch off and the pages would
  be open to anyone on the network.
- Reports and the desk screen are viewable without login (they show no photos of anything
  but packs). If that is not acceptable, the same `require_user` dependency in
  `server.py` can be added to those routes.
- Keep the server off the guest Wi-Fi.

## 4. Backups

`scripts/backup.py` (run nightly by the "PackCheck backup" task) writes one zip,
`packcheck-YYYY-MM-DD_HHMM.zip`, with:

| What | Where | Why |
|---|---|---|
| Database | `packcheck.sqlite` | every inspection, decision, reading, user |
| Products | `products/` | artwork references, zones (including "Always ignore this spot" zones), rules, golden photos |
| Proofs | `private/proofs/` | the uploaded proofs the products were cut from |
| Inspection images | `runs/` | only with `--with-runs`: large, and pruned anyway (see 6) |

The database is copied with sqlite's own backup API, so the copy is consistent even if a
scan is being saved at that moment. It keeps the newest 30 zips (`--keep`) and never
deletes any other file. Point `-BackupFolder` at a drive the company already backs up.

**Try a restore once** - a backup nobody has restored is a hope, not a backup:

```
python scripts\backup.py --restore E:\packcheck-backups\packcheck-2026-10-24_0200.zip --to C:\restore-test
```

It only unpacks into an empty folder, never over the live data. To really restore: stop
the "PackCheck server" task, copy the unpacked files over the project's, start the task.

## 5. Audit log

The database already is one: `inspections` (who, when, what), `decisions` (every decision
is kept, not only the latest), `logins` (which tickets were issued). Uvicorn's access log
(`logs\access.log` when set up with `windows_setup.ps1`) adds which IP called what. Nothing deletes rows except a person with
access to the file.

## 6. Retention of photos

Photos of real packs are confidential and pile up. Decide a number of days with the
company; the "PackCheck prune" task runs `python scripts/prune_runs.py --keep-days 90`
every Sunday (change it with `windows_setup.ps1 -KeepDays N`). Database rows stay (reports still list old inspections),
only the image folders go.

## 7. What can go wrong

- **Someone photographs the wrong product.** Alignment refuses when fewer than 25 points
  agree, or when they all sit on one shared detail. Not perfect: two products with nearly
  identical faces will pass. The barcode scan is there to pick the right one.
- **The server is slow.** One inspection is 1-3 s of CPU (SIFT + OCR). Ten phones at once
  will queue. Fine for one line; a second server or a faster CPU otherwise.
- **The disk fills.** See retention.

## 8. What the IT person should check

- Only port 8000 (or 443 through Caddy) is open, only on the internal network.
- The three PackCheck tasks show LastTaskResult 0, and the backup folder is on a backed-up drive.
- A restore was tried once (`backup.py --restore ... --to <empty folder>`).
- Users exist and the PINs are not "1234".
- The retention task runs.
- `private/` and `products/` are not in any shared folder that syncs elsewhere.
