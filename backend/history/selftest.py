"""
End-to-End-Selbsttest der Historie - OHNE Broker und OHNE Hardware.
==================================================================

Simuliert das Resch-Szenario (5 Geraete in 3 Zonen) mit den REALEN
Bridge-State-Payloads (operating_mode, fan_speed, air_quality, filters_status,
device_role, zone_index ...), zeichnet mehrere Zeitpunkte auf und prueft:
  * Schema/Insert + idempotenter Upsert (UNIQUE serial, ts_utc)
  * Mappings (OperatingMode-Enum, Luftqualitaet als String UND als Zahl,
    Luefterstufe, Filterampel, Feuchtestufe, Rolle)
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


# --- Resch-Szenario: 5 Geraete, 3 Zonen. Payload-Form = REALER Bridge-State. -
def scenario_devices():
    return {
        # Zone 0 - Master (air_quality als String)
        "AMB-2024-001": {"serial": "AMB-2024-001", "name": "Wohnzimmer",
                         "operating_mode": "Smart", "fan_speed": "Medium",
                         "humidity_level": "Normal", "light_sensor_level": "Off",
                         "temperature": 21.9, "humidity": 48, "air_quality": "good",
                         "humidity_alarm": False, "filters_status": "green",
                         "night_alarm": False, "device_role": "Master",
                         "last_operating_mode": "Auto", "zone_index": 0, "online": True},
        # Zone 0 - Slave (Filter gelb, Feuchte Moist)
        "AMB-2024-002": {"serial": "AMB-2024-002", "name": "Kueche",
                         "operating_mode": "ManualHeatRecovery", "fan_speed": "High",
                         "humidity_level": "Moist", "light_sensor_level": "Off",
                         "temperature": 22.4, "humidity": 55, "air_quality": "moderate",
                         "humidity_alarm": False, "filters_status": "yellow",
                         "night_alarm": False, "device_role": "Slave",
                         "last_operating_mode": "Smart", "zone_index": 0, "online": True},
        # Zone 1 - Master (Nacht, Filter rot)
        "AMB-2024-003": {"serial": "AMB-2024-003", "name": "Schlafzimmer",
                         "operating_mode": "Night", "fan_speed": "Low",
                         "humidity_level": "Dry", "light_sensor_level": "Low",
                         "temperature": 20.1, "humidity": 60, "air_quality": "bad",
                         "humidity_alarm": False, "filters_status": "red",
                         "night_alarm": True, "device_role": "Master",
                         "last_operating_mode": "Night", "zone_index": 1, "online": True},
        # Zone 1 - Slave (air_quality als ZAHL -> VOC-Pfad)
        "AMB-2024-004": {"serial": "AMB-2024-004", "name": "Bad",
                         "operating_mode": "Expulsion", "fan_speed": "High",
                         "humidity_level": "Normal", "light_sensor_level": "NotAvailable",
                         "temperature": 23.0, "humidity": 68, "air_quality": 1200,
                         "humidity_alarm": True, "filters_status": "green",
                         "night_alarm": False, "device_role": "Slave",
                         "last_operating_mode": "Auto", "zone_index": 1, "online": True},
        # Zone 2 - minimaler Payload (nur garantierte Felder, kein air_quality/role)
        "AMB-2024-005": {"serial": "AMB-2024-005", "name": "Keller",
                         "operating_mode": "Off", "fan_speed": "Low",
                         "temperature": 18.5, "humidity": 72,
                         "filters_status": "green", "zone_index": 2, "online": True},
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

    # Master EG: Smart(0), Luft "good"->gut(3), Fan Medium(2), Filter gruen(0),
    #            Rolle Master(1), Feuchte Normal(1), letzter Modus Auto(1), Zone 0
    r1 = store.query(serial="AMB-2024-001")[0]
    assert r1["mode_reported"] == "Smart" and r1["mode_reported_num"] == 0
    assert r1["air_quality"] == "gut" and r1["air_quality_num"] == 3 and r1["air_quality_voc"] is None
    assert r1["fan_speed"] == "Medium" and r1["fan_speed_num"] == 2
    assert r1["filter_status"] == "gruen" and r1["filter_status_num"] == 0
    assert r1["role"] == "Master" and r1["role_num"] == 1
    assert r1["humidity_level"] == "Normal" and r1["humidity_level_num"] == 1
    assert r1["mode_last"] == "Auto" and r1["mode_last_num"] == 1
    assert r1["zone_index"] == 0 and r1["night_mode"] == 0

    # Slave EG: ManualHeatRecovery(2), Luft "moderate"->befriedigend(2),
    #           Fan High(3), Filter gelb(1), Rolle Slave(0), Feuchte Moist(2)
    r2 = store.query(serial="AMB-2024-002")[0]
    assert r2["mode_reported"] == "ManualHeatRecovery" and r2["mode_reported_num"] == 2
    assert r2["air_quality"] == "befriedigend" and r2["air_quality_num"] == 2
    assert r2["fan_speed_num"] == 3 and r2["filter_status"] == "gelb" and r2["filter_status_num"] == 1
    assert r2["role_num"] == 0 and r2["humidity_level_num"] == 2

    # Master OG: Night(3) -> night_mode 1, Filter rot(2), Luft bad->schlecht(0),
    #            Lichtsensor Low(2), Nachtalarm 1
    r3 = store.query(serial="AMB-2024-003")[0]
    assert r3["mode_reported_num"] == 3 and r3["night_mode"] == 1
    assert r3["filter_status"] == "rot" and r3["filter_status_num"] == 2
    assert r3["air_quality"] == "schlecht" and r3["air_quality_num"] == 0
    assert r3["light_sensor_level_num"] == 2 and r3["night_alarm"] == 1

    # Slave OG: air_quality ZAHL 1200 -> VOC-Pfad: voc 1200, maessig(1)
    r4 = store.query(serial="AMB-2024-004")[0]
    assert r4["air_quality_voc"] == 1200 and r4["air_quality"] == "maessig" and r4["air_quality_num"] == 1
    assert r4["mode_reported"] == "Expulsion" and r4["mode_reported_num"] == 7
    assert r4["humidity_alarm"] == 1

    # Zone 2: Felder, die die Bridge hier NICHT liefert -> NULL, bricht nicht
    r5 = store.query(serial="AMB-2024-005")[0]
    assert r5["air_quality"] is None and r5["air_quality_num"] is None
    assert r5["role"] is None and r5["humidity_level"] is None
    assert r5["mode_reported"] == "Off" and r5["mode_reported_num"] == 11

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
    # Filter-Enum traegt options
    fil = next(c for c in cfgs if c["payload"]["unique_id"].endswith("filter_status"))
    assert fil["payload"].get("options") == ["gruen", "gelb", "rot"]
    # air_quality_voc fehlt bei String-Geraet (r2) -> kein toter Sensor
    assert not any(c["payload"]["unique_id"].endswith("air_quality_voc") for c in cfgs)
    # ...ist aber beim Zahl-Geraet (r4) vorhanden
    cfgs4 = discovery.build_discovery_configs("ambientika", r4)
    assert any(c["payload"]["unique_id"].endswith("air_quality_voc") for c in cfgs4)
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
    print(f"  Discovery-Sensoren    : {len(cfgs)} (Kueche, String-Luftqualitaet)")
    print(f"  Ausgabeverzeichnis    : {outdir}")
    print("  Beispielzeile (Bad, air_quality als Zahl):")
    print("   ", json.dumps({k: r4[k] for k in (
        "ts_utc", "serial", "mode_reported", "mode_reported_num",
        "air_quality_voc", "air_quality", "air_quality_num",
        "fan_speed", "fan_speed_num", "filter_status", "filter_status_num")},
        ensure_ascii=False))
    store.close()
    return outdir


if __name__ == "__main__":
    main()
