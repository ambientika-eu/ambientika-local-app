"""
SQLite-Speicher des Free-Cooling-Wächters.
==========================================

Drei Tabellen in derselben Datei wie die Messwert-Historie (HISTORY_DB):

  freecooling_config   eine Zeile (id=1), Konfiguration als JSON
  freecooling_state    Laufzeitzustand je Gerät (kühlt? seit wann? Rückkehrmodus),
                       damit ein Neustart des Backends ein laufendes Kühlen nicht
                       "vergisst" und das Gerät im Luftstrom-Modus stehen bleibt
  freecooling_log      jede Umschaltung mit Innen-/Außenwert, Quelle, Alter des
                       Außenwerts, Modus vorher/nachher und Grund — der Nachweis

Nur Python-Standardbibliothek (sqlite3, json).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

LOG_COLUMNS: List[str] = [
    "ts_utc", "serial", "device_name", "event", "reason",
    "t_in", "t_out", "t_out_source", "t_out_age_s", "delta",
    "mode_from", "fan_from", "mode_to", "fan_to", "night",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS freecooling_config (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    json    TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS freecooling_state (
    serial  TEXT PRIMARY KEY,
    json    TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS freecooling_log (
    id            INTEGER PRIMARY KEY,
    ts_utc        TEXT NOT NULL,     -- ISO 8601 UTC
    serial        TEXT NOT NULL,
    device_name   TEXT,
    event         TEXT NOT NULL,     -- start | end | override | smart_blocked | fan | config
    reason        TEXT,
    t_in          REAL,              -- Innenwert (Master-Sensor), °C
    t_out         REAL,              -- verwendeter Außenwert, °C
    t_out_source  TEXT,              -- sensor:<name> | weather:<name>
    t_out_age_s   INTEGER,           -- Alter des Außenwerts beim Umschalten, s
    delta         REAL,              -- t_in - t_out, K
    mode_from     TEXT,
    fan_from      TEXT,
    mode_to       TEXT,
    fan_to        TEXT,
    night         INTEGER            -- 0/1: Nachtstufe verwendet
);
CREATE INDEX IF NOT EXISTS idx_fc_log_ts        ON freecooling_log (ts_utc);
CREATE INDEX IF NOT EXISTS idx_fc_log_serial_ts ON freecooling_log (serial, ts_utc);
"""


def utc_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> str:
    """Gleiche Datei wie die Historie, damit nur ein Volume gepflegt wird."""
    return os.getenv("FREECOOLING_DB") or os.getenv("HISTORY_DB", "history.db")


class FreeCoolingStore:
    def __init__(self, db_path: Optional[str] = None,
                 log_retention_days: Optional[int] = None):
        self.db_path = db_path or default_db_path()
        self.log_retention_days = (
            log_retention_days if log_retention_days is not None
            else int(os.getenv("FREECOOLING_LOG_RETENTION_DAYS", "400"))
        )
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- Konfiguration -----------------------------------------------------
    def load_config(self) -> Optional[Dict[str, Any]]:
        row = self._conn.execute("SELECT json FROM freecooling_config WHERE id = 1").fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["json"])
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save_config(self, config: Dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO freecooling_config (id, json, updated) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET json = excluded.json, updated = excluded.updated",
            (json.dumps(config, sort_keys=True), utc_iso()),
        )
        self._conn.commit()

    # -- Laufzeitzustand je Geraet ------------------------------------------
    def load_states(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in self._conn.execute("SELECT serial, json FROM freecooling_state").fetchall():
            try:
                data = json.loads(row["json"])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                out[row["serial"]] = data
        return out

    def save_state(self, serial: str, state: Dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO freecooling_state (serial, json, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(serial) DO UPDATE SET json = excluded.json, updated = excluded.updated",
            (serial, json.dumps(state, sort_keys=True), utc_iso()),
        )
        self._conn.commit()

    def delete_state(self, serial: str) -> None:
        self._conn.execute("DELETE FROM freecooling_state WHERE serial = ?", (serial,))
        self._conn.commit()

    # -- Protokoll ---------------------------------------------------------
    def log(self, record: Dict[str, Any]) -> int:
        rec = dict(record)
        rec.setdefault("ts_utc", utc_iso())
        if isinstance(rec.get("night"), bool):
            rec["night"] = int(rec["night"])
        cols = [c for c in LOG_COLUMNS if c in rec]
        sql = (f"INSERT INTO freecooling_log ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        cur = self._conn.execute(sql, [rec.get(c) for c in cols])
        self._conn.commit()
        return int(cur.lastrowid)

    def query_log(self, serial: Optional[str] = None, start: Optional[str] = None,
                  end: Optional[str] = None, limit: Optional[int] = 500,
                  newest_first: bool = True) -> List[Dict[str, Any]]:
        where, params = [], []
        if serial:
            where.append("serial = ?"); params.append(serial)
        if start:
            where.append("ts_utc >= ?"); params.append(start)
        if end:
            where.append("ts_utc <= ?"); params.append(end)
        sql = "SELECT * FROM freecooling_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts_utc " + ("DESC" if newest_first else "ASC") + ", id " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def log_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM freecooling_log").fetchone()[0])

    def purge_log(self, retention_days: Optional[int] = None) -> int:
        days = self.log_retention_days if retention_days is None else retention_days
        cutoff = utc_iso(datetime.now(timezone.utc) - timedelta(days=days))
        cur = self._conn.execute("DELETE FROM freecooling_log WHERE ts_utc < ?", (cutoff,))
        self._conn.commit()
        return int(cur.rowcount)

    def close(self) -> None:
        self._conn.close()


def log_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Protokollzeilen als CSV (Semikolon, Excel-tauglich, UTF-8)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(LOG_COLUMNS)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in LOG_COLUMNS])
    return buf.getvalue()
