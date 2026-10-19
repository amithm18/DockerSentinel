"""
Store.

Saves every diagnosis in a small SQLite database file, so problems are
remembered even after the container is removed or Sentinel restarts.

Database file: ~/.docker-sentinel/sentinel.db
(can be changed with the SENTINEL_DB environment variable)

Two tables:
  diagnoses  one row per problem (a "ticket").
             The same failure is never saved twice: a container + the time
             it stopped + the trigger together are UNIQUE.
  snapshots  one row per container run: its flattened settings and whether
             that run was "candidate" (just started), "good" or "bad".
             Used to answer "what changed since it last worked?".
"""

import json
import os
import sqlite3
from datetime import datetime


DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".docker-sentinel", "sentinel.db")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS diagnoses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at    TEXT NOT NULL,
    container_id   TEXT NOT NULL,
    container_name TEXT NOT NULL,
    image          TEXT NOT NULL,
    trigger        TEXT NOT NULL,
    exit_code      INTEGER NOT NULL,
    oom_killed     INTEGER NOT NULL,
    error          TEXT NOT NULL,
    finished_at    TEXT NOT NULL,
    findings       TEXT NOT NULL,
    logs           TEXT NOT NULL,
    UNIQUE (container_id, finished_at, trigger)
)
"""

CREATE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    app_key      TEXT NOT NULL,
    container_id TEXT NOT NULL UNIQUE,
    taken_at     TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL
)
"""


def get_db_path():
    path = os.environ.get("SENTINEL_DB")
    if path is None or path == "":
        path = DEFAULT_DB_PATH
    return path


def connect():
    """
    Open the database, creating the folder and table the first time.
    """
    path = get_db_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(CREATE_TABLE)
    connection.execute(CREATE_SNAPSHOTS_TABLE)

    return connection


def save_diagnosis(trigger, evidence, findings):
    """
    Save one diagnosis.

    Returns (ticket_id, is_new):
      - ticket_id  the row id, shown to the user as the ticket number
      - is_new     False if this exact failure was already saved before,
                   so callers can print/notify only new problems

    It first looks for an existing ticket. Only if there is none does it
    insert, so ticket numbers stay continuous (#1, #2, #3 ...).
    """
    connection = connect()

    existing_id = find_ticket(connection, trigger, evidence)
    if existing_id is not None:
        connection.close()
        return existing_id, False

    values = (
        datetime.now().isoformat(timespec="seconds"),
        evidence["id"],
        evidence["name"],
        evidence["image"],
        trigger,
        evidence["exit_code"],
        int(evidence["oom_killed"]),
        evidence["error"],
        evidence["finished_at"],
        json.dumps(findings),
        evidence["logs"],
    )

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO diagnoses
            (detected_at, container_id, container_name, image, trigger,
             exit_code, oom_killed, error, finished_at, findings, logs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    connection.commit()

    if cursor.rowcount == 1:
        ticket_id = cursor.lastrowid
        is_new = True
    else:
        ticket_id = find_ticket(connection, trigger, evidence)
        is_new = False

    connection.close()

    return ticket_id, is_new


def find_ticket(connection, trigger, evidence):
    """
    Return the id of an already saved ticket for this exact failure, or None.
    """
    row = connection.execute(
        "SELECT id FROM diagnoses WHERE container_id = ? AND finished_at = ? AND trigger = ?",
        (evidence["id"], evidence["finished_at"], trigger),
    ).fetchone()

    if row is None:
        return None

    return row["id"]


def load_history(limit):
    """
    Return the most recent diagnoses, newest first, as a list of dictionaries.
    """
    connection = connect()

    rows = connection.execute(
        "SELECT * FROM diagnoses ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    history = []
    for row in rows:
        item = dict(row)
        item["findings"] = json.loads(item["findings"])
        history.append(item)

    connection.close()

    return history


def save_snapshot(app_key, container_id, data):
    """
    Save the settings of a container that just started, as a "candidate".

    If this container already has a snapshot:
      - "good" stays good (a restart does not undo that it worked)
      - otherwise it becomes a fresh candidate again
    """
    connection = connect()
    now = datetime.now().isoformat(timespec="seconds")

    row = connection.execute(
        "SELECT status FROM snapshots WHERE container_id = ?",
        (container_id,),
    ).fetchone()

    if row is None:
        connection.execute(
            "INSERT INTO snapshots (app_key, container_id, taken_at, status, data) VALUES (?, ?, ?, ?, ?)",
            (app_key, container_id, now, "candidate", json.dumps(data)),
        )
    elif row["status"] != "good":
        connection.execute(
            "UPDATE snapshots SET taken_at = ?, status = ?, data = ? WHERE container_id = ?",
            (now, "candidate", json.dumps(data), container_id),
        )

    connection.commit()
    connection.close()


def mark_snapshot(container_id, status):
    """
    Turn a "candidate" into "good" or "bad". A snapshot that is already
    good is not changed, because that run really did work.
    """
    connection = connect()

    connection.execute(
        "UPDATE snapshots SET status = ? WHERE container_id = ? AND status = 'candidate'",
        (status, container_id),
    )

    connection.commit()
    connection.close()


def list_candidates():
    connection = connect()

    rows = connection.execute(
        "SELECT container_id, taken_at FROM snapshots WHERE status = 'candidate'"
    ).fetchall()

    candidates = []
    for row in rows:
        candidates.append(dict(row))

    connection.close()

    return candidates


def get_snapshot(container_id):
    """
    The saved snapshot of one container, or None.
    Used when a --rm container is already gone by the time it fails.
    """
    connection = connect()

    row = connection.execute(
        "SELECT app_key, data FROM snapshots WHERE container_id = ?",
        (container_id,),
    ).fetchone()

    connection.close()

    if row is None:
        return None

    snapshot = {
        "app_key": row["app_key"],
        "data": json.loads(row["data"]),
    }
    return snapshot


def last_good_snapshot(app_key):
    """
    The most recent run of this app that worked, or None.
    """
    connection = connect()

    row = connection.execute(
        "SELECT taken_at, data FROM snapshots WHERE app_key = ? AND status = 'good' "
        "ORDER BY taken_at DESC, id DESC LIMIT 1",
        (app_key,),
    ).fetchone()

    connection.close()

    if row is None:
        return None

    snapshot = {
        "taken_at": row["taken_at"],
        "data": json.loads(row["data"]),
    }
    return snapshot
