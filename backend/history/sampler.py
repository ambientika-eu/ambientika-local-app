"""
Sampler: periodische Aufzeichnung des aktuellen Geraetezustands.
================================================================

Laeuft als asyncio-Task in der FastAPI-lifespan. Alle `interval_seconds`
(Default 300 s = 5 min):
  1. ueber alle bekannten Geraete iterieren (das `devices`-Dict des Backends),
  2. je Geraet einen Datensatz bauen (recorder) und in SQLite schreiben,
  3. optional den numerischen Wertesatz + HA-Discovery per MQTT publizieren,
  4. taeglich einmal die Aufbewahrung durchsetzen (purge).

Der Zugriff auf die Geraete erfolgt ueber einen Callback `get_devices()`, damit
das Modul nicht an eine konkrete Backend-Variable gekoppelt ist (Testbarkeit).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from . import discovery
from .recorder import build_record
from .store import HistoryStore, HistoryConfig, utc_iso

logger = logging.getLogger("ambientika-history")


class HistorySampler:
    def __init__(
        self,
        get_devices: Callable[[], Dict[str, Dict[str, Any]]],
        config: Optional[HistoryConfig] = None,
        mqtt_client: Any = None,
        mqtt_prefix: str = "ambientika",
    ):
        self.get_devices = get_devices
        self.store = HistoryStore(config)
        self.config = self.store.config
        self.mqtt_client = mqtt_client
        self.mqtt_prefix = mqtt_prefix
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._discovery_sent: set = set()
        self._last_purge_day: Optional[str] = None

    # -- eine Aufzeichnungsrunde (auch einzeln testbar) --------------------
    def sample_once(self, ts_utc: Optional[str] = None) -> int:
        ts = ts_utc or utc_iso()
        n = 0
        for device_id, state in list(self.get_devices().items()):
            record = build_record(device_id, state, ts)
            self.store.insert(record)
            n += 1
            if self.config.mqtt_publish and self.mqtt_client is not None:
                serial = record["serial"]
                if serial not in self._discovery_sent:
                    discovery.publish_discovery(self.mqtt_client, self.mqtt_prefix, record)
                    self._discovery_sent.add(serial)
                discovery.publish_state(self.mqtt_client, self.mqtt_prefix, record)
        logger.info("history: %d Geraete aufgezeichnet @ %s", n, ts)
        return n

    def maybe_purge(self) -> None:
        today = utc_iso()[:10]
        if today != self._last_purge_day:
            deleted = self.store.purge()
            self._last_purge_day = today
            if deleted:
                logger.info("history: %d alte Datensaetze entfernt (Aufbewahrung %d Tage)",
                            deleted, self.config.retention_days)

    # -- Dauerlauf ---------------------------------------------------------
    async def _run(self) -> None:
        logger.info("history-sampler gestartet (Intervall %ds, Aufbewahrung %d Tage)",
                    self.config.interval_seconds, self.config.retention_days)
        while not self._stop.is_set():
            try:
                self.maybe_purge()
                self.sample_once()
            except Exception as exc:  # nie den Task sterben lassen
                logger.exception("history-sampler Fehler: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.interval_seconds)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        self.store.close()
