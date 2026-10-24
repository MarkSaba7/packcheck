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
    session_id      TEXT                      -- QR hand-off session, if the phone was paired to a desk
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
    decided_at    TEXT NOT NULL
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
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
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


def connect(database_path):
    """Open (and if needed create) the database file. Returns a connection whose rows behave like dicts."""
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the web server may open a connection in one thread and use it
    # in another during the SAME request. A connection is never shared between requests, so
    # this is safe; sqlite's default refusal is meant for connections shared across threads.
    connection = sqlite3.connect(str(database_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- inspections

def save_inspection(connection, result, run, work_order=None, inspector=None, pack_id=None, session_id=None):
    """Store one pipeline result. Returns the inspection id and writes each difference's
    database id into result["differences"][i]["id"], so the page can post decisions."""
    created_at = result["created_at"]
    cursor = connection.execute(
        "INSERT INTO inspections (run, created_at, day, product_id, product_name, status, readings_status,"
        " message, work_order, inspector, pack_id, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (run, created_at, created_at[:10], result["product_id"], result["product_name"], result["status"],
         result.get("readings_status"), result["message"], work_order or None, inspector or None,
         pack_id or None, session_id or None),
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


def list_inspections(connection, day=None, work_order=None, session_id=None, after_id=None, pack_id=None):
    """Inspections matching the filters, oldest first, each with its differences (and their
    latest decision) and readings attached. Used by the report page and the desk screen."""
    conditions, values = [], []
    if day:
        conditions.append("day = ?")
        values.append(day)
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


def get_inspection(connection, inspection_id):
    row = connection.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()
    return _inspection_with_details(connection, row) if row else None


def _inspection_with_details(connection, row):
    inspection = dict(row)
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

def save_decision(connection, difference_id, decision, note=None, decided_by=None):
    """Record what a worker decided about one box. Every decision is kept (an audit trail);
    the LATEST one is what reports show."""
    if decision not in ("confirm", "dismiss"):
        raise ValueError(f"decision must be 'confirm' or 'dismiss', got {decision!r}")
    exists = connection.execute("SELECT 1 FROM differences WHERE id = ?", (difference_id,)).fetchone()
    if not exists:
        raise KeyError(f"no difference with id {difference_id}")
    connection.execute(
        "INSERT INTO decisions (difference_id, decision, note, decided_by, decided_at) VALUES (?,?,?,?,?)",
        (difference_id, decision, note or None, decided_by or None, _now()),
    )
    connection.commit()
    return latest_decision(connection, difference_id)


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
    connection.commit()


def _hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), PIN_ITERATIONS).hex()


def list_users(connection):
    return [row["name"] for row in connection.execute("SELECT name FROM users ORDER BY name")]


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
