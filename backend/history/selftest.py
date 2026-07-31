"""
End-to-End-Selbsttest der Historie - OHNE Broker und OHNE Hardware.
==================================================================

Simuliert das Resch-Szenario (5 Geraete in 3 Zonen), zeichnet mehrere
Zeitpunkte auf und prueft:
  * Schema/Insert + idempotenter Upsert (UNIQUE serial, ts_utc)
  * Mappings (Modus, Luftqualitaet, Luefterstufe, Filterstatus)
  * CSV/JSON-Export inkl. Text- und Num-Feldern
  * HA-Discovery: numerische Sensoren tragen state_class=measurement
  * Aufbewahrung/Purge

Aufruf:  python -m history.selftest      (aus dem backend/-Verzeichnis)
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

from .store import HistoryConfig, utc_iso
from .sampler import HistorySampler
from . import discovery, exporter


class FakeMQTT:
    """Minimaler paho-kompatibler Client, der Publishes nur mitschreibt."""
    def __init__(self):
        self.published = []
    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "retain": retain})


# --- Resch-Szenario: 5 Geraete, 3 Zonen. Payload-Form = Bridge-Status. -----
def scenario_devices():
    return {
        # Zone EG - Master (mit Vorwaerts-Feldern zone/role, die die Bridge
        # heute noch nicht liefert - zeigt die automatische Uebernahme).
        "DEV001": {"deviceId": "DEV001", "serial": "AMB-2024-001", "name": "Wohnzimmer",
                   "zone": "Erdgeschoss", "role": "Master",
                   "mode": "SMART", "fanSpeed": 40, "temperature": 21.9, "humidity": 48,
                   "airQuality": 420, "filterAlarm": False, "online": True, "rssi": -55},
        "DEV002": {"deviceId": "DEV002", "serial": "AMB-2024-002", "name": "Kueche",
                   "zone": "Erdgeschoss", "role": "Slave",
                   "mode": "HRV", "fanSpeed": 75, "temperature": 22.4, "humidity": 55,
                   "airQuality": 850, "filterAlarm": False, "online": True, "rssi": -61},
        # Zone OG
        "DEV003": {"deviceId": "DEV003", "serial": "AMB-2024-003", "name": "Schlafzimmer",
                   "zone": "Obergeschoss", "role": "Master",
                   "mode": "NIGHT", "fanSpeed": 20, "temperature": 20.1, "humidity": 60,
                   "airQuality": 1200, "filterAlarm": True, "online": True, "rssi": -70},
        "DEV004": {"deviceId": "DEV004", "serial": "AMB-2024-004", "name": "Bad",
                   "zone": "Obergeschoss", "role": "Slave",
                   "mode": "BOOST", "fanSpeed": 100, "temperature": 23.0, "humidity": 68,
                   "airQuality": 1600, "filterAlarm": False, "online": True, "rssi": -66},
        # Zone Keller - minimaler Payload (nur die heute garantierten Felder)
        "DEV005": {"deviceId": "DEV005", "serial": "AMB-2024-005", "name": "Keller",
                   "mode": "ECO", "fanSpeed": 0, "temperature": 18.5, "humidity": 72,
                   "airQuality": 300, "filterAlarm": False, "online": True, "rssi": -80},
    }


def main():
    outdir = os.getenv("HISTORY_OUT", tempfile.mkdtemp(prefix="ambientika_hist_"))
    os.makedirs(outdir, exist_ok=True)
    db = os.path.join(outdir, "history.db")
    if os.path.exists(db):
        os.remove(db)

    cfg = HistoryConfig(db_path=db, interval_seconds=300, retention_days=730)
    mqtt = FakeMQTT()
    sampler = HistorySampler(get_devices=scenario_devices, config=cfg,
                             mqtt_client=mqtt, mqtt_prefix="ambientika")

    # Drei Aufzeichnungszeitpunkte im 5-Minuten-Takt.
    base = datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        sampler.sample_once(utc_iso(base + timedelta(minutes=5 * i)))

    store = sampler.store

    # ---- Pruefungen --------------------------------------------------------
    assert store.count() == 15, f"erwartet 15 Zeilen, ist {store.count()}"

    # Idempotenz: gleicher Zeitpunkt erneut -> kein Zuwachs
    sampler.sample_once(utc_iso(base))
    assert store.count() == 15, f"Upsert nicht idempotent: {store.count()}"

    r2 = store.query(serial="AMB-2024-002")[0]
    assert r2["mode_reported"] == "HRV" and r2["mode_reported_num"] == 2
    assert r2["air_quality_voc"] == 850 and r2["air_quality"] == "befriedigend" and r2["air_quality_num"] == 2
    assert r2["fan_speed_pct"] == 75 and r2["fan_speed_num"] == 3
    assert r2["filter_status"] == "gruen" and r2["filter_status_num"] == 0
    assert r2["role"] == "Slave" and r2["role_num"] == 0

    r3 = store.query(serial="AMB-2024-003")[0]
    assert r3["mode_reported"] == "NIGHT" and r3["night_mode"] == 1
    assert r3["filter_status"] == "rot" and r3["filter_status_num"] == 2
    assert r3["air_quality_num"] == 1  # voc 1200 -> maessig

    # Feld, das die Bridge heute NICHT liefert -> NULL, bricht nicht
    r5 = store.query(serial="AMB-2024-005")[0]
    assert r5["zone"] is None and r5["role"] is None and r5["humidity_threshold"] is None

    # ---- Export ------------------------------------------------------------
    csv_text = exporter.export(store, "csv")
    json_text = exporter.export(store, "json")
    with open(os.path.join(outdir, "sample_history.csv"), "w") as f:
        f.write(csv_text)
    with open(os.path.join(outdir, "sample_history.json"), "w") as f:
        f.write(json_text)

    # ---- HA-Discovery ------------------------------------------------------
    cfgs = discovery.build_discovery_configs("ambientika", r2)
    temp_cfg = next(c for c in cfgs if c["payload"]["unique_id"].endswith("temperature"))
    assert temp_cfg["payload"]["device_class"] == "temperature"
    assert temp_cfg["payload"]["state_class"] == "measurement"
    # numerische _num-Felder tragen state_class=measurement
    aqn = next(c for c in cfgs if c["payload"]["unique_id"].endswith("air_quality_num"))
    assert aqn["payload"]["state_class"] == "measurement"
    with open(os.path.join(outdir, "sample_ha_discovery.json"), "w") as f:
        f.write(json.dumps([c["payload"] for c in cfgs], ensure_ascii=False, indent=2))

    # ---- Aufbewahrung ------------------------------------------------------
    old_ts = utc_iso(base - timedelta(days=800))
    store.insert({"ts_utc": old_ts, "serial": "AMB-2024-001", "temperature": 1.0})
    before = store.count()
    deleted = store.purge()
    assert deleted == 1 and store.count() == before - 1

    # ---- Zusammenfassung ---------------------------------------------------
    print("OK - alle Pruefungen bestanden")
    print(f"  Zeilen in der DB      : {store.count()}")
    print(f"  MQTT-Publishes (fake) : {len(mqtt.published)}  (State + Discovery)")
    print(f"  Discovery-Sensoren    : {len(cfgs)} je Geraet")
    print(f"  Ausgabeverzeichnis    : {outdir}")
    print("  Beispielzeile (Kueche, HRV):")
    print("   ", json.dumps({k: r2[k] for k in (
        "ts_utc", "serial", "mode_reported", "mode_reported_num",
        "air_quality_voc", "air_quality", "air_quality_num",
        "fan_speed_pct", "fan_speed_num", "filter_status", "filter_status_num",
        "rssi")}, ensure_ascii=False))
    store.close()
    return outdir


if __name__ == "__main__":
    main()
