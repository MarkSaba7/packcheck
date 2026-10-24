"""Milestone 3: remember every inspection, so reports can be built from them.

The files in runs/<run>/ stay the source of truth for images. The database is an INDEX over
those folders: which packs were inspected today, what was found, what the worker decided.
Without it, "everything from today" would mean walking hundreds of folders and opening
every result.json.

Why sqlite3: it is built into Python, the whole database is one file (packcheck.sqlite),
there is no server to install, and plain SQL is easy to read. A "real" database (Postgres)
only pays off with many users at once (milestone 7 can revisit that).

Tables:

    inspections   one row per photo checked          (product, time, status, who, work order)
    differences   one row per box the pipeline drew  (kind, where, how strong)
    decisions     what the worker said about a box   (confirm / dismiss, note, who, when)
    readings      what was read in each variable zone and whether it passed its rule
    sessions      QR hand-off sessions (milestone 4): the desk that opened one, when
    users         who may log in (milestone 4): name + salted PIN hash
    logins        active login tickets (the cookie value) and who they belong to

Every function takes an open connection, so tests can use a temporary file.
"""

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id              INTEGER PRIMARY KEY,
    run             TEXT UNIQUE NOT NULL,     -- folder name under runs/
    created_at      TEXT NOT NULL,            -- ISO 8601, local time
    day             TEXT NOT NULL,            -- YYYY-MM-DD, so "today's report" is one comparison
    product_id      TEXT NOT NULL,
    product_name    TEXT NOT NULL,
    status          TEXT NOT NULL,            -- ok | differences_found | alignment_failed
    readings_status TEXT,                     -- ok | invalid | check_by_hand | NULL
    message         TEXT NOT NULL,
    work_order      TEXT,                     -- typed by the worker; groups packs for one report
    inspector       TEXT,                     -- who took the photo
    pack_id         TEXT,                     -- groups the faces (front, back) of one physical pack
    session_id      TEXT,                     -- QR hand-off session, if the phone was paired to a desk
    codes_read      TEXT                      -- JSON list of the barcodes / QR codes read in the photo
);
CREATE INDEX IF NOT EXISTS inspections_day ON inspections(day);
CREATE INDEX IF NOT EXISTS inspections_work_order ON inspections(work_order);

CREATE TABLE IF NOT EXISTS differences (
    id            INTEGER PRIMARY KEY,
    inspection_id INTEGER NOT NULL REFERENCES inspections(id),
    number        INTEGER NOT NULL,           -- the number drawn on the comparison image
    kind          TEXT NOT NULL,              -- content | colour
    rect_fraction TEXT NOT NULL,              -- JSON [x, y, w, h] as fractions of the face
    mean_delta_e  REAL,
    max_delta_e   REAL,
    pixels        INTEGER,
    closeup       TEXT                        -- file name in the run folder
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY,
    difference_id INTEGER NOT NULL REFERENCES differences(id),
    decision      TEXT NOT NULL CHECK (decision IN ('confirm', 'dismiss')),
    note          TEXT,
    decided_by    TEXT,
    decided_at    TEXT NOT NULL,
    checklist_item TEXT                       -- which R79 point a confirmed difference breaks (r79.CHECKLIST)
);

CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY,
    inspection_id INTEGER NOT NULL REFERENCES inspections(id),
    zone          TEXT NOT NULL,
    name          TEXT NOT NULL,              -- rule name (best_before, lot); "" = the whole zone
    text          TEXT,
    valid         INTEGER,                    -- 1 valid, 0 invalid, NULL check by hand
    reason        TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    connected_at TEXT,                        -- when a phone opened the session's link
    checking     INTEGER NOT NULL DEFAULT 0   -- 1 while the phone's photos are being checked
);

CREATE TABLE IF NOT EXISTS users (
    name       TEXT PRIMARY KEY,
    pin_hash   TEXT NOT NULL,
    salt       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logins (
    token      TEXT PRIMARY KEY,
    user_name  TEXT NOT NULL REFERENCES users(name),
    created_at TEXT NOT NULL
);
"""

# Columns added after the first version of the schema. CREATE TABLE IF NOT EXISTS does not
# touch a table that already exists, so a database made by an older version gets them here.
ADDED_COLUMNS = [
    ("inspections", "codes_read", "TEXT"),
    ("decisions", "checklist_item", "TEXT"),
    ("sessions", "connected_at", "TEXT"),
    ("sessions", "checking", "INTEGER NOT NULL DEFAULT 0"),
]


def connect(database_path):
    """Open (and if needed create) the database file. Returns a connection whose rows behave like dicts."""
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the web server may open a connection in one thread and use it
    # in another during the SAME request. A connection is never shared between requests, so
    # this is safe; sqlite's default refusal is meant for connections shared across threads.
    connection = sqlite3.connect(str(database_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    for table, column, kind in ADDED_COLUMNS:
        existing = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    return connection


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- inspections

def save_inspection(connection, result, run, work_order=None, inspector=None, pack_id=None, session_id=None):
    """Store one pipeline result. Returns the inspection id and writes each difference's
    database id into result["differences"][i]["id"], so the page can post decisions."""
    created_at = result["created_at"]
    codes_read = json.dumps(result["codes_read"]) if "codes_read" in result else None
    cursor = connection.execute(
        "INSERT INTO inspections (run, created_at, day, product_id, product_name, status, readings_status,"
        " message, work_order, inspector, pack_id, session_id, codes_read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run, created_at, created_at[:10], result["product_id"], result["product_name"], result["status"],
         result.get("readings_status"), result["message"], work_order or None, inspector or None,
         pack_id or None, session_id or None, codes_read),
    )
    inspection_id = cursor.lastrowid

    for difference in result["differences"]:
        cursor = connection.execute(
            "INSERT INTO differences (inspection_id, number, kind, rect_fraction, mean_delta_e, max_delta_e, pixels, closeup)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (inspection_id, difference["number"], difference["kind"], json.dumps(difference["rect_fraction"]),
             difference["mean_delta_e"], difference["max_delta_e"], difference["pixels"], difference["closeup"]),
        )
        difference["id"] = cursor.lastrowid

    for zone in result["variable_zones"]:
        connection.execute(
            "INSERT INTO readings (inspection_id, zone, name, text, valid, reason) VALUES (?,?,?,?,?,?)",
            (inspection_id, zone["name"], "", zone.get("text"), _valid_to_int(zone.get("valid")), zone.get("reason")),
        )
        for reading in zone.get("readings", []):
            connection.execute(
                "INSERT INTO readings (inspection_id, zone, name, text, valid, reason) VALUES (?,?,?,?,?,?)",
                (inspection_id, zone["name"], reading["name"], reading["text"], _valid_to_int(reading["valid"]), reading["reason"]),
            )

    connection.commit()
    result["inspection_id"] = inspection_id
    return inspection_id


def _valid_to_int(valid):
    return None if valid is None else int(bool(valid))


def _int_to_valid(value):
    return None if value is None else bool(value)


def list_inspections(connection, day=None, work_order=None, session_id=None, after_id=None, pack_id=None, since_day=None,
                     product_ids=None):
    """Inspections matching the filters, oldest first, each with its differences (and their
    latest decision) and readings attached. Used by the report pages and the desk screen.
    `since_day` (YYYY-MM-DD) keeps that day and later: the history's "this week"."""
    conditions, values = [], []
    if day:
        conditions.append("day = ?")
        values.append(day)
    if since_day:
        conditions.append("day >= ?")
        values.append(since_day)
    if product_ids is not None:
        conditions.append(f"product_id IN ({','.join('?' * len(product_ids))})" if product_ids else "0")
        values.extend(product_ids)
    if work_order:
        conditions.append("work_order = ?")
        values.append(work_order)
    if session_id:
        conditions.append("session_id = ?")
        values.append(session_id)
    if pack_id:
        conditions.append("pack_id = ?")
        values.append(pack_id)
    if after_id:
        conditions.append("id > ?")
        values.append(after_id)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = connection.execute(f"SELECT * FROM inspections {where} ORDER BY id", values).fetchall()
    return [_inspection_with_details(connection, row) for row in rows]


def count_earlier_inspections(connection, product_ids, before_id):
    """How many inspections of these products happened before inspection `before_id`.
    Zero means this delivery is the artwork's first ("Première réception" on the R79)."""
    if not product_ids:
        return 0
    marks = ",".join("?" * len(product_ids))
    return connection.execute(f"SELECT COUNT(*) FROM inspections WHERE product_id IN ({marks}) AND id < ?",
                              (*product_ids, before_id)).fetchone()[0]


def list_pack_ids(connection, since_day):
    """The packs inspected since that day, newest first (inspections without a pack are left out)."""
    rows = connection.execute("SELECT pack_id, MIN(id) AS first FROM inspections WHERE pack_id IS NOT NULL AND day >= ?"
                              " GROUP BY pack_id ORDER BY first DESC", (since_day,))
    return [row["pack_id"] for row in rows]


def get_inspection(connection, inspection_id):
    row = connection.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()
    return _inspection_with_details(connection, row) if row else None


def _inspection_with_details(connection, row):
    inspection = dict(row)
    inspection["codes_read"] = json.loads(inspection["codes_read"]) if inspection.get("codes_read") else None
    inspection["differences"] = []
    for difference_row in connection.execute("SELECT * FROM differences WHERE inspection_id = ? ORDER BY number", (row["id"],)):
        difference = dict(difference_row)
        difference["rect_fraction"] = json.loads(difference["rect_fraction"])
        difference["decision"] = latest_decision(connection, difference["id"])
        inspection["differences"].append(difference)
    inspection["readings"] = []
    for reading_row in connection.execute("SELECT * FROM readings WHERE inspection_id = ? ORDER BY id", (row["id"],)):
        reading = dict(reading_row)
        reading["valid"] = _int_to_valid(reading["valid"])
        inspection["readings"].append(reading)
    return inspection


# ------------------------------------------------------------------ decisions

def save_decision(connection, difference_id, decision, note=None, decided_by=None, checklist_item=None):
    """Record what a worker decided about one box. Every decision is kept (an audit trail);
    the LATEST one is what reports show. `checklist_item` says which R79 point a confirmed
    difference breaks (a key of r79.CHECKLIST), so the form can mark that point."""
    if decision not in ("confirm", "dismiss"):
        raise ValueError(f"decision must be 'confirm' or 'dismiss', got {decision!r}")
    exists = connection.execute("SELECT 1 FROM differences WHERE id = ?", (difference_id,)).fetchone()
    if not exists:
        raise KeyError(f"no difference with id {difference_id}")
    connection.execute(
        "INSERT INTO decisions (difference_id, decision, note, decided_by, decided_at, checklist_item) VALUES (?,?,?,?,?,?)",
        (difference_id, decision, note or None, decided_by or None, _now(), checklist_item if decision == "confirm" else None),
    )
    connection.commit()
    return latest_decision(connection, difference_id)


def get_difference(connection, difference_id):
    """One difference with the product it was found on, or None. "Always ignore this spot"
    needs both: where the box is (rect_fraction) and which artwork gets the ignore zone."""
    row = connection.execute(
        "SELECT differences.*, inspections.product_id FROM differences"
        " JOIN inspections ON inspections.id = differences.inspection_id WHERE differences.id = ?", (difference_id,)
    ).fetchone()
    if row is None:
        return None
    difference = dict(row)
    difference["rect_fraction"] = json.loads(difference["rect_fraction"])
    return difference


def latest_decision(connection, difference_id):
    row = connection.execute(
        "SELECT * FROM decisions WHERE difference_id = ? ORDER BY id DESC LIMIT 1", (difference_id,)
    ).fetchone()
    return dict(row) if row else None


def list_decisions(connection):
    """Every decision ever made, joined with its difference and inspection: the training data
    for a future false-alarm filter (milestone 6). See scripts/export_decisions.py."""
    rows = connection.execute(
        "SELECT decisions.*, differences.kind, differences.rect_fraction, differences.mean_delta_e,"
        " differences.max_delta_e, differences.pixels, inspections.product_id, inspections.run"
        " FROM decisions JOIN differences ON differences.id = decisions.difference_id"
        " JOIN inspections ON inspections.id = differences.inspection_id ORDER BY decisions.id"
    ).fetchall()
    return [dict(row) for row in rows]


# ------------------------------------------------------------------- sessions

def create_session(connection):
    """A session is a short random code the desk screen made up; the phone that scans its QR
    code sends inspections with that code, and the desk asks for them by that code."""
    session_id = secrets.token_urlsafe(6)
    connection.execute("INSERT INTO sessions (id, created_at) VALUES (?, ?)", (session_id, _now()))
    connection.commit()
    return session_id


def session_exists(connection, session_id):
    return connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is not None


def get_session(connection, session_id):
    """{id, created_at, connected_at, checking} or None. The desk polls it to know when the
    phone has opened the link and when photos are being checked."""
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def mark_session_connected(connection, session_id):
    connection.execute("UPDATE sessions SET connected_at = ? WHERE id = ?", (_now(), session_id))
    connection.commit()


def set_session_checking(connection, session_id, checking):
    connection.execute("UPDATE sessions SET checking = ? WHERE id = ?", (int(bool(checking)), session_id))
    connection.commit()


# ---------------------------------------------------------------- users, login

PIN_ITERATIONS = 200_000   # how many times the PIN is hashed; slows down guessing, not the user


def add_user(connection, name, pin):
    """Create or replace a user. The PIN is never stored: only a salted hash of it."""
    name = name.strip()
    if not name:
        raise ValueError("the user name is empty")
    if not pin or len(pin) < 4:
        raise ValueError("the PIN must have at least 4 characters")
    salt = secrets.token_hex(16)
    connection.execute(
        "INSERT OR REPLACE INTO users (name, pin_hash, salt, created_at) VALUES (?,?,?,?)",
        (name, _hash_pin(pin, salt), salt, _now()),
    )
    # Running add-user again for an existing name is how a PIN is changed. A PIN is usually
    # changed because someone else learned it, so every device signed in with the old one
    # is signed out too.
    connection.execute("DELETE FROM logins WHERE user_name = ?", (name,))
    connection.commit()


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), PIN_ITERATIONS).hex()


def list_users(connection):
    return [row["name"] for row in connection.execute("SELECT name FROM users ORDER BY name")]


def remove_user(connection, name):
    """Delete a user and sign out every device they are signed in on. Returns False if there
    is no such user. Their past inspections and decisions keep their name: that is the audit trail."""
    if name not in list_users(connection):
        return False
    if len(list_users(connection)) == 1:
        # With no user left, login_required() is False and the pages open to anyone on the
        # network. That must never happen by accident while removing someone.
        raise ValueError("this is the last user: removing it would turn the login off. Add another user first.")
    connection.execute("DELETE FROM logins WHERE user_name = ?", (name,))
    connection.execute("DELETE FROM users WHERE name = ?", (name,))
    connection.commit()
    return True


def login_required(connection):
    """Until the first user is created, anyone may use the tool (handy while setting up)."""
    return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def login(connection, name, pin):
    """Check name + PIN. Returns a login token to put in a cookie, or None if they do not match."""
    row = connection.execute("SELECT * FROM users WHERE name = ?", (name.strip(),)).fetchone()
    if row is None or not secrets.compare_digest(_hash_pin(pin, row["salt"]), row["pin_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    connection.execute("INSERT INTO logins (token, user_name, created_at) VALUES (?,?,?)", (token, row["name"], _now()))
    connection.commit()
    return token


def user_for_token(connection, token):
    """The user name behind a login token, or None if the token is unknown."""
    if not token:
        return None
    row = connection.execute("SELECT user_name FROM logins WHERE token = ?", (token,)).fetchone()
    return row["user_name"] if row else None


def logout(connection, token):
    connection.execute("DELETE FROM logins WHERE token = ?", (token,))
    connection.commit()
