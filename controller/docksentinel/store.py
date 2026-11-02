"""SQLite storage: incident history and the last revision verified as healthy."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Diagnosis, Incident, Outcome

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    namespace TEXT, deployment TEXT, pod TEXT, container TEXT,
    signal TEXT, rule TEXT, cause TEXT, action TEXT,
    status TEXT, detail TEXT, recovery_seconds REAL
);
CREATE TABLE IF NOT EXISTS good_revisions (
    namespace TEXT, deployment TEXT, revision INTEGER, verified_at TEXT,
    PRIMARY KEY (namespace, deployment)
);
"""


class Store:
    def __init__(self, path: str | Path = "docksentinel.db"):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- incidents ---------------------------------------------------------
    def record(self, inc: Incident, dx: Diagnosis, out: Outcome) -> int:
        cur = self.db.execute(
            "INSERT INTO incidents (detected_at, namespace, deployment, pod, container,"
            " signal, rule, cause, action, status, detail, recovery_seconds)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (inc.detected_at.isoformat(), inc.namespace, inc.deployment, inc.pod,
             inc.container, inc.signal, dx.rule, dx.cause, out.action, out.status,
             out.detail, out.recovery_seconds),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def actions_since(self, namespace: str, deployment: str, minutes: int = 60,
                      action: str | None = None) -> int:
        """How many recovery actions were taken on this Deployment recently."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        sql = ("SELECT COUNT(*) FROM incidents WHERE namespace=? AND deployment=?"
               " AND detected_at>=? AND status IN ('recovered','failed')")
        args: list = [namespace, deployment, since]
        if action:
            sql += " AND action=?"
            args.append(action)
        return int(self.db.execute(sql, args).fetchone()[0])

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)))

    # -- good revisions ------------------------------------------------------
    def mark_good(self, namespace: str, deployment: str, revision: int) -> None:
        self.db.execute(
            "INSERT INTO good_revisions VALUES (?,?,?,?) ON CONFLICT(namespace, deployment)"
            " DO UPDATE SET revision=excluded.revision, verified_at=excluded.verified_at",
            (namespace, deployment, revision, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def good_revision(self, namespace: str, deployment: str) -> int | None:
        row = self.db.execute(
            "SELECT revision FROM good_revisions WHERE namespace=? AND deployment=?",
            (namespace, deployment)).fetchone()
        return int(row[0]) if row else None
