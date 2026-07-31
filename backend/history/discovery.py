"""
MQTT-Zweig + Home-Assistant-Discovery fuer die Historie.
========================================================

Zweck laut Spezifikation:
  Bei den NUMERISCHEN Sensoren device_class und state_class="measurement"
  setzen. Dann fuehrt Home Assistant die Langzeitstatistik selbst -> der
  Umweg ueber einen Export entfaellt. Genau dafuer existieren die *_num-Felder:
  reine Text-/Enum-Sensoren bekommen in HA keine numerische Statistik, die
  numerische Variante schon.

Die Discovery-Payloads werden hier als reine Funktionen gebaut (ohne Broker),
d.h. voll testbar. publish_* nimmt einen bereits verbundenen paho-mqtt-Client
entgegen (Dependency Injection) - kein Broker noetig, um die Payloads zu pruefen.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Feld -> (HA-Komponente, device_class, state_class, Einheit, Anzeigename)
# state_class="measurement" auf allen numerischen Feldern => HA-Langzeitstatistik.
SENSOR_SPEC: List[Dict[str, Any]] = [
    # numerische Sensoren (Langzeitstatistik)
    {"field": "temperature",       "component": "sensor",        "device_class": "temperature",     "state_class": "measurement", "unit": "°C",  "name": "Temperatur"},
    {"field": "humidity",          "component": "sensor",        "device_class": "humidity",        "state_class": "measurement", "unit": "%",   "name": "Luftfeuchtigkeit"},
    {"field": "air_quality_voc",   "component": "sensor",        "device_class": "volatile_organic_compounds_parts", "state_class": "measurement", "unit": "ppb", "name": "Luftqualitaet VOC"},
    {"field": "air_quality_num",   "component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": None,  "name": "Luftqualitaet Stufe"},
    {"field": "fan_speed_pct",     "component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": "%",   "name": "Luefter"},
    {"field": "fan_speed_num",     "component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": None,  "name": "Luefterstufe"},
    {"field": "mode_reported_num", "component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": None,  "name": "Modus (num)"},
    {"field": "mode_effective_num","component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": None,  "name": "Modus Zone (num)"},
    {"field": "filter_status_num", "component": "sensor",        "device_class": None,              "state_class": "measurement", "unit": None,  "name": "Filterstatus (num)"},
    {"field": "humidity_threshold","component": "sensor",        "device_class": "humidity",        "state_class": "measurement", "unit": "%",   "name": "Feuchteschwelle"},
    {"field": "rssi",              "component": "sensor",        "device_class": "signal_strength", "state_class": "measurement", "unit": "dBm", "name": "Funkguete"},
    # Text-/Enum-Sensoren (Anzeige, keine numerische Statistik)
    {"field": "air_quality",       "component": "sensor",        "device_class": "enum",           "state_class": None,          "unit": None,  "name": "Luftqualitaet"},
    {"field": "mode_reported",     "component": "sensor",        "device_class": "enum",           "state_class": None,          "unit": None,  "name": "Modus"},
    {"field": "mode_effective",    "component": "sensor",        "device_class": "enum",           "state_class": None,          "unit": None,  "name": "Modus Zone"},
    {"field": "filter_status",     "component": "sensor",        "device_class": "enum",           "state_class": None,          "unit": None,  "name": "Filterstatus"},
    # Bool -> binary_sensor
    {"field": "humidity_alarm",    "component": "binary_sensor", "device_class": "moisture",        "state_class": None,          "unit": None,  "name": "Feuchtealarm", "payload": (1, 0)},
    {"field": "night_mode",        "component": "binary_sensor", "device_class": "running",         "state_class": None,          "unit": None,  "name": "Nachtmodus",   "payload": (1, 0)},
    {"field": "online",            "component": "binary_sensor", "device_class": "connectivity",    "state_class": None,          "unit": None,  "name": "Online",       "payload": (1, 0)},
]


def state_topic(prefix: str, serial: str) -> str:
    """Gemeinsames numerisches State-Topic je Geraet (JSON, retained)."""
    return f"{prefix}/history/{serial}/state"


def build_state_payload(record: Dict[str, Any]) -> str:
    """JSON-Payload mit dem kompletten Wertesatz (Text + Zahl)."""
    return json.dumps(record, ensure_ascii=False)


def build_discovery_configs(prefix: str, record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Baut je Feld eine (topic, payload)-Discovery-Konfiguration.
    Rueckgabe: Liste von {"topic":..., "payload": {...}}.
    """
    serial = record.get("serial")
    name = record.get("device_name") or serial
    st = state_topic(prefix, serial)
    device_block = {
        "identifiers": [serial],
        "name": name,
        "manufacturer": "Ambientika",
        "model": "Ambientika Smart",
    }
    configs: List[Dict[str, Any]] = []
    for spec in SENSOR_SPEC:
        field = spec["field"]
        if field not in record:
            continue
        object_id = f"ambientika_{serial}_{field}"
        payload: Dict[str, Any] = {
            "name": spec["name"],
            "unique_id": object_id,
            "state_topic": st,
            "value_template": "{{ value_json.%s }}" % field,
            "device": device_block,
        }
        if spec.get("unit"):
            payload["unit_of_measurement"] = spec["unit"]
        if spec.get("device_class"):
            payload["device_class"] = spec["device_class"]
        if spec.get("state_class"):
            payload["state_class"] = spec["state_class"]
        if spec["component"] == "binary_sensor":
            on, off = spec.get("payload", (1, 0))
            payload["payload_on"] = on
            payload["payload_off"] = off
        if spec["device_class"] == "enum" and field == "filter_status":
            payload["options"] = ["gruen", "gelb", "rot"]
        topic = f"homeassistant/{spec['component']}/{object_id}/config"
        configs.append({"topic": topic, "payload": payload})
    return configs


# ---------------------------------------------------------------------------
# Publish-Helfer (paho-mqtt-Client injiziert; kein Broker im Test noetig)
# ---------------------------------------------------------------------------
def publish_discovery(client, prefix: str, record: Dict[str, Any]) -> int:
    n = 0
    for cfg in build_discovery_configs(prefix, record):
        client.publish(cfg["topic"], json.dumps(cfg["payload"], ensure_ascii=False),
                       qos=1, retain=True)
        n += 1
    return n


def publish_state(client, prefix: str, record: Dict[str, Any]) -> str:
    topic = state_topic(prefix, record.get("serial"))
    client.publish(topic, build_state_payload(record), qos=1, retain=True)
    return topic
