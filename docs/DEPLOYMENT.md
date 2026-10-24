# Deployment (milestone 7)

What it takes to run PackCheck on the company's network for real, and what the IT person
should look at before saying yes. Nothing here is done for you: it is a checklist with the
plainest option for each point.

## 1. Where it runs

One ordinary PC or small server on the company's network, running Python 3.10+.
Everything stays on it: the artwork, the photos, the database. Nothing is sent to a cloud
service. The phones only need to reach that PC over Wi-Fi.

Start command (one worker process; SQLite and the in-process session list assume one):

```
uvicorn packcheck.server:app --host 0.0.0.0 --port 8000 --workers 1
```

Keep it running: on Windows, a Scheduled Task "at startup" running that command from the
project folder (or the NSSM tool to make it a service); on Linux, a systemd unit.

**Windows Firewall (a common first-time snag).** Starting the server is not enough: by
default Windows blocks other devices from reaching a listening app, especially on a network
marked "Public" (the usual default for a fresh Wi-Fi or Ethernet connection). The symptom is
exactly a phone's browser hanging on "loading" forever, never a clean error, because the
connection attempt is silently dropped rather than refused. Fix it once, from an elevated
PowerShell (Start menu -> right-click PowerShell -> Run as administrator):

```
New-NetFirewallRule -DisplayName "PackCheck" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
```

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

- Create users: `python -m packcheck.cli add-user "Name" --pin 1234`. From the first user
  on, inspecting, deciding and editing zones require a login (name + PIN, stored as a
  salted hash; the login is a random ticket in a cookie).
- Reports and the desk screen are viewable without login (they show no photos of anything
  but packs). If that is not acceptable, the same `require_user` dependency in
  `server.py` can be added to those routes.
- Keep the server off the guest Wi-Fi.

## 4. Backups

Three things, copy them nightly to wherever the company backs up:

| What | Where | Why |
|---|---|---|
| Products | `products/` | artwork references, zones, rules, golden photos |
| Database | `packcheck.sqlite` | every inspection, decision, reading, user |
| Inspection images | `runs/` | close-ups shown in reports; large, and pruned (see 6) |

SQLite is one file; copy it while the server is idle, or use `sqlite3 packcheck.sqlite ".backup copy.sqlite"`.

## 5. Audit log

The database already is one: `inspections` (who, when, what), `decisions` (every decision
is kept, not only the latest), `logins` (which tickets were issued). Uvicorn's access log
(`--access-log`) adds which IP called what. Nothing deletes rows except a person with
access to the file.

## 6. Retention of photos

Photos of real packs are confidential and pile up. Decide a number of days with the
company, then run

```
python scripts/prune_runs.py --keep-days 90
```

from a weekly scheduled task. Database rows stay (reports still list old inspections),
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
- The backup job runs and a restore was tried once.
- Users exist and the PINs are not "1234".
- The retention task runs.
- `private/` and `products/` are not in any shared folder that syncs elsewhere.
