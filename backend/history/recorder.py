"""
Recorder: Geraetezustand (realer Bridge-State) -> normalisierter Datensatz.
============================================================================

Eingabe ist der Zustand, wie ihn das Backend in `devices[serial]` haelt - das
ist der von der Bridge unter <prefix>/<serial>/state publizierte JSON-Payload
(plus die vom Backend ergaenzten Felder serial/name/online/lastSeen). Beispiel:

    {"serial":"AMB-2024-001","name":"Wohnzimmer",
     "operating_mode":"Smart","fan_speed":"Medium","humidity_level":"Normal",
     "light_sensor_level":"Off","temperature":21.9,"humidity":48,
     "air_quality":"good","humidity_alarm":false,"filters_status":"green",
     "night_alarm":false,"device_role":"Master","last_operating_mode":"Night",
     "zone_index":0,"online":true}

Der Recorder ist tolerant: fehlende Felder werden zu NULL, alternative
(Legacy-/Kompat-)Schluesselnamen werden mit abgedeckt.
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
    device_id ist der MQTT-Topic-Key (= Seriennummer); serial kommt aus dem
    Payload (Fallback: device_id, damit UNIQUE(serial, ts_utc) immer greift).
    """
    ts = ts_utc or utc_iso()
    serial = _first(state, "serial", "serialNumber", "device_serial_number") or device_id

    # --- Rohwerte aus dem realen Bridge-State (mit Legacy-Fallbacks) --------
    mode_rep = _first(state, "operating_mode", "mode", "modeReported")
    mode_last = _first(state, "last_operating_mode", "modeEffective", "lastOperatingMode")
    fan_raw = _first(state, "fan_speed", "fanSpeed")
    hum_lvl = _first(state, "humidity_level", "humidityLevel")
    light_lvl = _first(state, "light_sensor_level", "lightSensorLevel")
    role = _first(state, "device_role", "role")
    aq_value = _first(state, "air_quality", "airQuality", "voc")
    filt_value = _first(state, "filters_status", "filterStatus", "filter_status")
    filt_alarm = _first(state, "filterAlarm", "filter_alarm")

    # --- Abgeleitete Kategorien (Text + Zahl) ------------------------------
    aq_voc, aq_text, aq_num = M.air_quality_normalize(aq_value)
    fan_num = M.fan_speed_to_num(fan_raw)
    fil_text, fil_num = M.filter_status_normalize(filt_value, filt_alarm)
    mode_rep_num = M.mode_to_num(mode_rep)

    # Nachtmodus abgeleitet: aktiver Modus == Night (oder Legacy-Feld).
    night_mode = _first(state, "nightMode", "night_mode")
    if night_mode is None and mode_rep is not None:
        night_mode = (mode_rep_num == 3) or (str(mode_rep).strip().lower() == "night")

    record: Dict[str, Any] = {
        "ts_utc": ts,
        "serial": serial,
        "device_id": device_id,
        "device_name": _first(state, "name", "device_name", "deviceName"),
        "role": role,
        "role_num": M.role_to_num(role),
        "zone_index": _first(state, "zone_index", "zoneIndex", "zone"),
        "temperature": _first(state, "temperature"),
        "humidity": _first(state, "humidity"),
        "air_quality_voc": aq_voc,
        "air_quality": aq_text,
        "air_quality_num": aq_num,
        "fan_speed": fan_raw,
        "fan_speed_num": fan_num,
        "mode_reported": mode_rep,
        "mode_reported_num": mode_rep_num,
        "mode_last": mode_last,
        "mode_last_num": M.mode_to_num(mode_last),
        "humidity_level": hum_lvl,
        "humidity_level_num": M.humidity_level_to_num(hum_lvl),
        "light_sensor_level": light_lvl,
        "light_sensor_level_num": M.light_sensor_to_num(light_lvl),
        "filter_status": fil_text,
        "filter_status_num": fil_num,
        "humidity_alarm": M.bool_to_int(_first(state, "humidity_alarm", "humidityAlarm")),
        "night_alarm": M.bool_to_int(_first(state, "night_alarm", "nightAlarm")),
        "night_mode": M.bool_to_int(night_mode),
        "online": M.bool_to_int(_first(state, "online")),
    }
    return record
