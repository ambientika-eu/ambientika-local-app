"""
SQLite-Historienspeicher fuer die lokale Messwert-Historie.
==========================================================

* Ein Datensatz je (Seriennummer, Zeitstempel), Zeit in UTC/ISO 8601.
* UNIQUE(serial, ts_utc) -> idempotentes Schreiben (doppelte Ticks
  aktualisieren statt zu duplizieren).
* Konfigurierbares Intervall und konfigurierbare Aufbewahrung (Default 2 Jahre).
* Rohwerte (Prozent, VOC, dBm) + abgeleitete numerische Felder + Textwerte.

Keine externen Abhaengigkeiten - nur die Python-Standardbibliothek (sqlite3).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
@dataclass
class HistoryConfig:
    db_path: str = os.getenv("HISTORY_DB", "history.db")
    # Aufzeichnungsintervall in Sekunden (Default 5 Minuten), konfigurierbar.
    interval_seconds: int = int(os.getenv("HISTORY_INTERVAL", "300"))
    # Aufbewahrung in Tagen (Default 2 Jahre = 730 Tage), konfigurierbar.
    retention_days: int = int(os.getenv("HISTORY_RETENTION_DAYS", "730"))
    # Publiziert den numerischen Wertesatz zusaetzlich per MQTT.
    mqtt_publish: bool = os.getenv("HISTORY_MQTT", "true").lower() == "true"


# Spaltenreihenfolge = Exportreihenfolge. Text- und *_num-Felder gemeinsam.
COLUMNS: List[str] = [
    "ts_utc", "serial", "device_id", "device_name",
    "role", "role_num", "zone",
    "temperature", "humidity",
    "air_quality_voc", "air_quality", "air_quality_num",
    "fan_speed_pct", "fan_speed", "fan_speed_num",
    "mode_reported", "mode_reported_num",
    "mode_effective", "mode_effective_num",
    "humidity_threshold",
    "filter_status", "filter_status_num",
    "humidity_alarm", "night_mode",
    "rssi", "online",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_history (
    id                  INTEGER PRIMARY KEY,
    ts_utc              TEXT    NOT NULL,   -- ISO 8601 UTC, z.B. 2026-07-31T12:00:00Z
    serial              TEXT    NOT NULL,   -- fachlicher Schluessel
    device_id           TEXT,              -- MQTT-Topic-ID
    device_name         TEXT,
    role                TEXT,              -- 'Master' | 'Slave'   (falls Bridge liefert)
    role_num            INTEGER,           -- 0 Slave, 1 Master
    zone                TEXT,              -- (falls Bridge liefert)
    temperature         REAL,              -- °C
    humidity            INTEGER,           -- % rF
    air_quality_voc     INTEGER,           -- VOC-Rohwert
    air_quality         TEXT,              -- 5-stufige Kategorie
    air_quality_num     INTEGER,           -- 0..4 (hoeher = besser)
    fan_speed_pct       INTEGER,           -- 0..100 % (Rohwert inkl. Nacht)
    fan_speed           TEXT,              -- grobe Stufe
    fan_speed_num       INTEGER,           -- 0..3
    mode_reported       TEXT,              -- am Geraet gemeldeter Modus
    mode_reported_num   INTEGER,
    mode_effective      TEXT,              -- tatsaechlicher/Zonen-Modus (falls Bridge liefert)
    mode_effective_num  INTEGER,
    humidity_threshold  INTEGER,           -- % rF (falls Bridge liefert)
    filter_status       TEXT,              -- 'gruen' | 'gelb' | 'rot'
    filter_status_num   INTEGER,           -- 0..2 (hoeher = dringlicher)
    humidity_alarm      INTEGER,           -- 0/1 (falls Bridge liefert)
    night_mode          INTEGER,           -- 0/1 (falls Bridge liefert)
    rssi                INTEGER,           -- dBm (Funkguete)
    online              INTEGER,           -- 0/1
    UNIQUE(serial, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_hist_serial_ts ON device_history (serial, ts_utc);
CREATE INDEX IF NOT EXISTS idx_hist_ts        ON device_history (ts_utc);
"""


def utc_iso(dt: Optional[datetime] = None) -> str:
    """Aktueller (oder uebergebener) Zeitpunkt als ISO-8601-UTC-String."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HistoryStore:
    def __init__(self, config: Optional[HistoryConfig] = None):
        self.config = config or HistoryConfig()
        self._conn = sqlite3.connect(self.config.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- Schreiben ---------------------------------------------------------
    def insert(self, record: Dict[str, Any]) -> None:
        """Idempotenter Upsert eines normalisierten Datensatzes (siehe recorder)."""
        cols = [c for c in COLUMNS if c in record]
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("serial", "ts_utc"))
        sql = (
            f"INSERT INTO device_history ({collist}) VALUES ({placeholders}) "
            f"ON CONFLICT(serial, ts_utc) DO UPDATE SET {updates}"
        )
        self._conn.execute(sql, [record.get(c) for c in cols])
        self._conn.commit()

    def insert_many(self, records: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for r in records:
            self.insert(r)
            n += 1
        return n

    # -- Lesen -------------------------------------------------------------
    def query(
        self,
        serial: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where, params = [], []
        if serial:
            where.append("serial = ?"); params.append(serial)
        if start:
            where.append("ts_utc >= ?"); params.append(start)
        if end:
            where.append("ts_utc <= ?"); params.append(end)
        sql = "SELECT * FROM device_history"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts_utc ASC, serial ASC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM device_history").fetchone()[0]

    # -- Aufbewahrung ------------------------------------------------------
    def purge(self, retention_days: Optional[int] = None) -> int:
        """Loescht Datensaetze aelter als die Aufbewahrungsfrist. Gibt Anzahl zurueck."""
        days = self.config.retention_days if retention_days is None else retention_days
        cutoff = utc_iso(datetime.now(timezone.utc) - timedelta(days=days))
        cur = self._conn.execute("DELETE FROM device_history WHERE ts_utc < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    def vacuum(self) -> None:
        self._conn.execute("VACUUM;")

    def close(self) -> None:
        self._conn.close()
