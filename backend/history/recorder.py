"""
Recorder: Geraetezustand (Bridge-Status-Payload) -> normalisierter Datensatz.
============================================================================

Eingabe ist der Zustand, wie ihn das Backend in `devices[device_id]` haelt -
d.h. der Bridge-Status-Payload plus evtl. Zusatzfelder. Beispiel:

    {"deviceId":"DEV001","serial":"AMB-2024-001","name":"Bedroom",
     "mode":"HRV","fanSpeed":75,"temperature":21.5,"humidity":52,
     "airQuality":850,"filterAlarm":false,"online":true,"rssi":-58}

Der Recorder ist tolerant: fehlende Felder werden zu NULL, zusaetzliche
(zukuenftige) Felder wie zone/role/humidityThreshold/nightMode/modeEffective
werden automatisch uebernommen, sobald die Bridge sie liefert.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import mappings as M
from .store import utc_iso


def _first(d: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Erster vorhandener (nicht-None) Wert unter mehreren moeglichen Keys."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def build_record(device_id: str, state: Dict[str, Any], ts_utc: Optional[str] = None) -> Dict[str, Any]:
    """
    Baut einen Datensatz gemaess Historien-Schema aus einem Geraetezustand.
    device_id ist der MQTT-Topic-Key; serial kommt aus dem Payload
    (Fallback: device_id, damit UNIQUE(serial, ts_utc) immer greift).
    """
    ts = ts_utc or utc_iso()
    serial = _first(state, "serial", "serialNumber") or device_id

    # Rohwerte aus dem realen Payload
    voc = _first(state, "airQuality", "voc", "airQualityVoc")
    fan_pct = _first(state, "fanSpeed", "fanSpeedPct")
    mode_rep = _first(state, "mode", "modeReported")
    mode_eff = _first(state, "modeEffective", "zoneMode", "masterMode")
    role = _first(state, "role")
    filter_alarm = _first(state, "filterAlarm")
    filter_status_text = _first(state, "filterStatus")

    # Abgeleitete Kategorien (Text + Zahl)
    aq_text, aq_num = M.air_quality_from_voc(voc)
    fan_text, fan_num = M.fan_stage_from_percent(fan_pct)
    fil_text, fil_num = M.filter_status_from_alarm(filter_alarm, filter_status_text)

    record: Dict[str, Any] = {
        "ts_utc": ts,
        "serial": serial,
        "device_id": device_id,
        "device_name": _first(state, "name", "deviceName"),
        "role": role,
        "role_num": M.role_to_num(role),
        "zone": _first(state, "zone"),
        "temperature": _first(state, "temperature"),
        "humidity": _first(state, "humidity"),
        "air_quality_voc": voc,
        "air_quality": aq_text,
        "air_quality_num": aq_num,
        "fan_speed_pct": fan_pct,
        "fan_speed": fan_text,
        "fan_speed_num": fan_num,
        "mode_reported": mode_rep,
        "mode_reported_num": M.mode_to_num(mode_rep),
        "mode_effective": mode_eff,
        "mode_effective_num": M.mode_to_num(mode_eff),
        "humidity_threshold": _first(state, "humidityThreshold", "humidity_threshold"),
        "filter_status": fil_text,
        "filter_status_num": fil_num,
        "humidity_alarm": M.bool_to_int(_first(state, "humidityAlarm")),
        "night_mode": M.bool_to_int(
            _first(state, "nightMode")
            if _first(state, "nightMode") is not None
            else (str(mode_rep).upper() == "NIGHT" if mode_rep is not None else None)
        ),
        "rssi": _first(state, "rssi", "signal"),
        "online": M.bool_to_int(_first(state, "online")),
    }
    return record
