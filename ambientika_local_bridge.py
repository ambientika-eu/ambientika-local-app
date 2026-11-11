#!/usr/bin/env python3
"""
ambientika_local_bridge.py
==========================

Local bridge for Ambientika Smart / Office ventilation units — server-independent
operating mode of the Ambientika Local App.

The device firmware opens a persistent outbound TCP connection to the host:port it
was provisioned with (by default app.ambientika.eu:11000). This module implements
that server endpoint locally, so the units are monitored and controlled entirely on
the LAN — without the Ambientika server and without an internet connection.

Positioning: the recommended operating mode of the Local App remains the cloud
bridge. This local bridge is the FALLBACK layer — it keeps an installation operable
while the Ambientika server is unreachable (maintenance windows, network outages) and
serves buildings for which no permanent internet connection is intended.

It is interface-compatible with the cloud bridge: the same MQTT topics and the same
field vocabulary (mode names + fanSpeed 0-100 %), so the FastAPI backend and the PWA
run unchanged.

Feature scope, fully server-free:
  • device monitoring + control                    (status <-> commands)
  • weekly schedule execution                      (schedule/* topics)
  • NeuraCell-X: radon protection + dew-point       (radon/dewpoint topics)
    control, with priority and exact restore.

    Device (WiFi, TCP:11000) <-> [THIS bridge] <-> MQTT <-> local-app / HA

--------------------------------------------------------------------------------
IMPLEMENTATION NOTE
--------------------------------------------------------------------------------
Independent in-house implementation, written against the documented binary protocol
specification (see CLOUD-INTEGRATION.md). No third-party source is incorporated.

--------------------------------------------------------------------------------
COMMISSIONING NOTES (read before deployment)
--------------------------------------------------------------------------------
This module drives ventilation equipment and, via NeuraCell-X, radon and moisture
behaviour. Please observe the following when commissioning:
1. Field validation across the deployed firmware base is ongoing; the mode is shipped
   as a controlled release. Verify status decoding on the actual unit at handover,
   in particular the signed decoding of temperature and RSSI (see _s8).
2. Units reach this server only once they are directed to it (BLE H_<host>:11000, or
   a static route / DNAT for 185.214.203.87). See CLOUD-INTEGRATION.md.
3. Control thresholds and mappings are preset with application-safe defaults and are
   intended to be adapted per project:
     - operating-mode / fan mappings
     - radon threshold, dew-point margin, protection targets
   Site-specific limits — in particular officially mandated radon thresholds — are to
   be set at commissioning.
4. Dew-point control needs OUTDOOR temperature+humidity, which the device packet
   does not contain. Publish it locally to `ambientika/weather`
   ({"temperature": t, "humidity": rh}); without it, auto dew-point is inactive
   and only the manual `ambientika/dewpoint/block` override works.
5. Commission every unit with the official Ambientika app first (roles, zones,
   house). The bridge does NOT write a setup frame by default; it only does so
   for serials listed in SETUP_DEVICES, and never changes the master/slave role
   a unit reports unless SETUP_ALLOW_ROLE_CHANGE=true.
6. Only confirmed wire codes are ever sent. A command with an explicit mode / fan
   / humidity / light number outside the protocol tables is refused and logged;
   unknown names keep the unit's current value; echoed codes outside the tables
   are replaced by confirmed defaults (humidity NORMAL, light OFF).
7. No outdoor-weather data is sent to the units in this mode (in cloud mode the
   server pushes it for the SMART mode). Its format is only community-documented,
   not confirmed by the manufacturer, and a wrong packet would make the unit regulate on invented
   values. Unknown frame types from a unit are logged, never answered.

Requires: Python 3.10+, paho-mqtt>=1.6  (pip install paho-mqtt)  — works with
both the 1.x and the 2.x callback API.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import paho.mqtt.client as mqtt

log = logging.getLogger("ambientika.local")


# ---------------------------------------------------------------------------
# Wire-protocol enums  (source: protocol specification)
# ---------------------------------------------------------------------------
OPERATING_MODE = {
    0: "SMART", 1: "AUTO", 2: "MANUAL_HEAT_RECOVERY", 3: "NIGHT",
    4: "AWAY_HOME", 5: "SURVEILLANCE", 6: "TIMED_EXPULSION", 7: "EXPULSION",
    8: "INTAKE", 9: "MASTER_SLAVE_FLOW", 10: "SLAVE_MASTER_FLOW", 11: "OFF",
}
FAN_SPEED = {0: "LOW", 1: "MEDIUM", 2: "HIGH", 3: "NIGHT"}
HUMIDITY_LEVEL = {0: "DRY", 1: "NORMAL", 2: "MOIST"}
DEVICE_ROLE = {0: "MASTER", 1: "SLAVE_EQUAL_MASTER", 2: "SLAVE_OPPOSITE_MASTER"}
AIR_QUALITY = {0: "VERY_GOOD", 1: "GOOD", 2: "MEDIUM", 3: "POOR", 4: "BAD"}  # raw-1
FILTER_STATUS = {0: "GOOD", 1: "MEDIUM", 2: "BAD"}
LIGHT_SENS = {0: "NOT_AVAILABLE", 1: "OFF", 2: "LOW", 3: "MEDIUM"}

MODE_INTAKE = 8
MODE_OFF = 11
FAN_LOW = 0
#: A restore is confirmed by the unit's next status; it is re-sent at most this
#: many times in total, never sooner than RESTORE_RETRY_MIN seconds apart.
RESTORE_MAX_ATTEMPTS = 3
RESTORE_RETRY_MIN = 20.0

# ---------------------------------------------------------------------------
# Frame types and lengths
# ---------------------------------------------------------------------------
FRAME_STATUS = 0x01
FRAME_FIRMWARE = 0x03
FRAME_PING = 0x04             # 04 00 <MAC>: masters send it every few seconds, no state
PING_LEN = 8
KNOWN_DEVICE_FRAMES = (FRAME_STATUS, FRAME_FIRMWARE, FRAME_PING)
STATUS_LEN_MODERN = 21        # ... + lightSensor + rssi
STATUS_LEN_LEGACY = 19        # radio firmware 0.0.11 and older
FIRMWARE_LEN = 18
#: Radio firmware at or above this version sends the 21-byte status frame.
#: 0.0.11 is confirmed legacy, 0.0.28 confirmed modern; versions in between
#: are decided from the byte stream (see probe_status_len).
FW_MODERN_MIN = (0, 0, 28)
FW_LEGACY_MAX = (0, 0, 11)
OFF_DEVICE_ROLE = 17
OFF_LIGHT_SENSOR = 19
OFF_RSSI = 20
#: Light-sensor code echoed for legacy units, which have no such sensor.
LEGACY_LIGHT_CODE = 1         # "OFF" — same default the bridge always used

#: Codes that may be written to a unit. Anything else is refused.
VALID_MODE_CODES = frozenset(OPERATING_MODE)
VALID_SPEED_CODES = frozenset(FAN_SPEED)
VALID_HUMIDITY_CODES = frozenset(HUMIDITY_LEVEL)
VALID_LIGHT_CODES = frozenset(LIGHT_SENS)
VALID_ROLE_CODES = frozenset(DEVICE_ROLE)

_REV_MODE = {v: k for k, v in OPERATING_MODE.items()}
_REV_SPEED = {v: k for k, v in FAN_SPEED.items()}
_REV_HUM = {v: k for k, v in HUMIDITY_LEVEL.items()}
_REV_LIGHT = {v: k for k, v in LIGHT_SENS.items()}

# ---------------------------------------------------------------------------
# App-vocabulary mapping  (local-app: HRV|NIGHT|BOOST|ECO|SMART|OFF, fan 0-100)
# ---------------------------------------------------------------------------
# Mapping: app mode name <-> wire operating-mode code (adapt per project)
APP_MODE_TO_PROTO = {
    "SMART": 0, "ECO": 1, "HRV": 2, "NIGHT": 3, "BOOST": 6, "OFF": 11,
}
PROTO_TO_APP_MODE = {v: k for k, v in APP_MODE_TO_PROTO.items()}

# Mapping: fan level <-> percentage used by the PWA slider (adapt per project)
# NOTE: NIGHT (level 3) is a MODE-LINKED speed, not a slider position, so it is
# intentionally one-way: it is *reported* as a distinct low % (and the "fanLevel"
# field always carries the exact wire level "NIGHT"), but a user %-command only
# ever selects LOW/MEDIUM/HIGH — NIGHT speed is entered via NIGHT mode.
LEVEL_TO_PCT = {0: 40, 1: 70, 2: 100, 3: 15}  # LOW, MEDIUM, HIGH, NIGHT


def _s8(b: int) -> int:
    """Interpret an unsigned byte as a signed 8-bit two's-complement value.
    Temperature (°C, can be negative) and RSSI (negative dBm) are signed."""
    return b - 256 if b >= 128 else b


def pct_to_level(pct) -> int:
    p = int(float(pct))
    if p <= 40:
        return 0
    if p <= 75:
        return 1
    return 2


def app_mode_name(code: int) -> str:
    return PROTO_TO_APP_MODE.get(code, OPERATING_MODE.get(code, f"UNKNOWN_{code}"))


def mode_to_code(value, default: Optional[int]) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    s = str(value).strip().upper()
    if s in APP_MODE_TO_PROTO:
        return APP_MODE_TO_PROTO[s]
    if s in _REV_MODE:
        return _REV_MODE[s]
    if s.isdecimal() and s.isascii():
        return int(s)
    log.warning("unknown mode %r -> default %s", value, default)
    return default


def speed_to_code(value, default: Optional[int]) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return pct_to_level(value)
    s = str(value).strip().upper()
    if s in _REV_SPEED:
        return _REV_SPEED[s]
    if s.replace(".", "", 1).isdecimal() and s.isascii():
        return pct_to_level(float(s))
    log.warning("unknown fanSpeed %r -> default %s", value, default)
    return default


def _generic_to_code(value, table_rev: dict, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    s = str(value).strip().upper()
    if s in table_rev:
        return table_rev[s]
    if s.isdecimal() and s.isascii():
        return int(s)
    return default


def dew_point(temp_c, rh_pct) -> Optional[float]:
    """Dew point in °C from temperature (°C) and relative humidity (%),
    Magnus-Tetens (Sonntag) coefficients. None on invalid input."""
    try:
        t = float(temp_c)
        rh = float(rh_pct)
    except (TypeError, ValueError):
        return None
    if rh <= 0 or rh > 100:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + (a * t) / (b + t)
    return round((b * gamma) / (a - gamma), 2)


# ---------------------------------------------------------------------------
# Packet codec  (byte layout verified against the protocol specification)
# ---------------------------------------------------------------------------
def parse_serial(buf: bytes) -> str:
    return buf[2:8].hex().upper()


def serial_to_mac(serial: str) -> bytes:
    return bytes.fromhex(serial)


def parse_fw_tuple(text: Optional[str]) -> Optional[tuple]:
    """'0.0.11' -> (0, 0, 11). None for anything unparseable."""
    if not text:
        return None
    try:
        return tuple(int(p) for p in str(text).split("."))
    except (TypeError, ValueError):
        return None


def status_len_for_firmware(radio_fw: Optional[str]) -> Optional[int]:
    """Status length for a radio firmware string, or None if not confirmed."""
    ver = parse_fw_tuple(radio_fw)
    if ver is None:
        return None
    if ver >= FW_MODERN_MIN:
        return STATUS_LEN_MODERN
    if ver <= FW_LEGACY_MAX:
        return STATUS_LEN_LEGACY
    return None


#: Bytes needed to prove the legacy layout: 19-byte frame + next frame's
#: type, 0x00 and the same 6-byte MAC.
_LEGACY_PROOF_LEN = STATUS_LEN_LEGACY + 8


def _looks_like_frame_start_at(buf: bytes, i: int) -> bool:
    return len(buf) > i + 1 and buf[i] in KNOWN_DEVICE_FRAMES and buf[i + 1] == 0x00


def probe_status_len(buf: bytes) -> Optional[int]:
    """Decide the status length from the bytes; None = need more bytes.

    The 19-byte layout is accepted only on hard evidence: a new frame of the
    SAME unit (frame type, 0x00, identical MAC) visibly starts at offset 19.
    A modern frame would need light code 1/3, RSSI 0 AND the next frame's bytes
    to repeat the MAC at the wrong offset — that cannot happen by accident.
    In every other case the frame is read as 21 bytes, exactly as before.
    """
    if len(buf) < STATUS_LEN_MODERN:
        return None
    if _looks_like_frame_start_at(buf, STATUS_LEN_LEGACY):
        if len(buf) < _LEGACY_PROOF_LEN:
            return None                               # wait for the MAC
        if buf[STATUS_LEN_LEGACY + 2:_LEGACY_PROOF_LEN] == buf[2:8]:
            return STATUS_LEN_LEGACY
    return STATUS_LEN_MODERN


def resolve_status_len(buf: bytes, radio_fw: Optional[str] = None,
                       known_len: Optional[int] = None) -> Optional[int]:
    """How many bytes the pending status frame occupies; None = wait for more.

    Order of authority: the unit's reported radio firmware, then a length
    already established on this connection, then the stream probe. Without
    proof of the legacy layout the previous fixed 21-byte reading is kept, so
    installations that work today keep working byte for byte.
    """
    n = status_len_for_firmware(radio_fw) or known_len
    if n in (STATUS_LEN_LEGACY, STATUS_LEN_MODERN):
        return n if len(buf) >= n else None
    return probe_status_len(buf)


def codes_valid(mode: int, speed: int, humidity: int, light: int) -> bool:
    """True only if every code of a mode command is a confirmed wire value."""
    return (mode in VALID_MODE_CODES and speed in VALID_SPEED_CODES
            and humidity in VALID_HUMIDITY_CODES and light in VALID_LIGHT_CODES)


def decode_status(buf: bytes) -> dict:
    """19- or 21-byte device status -> app-compatible dict (+ raw fields).

    Raises ValueError on any other length so a broken frame surfaces as an
    error instead of silently producing wrong readings."""
    n = len(buf)
    if n not in (STATUS_LEN_LEGACY, STATUS_LEN_MODERN):
        raise ValueError(f"unsupported status frame length: {n}")
    mode_code = buf[8]
    speed_code = buf[9]
    temp = _s8(buf[11])                      # signed: outdoor/intake can be < 0 °C
    aq_raw = buf[13]
    if aq_raw <= 0:                          # 0 = sensor not ready / no data
        aq_class = 0
        aq_label = "UNKNOWN_SENSOR"
    else:
        aq_class = min(aq_raw - 1, 4)        # clamp to the 0..4 label range
        aq_label = AIR_QUALITY.get(aq_class, f"UNKNOWN_{aq_raw}")
    dp = dew_point(temp, buf[12])
    out = {
        "serial": parse_serial(buf),
        "name": parse_serial(buf),
        "mode": app_mode_name(mode_code),
        "fanSpeed": LEVEL_TO_PCT.get(speed_code, 0),
        "temperature": temp,
        "humidity": buf[12],
        "airQuality": aq_class,
        "filterAlarm": buf[15] == 2,
        "dewPoint": dp,
        "online": True,
        "airQualityLabel": aq_label,
        "humidityLevel": HUMIDITY_LEVEL.get(buf[10], f"UNKNOWN_{buf[10]}"),
        "humidityAlarm": bool(buf[14]),
        "filterStatus": FILTER_STATUS.get(buf[15], f"UNKNOWN_{buf[15]}"),
        "nightAlarm": bool(buf[16]),
        "deviceRole": DEVICE_ROLE.get(buf[17], f"UNKNOWN_{buf[17]}"),
        "lastMode": app_mode_name(buf[18]),
        "modeProtocol": OPERATING_MODE.get(mode_code, f"UNKNOWN_{mode_code}"),
        "fanLevel": FAN_SPEED.get(speed_code, f"UNKNOWN_{speed_code}"),
    }
    if n >= STATUS_LEN_MODERN:
        out["lightSensor"] = LIGHT_SENS.get(buf[19], f"UNKNOWN_{buf[19]}")
        out["rssi"] = _s8(buf[20])           # signed dBm
    else:
        # Legacy firmware has neither field: report them as absent instead of
        # reading bytes that belong to the next frame.
        out["lightSensor"] = "NOT_AVAILABLE"
        out["rssi"] = None
    return out


def status_raw_codes(buf: bytes) -> dict:
    light = buf[19] if len(buf) >= STATUS_LEN_MODERN else LEGACY_LIGHT_CODE
    return {"mode": buf[8], "speed": buf[9], "humidity": buf[10], "light": light}


def status_role_code(buf: bytes) -> int:
    """The master/slave role the unit reports about itself."""
    return buf[OFF_DEVICE_ROLE]


def decode_firmware(buf: bytes) -> dict:
    return {
        "serial": parse_serial(buf),
        "radioFw": f"{buf[8]}.{buf[9]}.{buf[10]}",
        "microFw": f"{buf[11]}.{buf[12]}.{buf[13]}",
        "radioAtFw": f"{buf[14]}.{buf[15]}.{buf[16]}.{buf[17]}",
    }


def encode_mode_command(serial: str, mode_code: int, speed_code: int,
                        humidity_code: int, light_code: int) -> bytes:
    """13-byte operating-mode command: 02 00 <MAC> 01 <mode><speed><hum><light>."""
    return bytes(
        [0x02, 0x00]
        + list(serial_to_mac(serial))
        + [0x01, mode_code & 0xFF, speed_code & 0xFF,
           humidity_code & 0xFF, light_code & 0xFF]
    )


def encode_filter_reset(serial: str) -> bytes:
    return bytes([0x02, 0x00] + list(serial_to_mac(serial)) + [0x03])


def encode_setup(serial: str, role: int, zone: int, house_id: int) -> bytes:
    """15-byte setup in the cloud's own format: 02 00 <MAC> 00 <role> <zone> <house LE32>.
    Units read the house ID from offset 11, as in the server's own packet."""
    # Clamp house_id into the unsigned 32-bit range so a mis-set HOUSE_ID can
    # never raise OverflowError (which would loop the device on every connect).
    hid = max(0, min(int(house_id), 0xFFFFFFFF))
    return bytes(
        [0x02, 0x00]
        + list(serial_to_mac(serial))
        + [0x00, role & 0xFF, zone & 0xFF]
        + list(hid.to_bytes(4, "little"))
    )


# ---------------------------------------------------------------------------
# Setup policy — when (if ever) a setup frame is written to a unit
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SetupTarget:
    role: int
    zone: int
    house: int


def parse_setup_devices(raw) -> dict:
    """{"AABBCCDDEEFF": {"role": 0, "zone": 2, "house": 1}} -> {SERIAL: SetupTarget}.

    Unparseable entries or out-of-range roles are skipped (logged), so a typo
    leaves that unit untouched — the safe direction."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            log.error("SETUP_DEVICES is not valid JSON — no setup will be sent")
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for serial, spec in raw.items():
        key = str(serial).strip().upper()
        if len(key) != 12 or any(c not in "0123456789ABCDEF" for c in key):
            log.error("SETUP_DEVICES key %r ignored (serial must be 12 hex chars)", serial)
            continue
        if not isinstance(spec, dict) or not all(k in spec for k in ("role", "zone", "house")):
            log.error("SETUP_DEVICES entry %s ignored (role, zone and house are mandatory)", key)
            continue
        if key in out:
            log.error("SETUP_DEVICES serial %s listed twice — entry ignored", key)
            continue
        vals = (spec["role"], spec["zone"], spec["house"])
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
            log.error("SETUP_DEVICES entry %s ignored (values must be integers)", key)
            continue
        t = SetupTarget(*vals)
        if not setup_target_valid(t):
            log.error("SETUP_DEVICES entry %s ignored (role/zone/house out of range)", key)
            continue
        out[key] = t
    return out


def setup_target_valid(t: SetupTarget) -> bool:
    # zone travels as a 4-bit field in the master->slave UDP sync
    return (t.role in VALID_ROLE_CODES and 0 <= t.zone <= 15
            and 0 <= t.house <= 0xFFFFFFFF)


def setup_decision(serial: str, cfg: "Config", reported_role: Optional[int]):
    """Return (SetupTarget, reason) if a setup frame may be sent, else (None, reason).

    * Per-serial targets from SETUP_DEVICES always take precedence.
    * The old global target (DEVICE_ROLE/DEVICE_ZONE/HOUSE_ID) is used only when
      SEND_SETUP=true is set explicitly — for single-unit setups ONLY: it writes
      the same zone/house to every unit whose reported role matches.
    * In both cases a setup that would change the role the unit itself reports
      is refused unless SETUP_ALLOW_ROLE_CHANGE=true: silently turning a slave
      into a master is how an installation loses its cross ventilation.
    """
    serial = (serial or "").upper()
    target = cfg.setup_devices.get(serial)
    if target is None:
        if not cfg.send_setup:
            return None, "setup disabled (not listed in SETUP_DEVICES)"
        target = SetupTarget(cfg.setup_role, cfg.setup_zone, cfg.setup_house)
        if not setup_target_valid(target):
            return None, (f"DEVICE_ROLE/DEVICE_ZONE/HOUSE_ID {target.role}/{target.zone}/"
                          f"{target.house} out of range")
    if reported_role is not None and reported_role not in VALID_ROLE_CODES:
        return None, (f"unit reports undocumented role {reported_role} — setup not sent "
                      "(newer firmware reports 255 for a standalone master)")
    if (reported_role is not None and reported_role != target.role
            and not cfg.setup_allow_role_change):
        return None, (f"unit reports role {reported_role}, target is {target.role}; "
                      "refusing to change it without SETUP_ALLOW_ROLE_CHANGE=true")
    return target, "ok"


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _pad_hhmm(s) -> str:
    """Normalise a time string to zero-padded HH:MM so lexicographic compares
    are correct ('9:00' -> '09:00'). Leaves anything unrecognised untouched."""
    s = str(s or "").strip()
    if ":" in s:
        h, _, mn = s.partition(":")
        if h.isascii() and h.isdecimal() and mn.isascii() and mn.isdecimal():
            return f"{int(h):02d}:{int(mn):02d}"
    return s


def normalize_week(payload) -> dict:
    """Accept the app's WeekSchedule dict {mon:[...],...} and return a clean
    {weekday: [slot,...]} with only known weekdays and zero-padded times."""
    out = {}
    if isinstance(payload, dict):
        for day in WEEKDAYS:
            slots = payload.get(day) or []
            if isinstance(slots, list):
                clean = []
                for s in slots:
                    if isinstance(s, dict) and s.get("start") and s.get("end"):
                        s2 = dict(s)
                        s2["start"] = _pad_hhmm(s.get("start"))
                        s2["end"] = _pad_hhmm(s.get("end"))
                        clean.append(s2)
                out[day] = clean
    return out


def active_slot(day_slots: list, hhmm: str) -> Optional[dict]:
    """Return the slot covering hhmm ('HH:MM'), or None. Zero-padded string
    compare is correct for HH:MM. Handles start<=t<end (no overnight wrap)."""
    for s in day_slots or []:
        start, end = s.get("start", ""), s.get("end", "")
        if start <= hhmm < end:
            return s
    return None


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
@dataclass
class Device:
    serial: str
    writer: asyncio.StreamWriter
    last_status: dict = field(default_factory=dict)
    raw_codes: dict = field(default_factory=lambda: {"mode": 0, "speed": 0,
                                                     "humidity": 1, "light": 1})
    firmware: dict = field(default_factory=dict)
    setup_sent: bool = False
    status_seq: int = 0                  # bridge-wide sequence number of the last status
    # per-device write serialisation so two coroutines never overlap drain()
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# ---------------------------------------------------------------------------
# Home Assistant: add-on profile  (HA_DISCOVERY_PROFILE=addon)
# ---------------------------------------------------------------------------
# With this profile the local bridge offers the units to Home Assistant the same
# way the Ambientika MQTT Bridge add-on does: same discovery config topics and
# unique_ids, same state topic and JSON keys, same value names (operating mode
# Smart / AwayHome / ManualHeatRecovery / Intake ..., fan speed Low / Medium /
# High) and the same per-attribute command topics. Home Assistant binds entity
# IDs to the unique_id, so automations, scripts and dashboards made with the
# add-on keep working when an installation moves to local operation.
# One broker carries either the add-on or this bridge, never both at once.
ADDON_MODE_NAMES = {
    0: "Smart", 1: "Auto", 2: "ManualHeatRecovery", 3: "Night", 4: "AwayHome",
    5: "Surveillance", 6: "TimedExpulsion", 7: "Expulsion", 8: "Intake",
    9: "MasterSlaveFlow", 10: "SlaveMasterFlow", 11: "Off",
}
ADDON_FAN_NAMES = {0: "Low", 1: "Medium", 2: "High", 3: "Night"}
#: Select options exactly as the add-on lists them (Night/Turbo are reported by
#: the unit itself and shown, but written as the nearest regular level).
ADDON_FAN_OPTIONS = ("Low", "Medium", "High", "Night", "Turbo")
ADDON_FAN_WRITE = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "NIGHT": 0, "TURBO": 2}
ADDON_HUMIDITY_NAMES = {0: "Dry", 1: "Normal", 2: "Moist"}
ADDON_LIGHT_NAMES = {0: "NotAvailable", 1: "Off", 2: "Low", 3: "Medium"}
ADDON_AIR_QUALITY = {0: "VeryGood", 1: "Good", 2: "Medium", 3: "Poor", 4: "Bad"}
ADDON_FILTER_NAMES = {0: "Good", 1: "Medium", 2: "Bad"}
ADDON_ROLE_NAMES = {0: "Master", 1: "SlaveEqualMaster", 2: "SlaveOppositeMaster"}
_REV_ADDON_MODE = {v.upper(): k for k, v in ADDON_MODE_NAMES.items()}
_REV_ADDON_HUM = {v.upper(): k for k, v in ADDON_HUMIDITY_NAMES.items()}
_REV_ADDON_LIGHT = {v.upper(): k for k, v in ADDON_LIGHT_NAMES.items()}
#: Commands for one unit arriving within this time are sent as one frame (an
#: automation setting mode and fan speed one after the other).
ADDON_COALESCE_S = 0.8
#: A sent state is the base for the next command until the unit reports it, at
#: most this long - so a second command cannot undo the first one.
ADDON_OVERLAY_S = 30.0
#: "Last Status Packet" moves at most this often (one recorder row each time);
#: the start and the end of an outage are always published at once.
ADDON_DIAG_MIN_S = 30.0
#: After (re)publishing the NeuraCell-X entities, the local NeuraCell state topic
#: waits this long, so Home Assistant has moved the entities to their own topic
#: before the local format appears where the add-on's entities used to listen.
ADDON_NC_HOLD_S = 3.0


def _addon_name(table: dict, code) -> str:
    return table.get(code, f"Unknown_{code}")


def addon_fields_from_frame(buf: bytes) -> dict:
    """Fields of one unit in the add-on vocabulary, decoded from the status frame.

    Keys and value names follow the add-on's state topic; the averaged values and
    the zone are added by the bridge."""
    n = len(buf)
    if n not in (STATUS_LEN_LEGACY, STATUS_LEN_MODERN):
        raise ValueError(f"unsupported status frame length: {n}")
    mode, speed, hum_lvl = buf[8], buf[9], buf[10]
    aq_raw, fs, last = buf[13], buf[15], buf[18]
    aq_class = None if aq_raw <= 0 else min(aq_raw - 1, 4)
    light = buf[OFF_LIGHT_SENSOR] if n >= STATUS_LEN_MODERN else 0   # legacy: no sensor
    mode_name = _addon_name(ADDON_MODE_NAMES, mode)
    fs_name = _addon_name(ADDON_FILTER_NAMES, fs)
    fs_num = fs if fs in ADDON_FILTER_NAMES else None
    return {
        "operating_mode": mode_name,
        "operating_mode_raw": mode_name,
        "fan_speed": _addon_name(ADDON_FAN_NAMES, speed),
        "humidity_level": _addon_name(ADDON_HUMIDITY_NAMES, hum_lvl),
        "light_sensor_level": _addon_name(ADDON_LIGHT_NAMES, light),
        "temperature": _s8(buf[11]),
        "humidity": buf[12],
        "air_quality": None if aq_class is None else ADDON_AIR_QUALITY[aq_class],
        "humidity_alarm": bool(buf[14]),
        "filters_status": fs_name,
        "filters_status_raw": fs_name,
        "filter_ack_since": None,
        "filter_ack_until": None,
        "night_alarm": bool(buf[16]),
        "device_role": _addon_name(ADDON_ROLE_NAMES, buf[OFF_DEVICE_ROLE]),
        "last_operating_mode": _addon_name(ADDON_MODE_NAMES, last),
        "operating_mode_num": mode if mode in ADDON_MODE_NAMES else None,
        "operating_mode_raw_num": mode if mode in ADDON_MODE_NAMES else None,
        "last_operating_mode_num": last if last in ADDON_MODE_NAMES else None,
        "fan_speed_num": speed + 1 if speed in ADDON_FAN_NAMES else None,
        "humidity_level_num": hum_lvl if hum_lvl in ADDON_HUMIDITY_NAMES else None,
        "light_sensor_level_num": light if light in ADDON_LIGHT_NAMES else None,
        "air_quality_num": None if aq_class is None else 4 - aq_class,
        "filter_status_num": fs_num,
        "filter_status_raw_num": fs_num,
    }


def parse_addon_command(attr: str, raw: str) -> Optional[dict]:
    """Payload of an add-on command topic (`<prefix>/<serial>/set/<attr>`) as a
    bridge command, or None if it cannot be read. Wire names are used for the fan
    speed (a bare number would be read as a percentage)."""
    v = (raw or "").strip().strip('"').strip()
    if not v:
        return None
    key = v.upper()
    if attr == "reset_filter":
        return {"resetFilter": True}
    if attr == "operating_mode":
        code = _REV_ADDON_MODE.get(key, _REV_MODE.get(key))
        if code is None and v.isdecimal() and v.isascii():
            code = int(v)
        return {"mode": code} if code in VALID_MODE_CODES else None
    if attr == "fan_speed":
        level = ADDON_FAN_WRITE.get(key)
        return None if level is None else {"fanSpeed": FAN_SPEED[level]}
    if attr == "humidity_level":
        code = _REV_ADDON_HUM.get(key, _REV_HUM.get(key))
        return None if code is None else {"humidityLevel": HUMIDITY_LEVEL[code]}
    if attr == "light_sensor_level":
        code = _REV_ADDON_LIGHT.get(key, _REV_LIGHT.get(key))
        return None if code is None else {"lightSensor": LIGHT_SENS[code]}
    return None


def addon_discovery_configs(prefix: str, base: str, serial: str, device_name: str) -> list:
    """Discovery configs of one unit with the add-on's topics and unique_ids."""
    device_info = {
        "identifiers": [serial],
        "name": device_name,
        "manufacturer": "Ambientika / SUEDWIND",
        "model": "Smart Ventilation Unit",
    }
    avail = f"{prefix}/{serial}/availability"
    state = f"{prefix}/{serial}/state"
    entities = []
    sensor_defs = [
        ("temperature", "Temperature", "°C", "temperature", None),
        ("humidity", "Humidity", "%", "humidity", None),
        ("temperature_avg", "Temperature (averaged)", "°C", "temperature", None),
        ("humidity_avg", "Humidity (averaged)", "%", "humidity", None),
        ("air_quality", "Air Quality", None, None, "mdi:air-filter"),
        ("filters_status", "Filter Status", None, None, "mdi:air-filter"),
        ("filters_status_raw", "Filter Status raw", None, None, "mdi:air-filter"),
        ("operating_mode", "Mode", None, None, "mdi:fan"),
        ("operating_mode_raw", "Mode raw", None, None, "mdi:fan"),
        ("fan_speed", "Fan Speed", None, None, "mdi:speedometer"),
        ("humidity_level", "Humidity Level", None, None, "mdi:water-percent"),
        ("light_sensor_level", "Light Sensor Level", None, None, "mdi:brightness-5"),
        ("device_role", "Device Role", None, None, "mdi:information"),
        ("last_operating_mode", "Last Mode", None, None, "mdi:fan-clock"),
        ("zone_index", "Zone", None, None, "mdi:home-group"),
    ]
    measurement = {"temperature", "humidity", "temperature_avg", "humidity_avg"}
    for key, name, unit, dc, icon in sensor_defs:
        p = {"name": name, "unique_id": f"ambientika_{serial}_{key}",
             "state_topic": state, "value_template": f"{{{{ value_json.{key} }}}}",
             "availability_topic": avail, "device": device_info}
        if key in measurement:
            p["state_class"] = "measurement"
        if unit:
            p["unit_of_measurement"] = unit
        if dc:
            p["device_class"] = dc
        if icon:
            p["icon"] = icon
        entities.append((f"{base}/sensor/{serial}_{key}/config", p))
    for key, name in (
            ("air_quality_num", "Air Quality (num)"),
            ("filter_status_num", "Filter Status (num)"),
            ("filter_status_raw_num", "Filter Status raw (num)"),
            ("operating_mode_num", "Mode (num)"),
            ("operating_mode_raw_num", "Mode raw (num)"),
            ("last_operating_mode_num", "Last Mode (num)"),
            ("fan_speed_num", "Fan Speed (num)"),
            ("humidity_level_num", "Humidity Level (num)"),
            ("light_sensor_level_num", "Light Sensor Level (num)")):
        entities.append((f"{base}/sensor/{serial}_{key}/config", {
            "name": name, "unique_id": f"ambientika_{serial}_{key}",
            "state_topic": state, "value_template": f"{{{{ value_json.{key} }}}}",
            "availability_topic": avail, "device": device_info,
            "state_class": "measurement"}))
    for key, name, dc in (("humidity_alarm", "Humidity Alarm", "moisture"),
                          ("night_alarm", "Night Alarm", "problem")):
        entities.append((f"{base}/binary_sensor/{serial}_{key}/config", {
            "name": name, "unique_id": f"ambientika_{serial}_{key}",
            "state_topic": state, "value_template": f"{{{{ value_json.{key} }}}}",
            "payload_on": "True", "payload_off": "False",
            "availability_topic": avail, "device": device_info, "device_class": dc}))
    for key, name, opts, icon in (
            ("operating_mode", "Mode", list(ADDON_MODE_NAMES.values()), "mdi:fan"),
            ("fan_speed", "Fan Speed", list(ADDON_FAN_OPTIONS), "mdi:speedometer"),
            ("humidity_level", "Humidity Level", list(ADDON_HUMIDITY_NAMES.values()),
             "mdi:water-percent"),
            ("light_sensor_level", "Light Sensor Level", list(ADDON_LIGHT_NAMES.values()),
             "mdi:brightness-5")):
        entities.append((f"{base}/select/{serial}_{key}/config", {
            "name": name, "unique_id": f"ambientika_{serial}_{key}_select",
            "state_topic": state, "value_template": f"{{{{ value_json.{key} }}}}",
            "command_topic": f"{prefix}/{serial}/set/{key}", "options": opts,
            "availability_topic": avail, "device": device_info, "icon": icon}))
    entities.append((f"{base}/button/{serial}_reset_filter/config", {
        "name": "Filter zuruecksetzen", "unique_id": f"ambientika_{serial}_reset_filter",
        "command_topic": f"{prefix}/{serial}/set/reset_filter", "payload_press": "PRESS",
        "availability_topic": avail, "device": device_info, "icon": "mdi:air-filter"}))
    diag = f"{prefix}/{serial}/diagnostics"
    bridge_avail = f"{prefix}/bridge/availability"
    entities.append((f"{base}/sensor/{serial}_last_status/config", {
        "name": "Last Status Packet", "unique_id": f"ambientika_{serial}_last_status",
        "state_topic": diag,
        "value_template": "{{ value_json.last_status | default('None', true) }}",
        "availability_topic": bridge_avail, "device": device_info,
        "device_class": "timestamp", "icon": "mdi:clock-check-outline",
        "entity_category": "diagnostic"}))
    entities.append((f"{base}/sensor/{serial}_poll_failures/config", {
        "name": "Failed Polls", "unique_id": f"ambientika_{serial}_poll_failures",
        "state_topic": diag, "value_template": "{{ value_json.poll_failures_total }}",
        "json_attributes_topic": diag,
        "json_attributes_template": (
            "{{ {'consecutive_failures': value_json.poll_failures_consecutive,"
            " 'last_failure': value_json.last_failure,"
            " 'last_failure_reason': value_json.last_failure_reason,"
            " 'last_outage_start': value_json.last_outage_start,"
            " 'last_outage_end': value_json.last_outage_end,"
            " 'last_outage_polls': value_json.last_outage_polls,"
            " 'counting_since': value_json.bridge_started} | tojson }}"),
        "availability_topic": bridge_avail, "device": device_info,
        "state_class": "total_increasing", "icon": "mdi:cloud-alert-outline",
        "entity_category": "diagnostic"}))
    return entities


def addon_neuracell_discovery(prefix: str, base: str) -> list:
    """NeuraCell-X entities with the add-on's unique_ids, fed from their own state
    topic (the local NeuraCell state topic keeps the local app's format)."""
    state = f"{prefix}/neuracell/addon_state"
    avail = f"{prefix}/bridge/availability"
    dev = {"identifiers": ["neuracell_x"], "name": "NeuraCell-X",
           "manufacturer": "Ambientika / SUEDWIND",
           "model": "NeuraCell-X AI Neural Control System"}
    rows = [
        ("binary_sensor", "neuracell_radon_protection", "Radon Protection Active",
         "neuracell_radon_protection_active", "radon_protection",
         {"device_class": "safety", "icon": "mdi:shield-home"}),
        ("sensor", "neuracell_radon_level", "Radon Level", "neuracell_radon_level",
         "radon", {"unit_of_measurement": "Bq/m³", "icon": "mdi:radioactive"}),
        ("binary_sensor", "neuracell_radon_meter_connected", "Radon Meter Connected",
         "neuracell_radon_meter_connected", "radon_meter_connected",
         {"device_class": "connectivity", "icon": "mdi:access-point-network"}),
        ("binary_sensor", "neuracell_dewpoint_controller_connected",
         "Dew Point Controller Connected", "neuracell_dewpoint_controller_connected",
         "dewpoint_controller_connected",
         {"device_class": "connectivity", "icon": "mdi:access-point-network"}),
        ("binary_sensor", "neuracell_dewpoint_block", "Ventilation Blocked (Dew Point)",
         "neuracell_dewpoint_block", "dewpoint_block",
         {"device_class": "moisture", "icon": "mdi:water-off"}),
        ("binary_sensor", "neuracell_dewpoint_fallback", "Fallback Ventilation Active",
         "neuracell_dewpoint_fallback", "dewpoint_fallback | default(false)",
         {"device_class": "problem", "icon": "mdi:fan-alert"}),
        ("sensor", "neuracell_dewpoint_indoor", "Dew Point Indoor",
         "neuracell_dewpoint_indoor", "indoor_dew_point",
         {"unit_of_measurement": "°C", "icon": "mdi:thermometer-water"}),
        ("sensor", "neuracell_dewpoint_outdoor", "Dew Point Outdoor",
         "neuracell_dewpoint_outdoor", "outdoor_dew_point",
         {"unit_of_measurement": "°C", "icon": "mdi:thermometer-water"}),
    ]
    out = []
    for comp, obj, name, uid, field_, extra in rows:
        p = {"name": name, "unique_id": uid, "state_topic": state,
             "value_template": f"{{{{ value_json.{field_} }}}}"}
        if comp == "binary_sensor":
            p.update({"payload_on": "True", "payload_off": "False"})
        p.update(extra)
        p.update({"device": dev, "availability_topic": avail})
        out.append((f"{base}/{comp}/{obj}/config", p))
    return out


def addon_bridge_discovery(prefix: str, base: str) -> list:
    return [(f"{base}/binary_sensor/{prefix}_bridge/config", {
        "name": "Ambientika Bridge", "unique_id": f"ambientika_{prefix}_bridge_online",
        "state_topic": f"{prefix}/bridge/availability",
        "payload_on": "online", "payload_off": "offline", "device_class": "connectivity",
        "device": {"identifiers": [f"{prefix}_bridge"], "name": "Ambientika MQTT Bridge",
                   "manufacturer": "Ambientika / SUEDWIND", "model": "MQTT Bridge"}})]


def parse_device_names(raw) -> dict:
    """ADDON_DEVICE_NAMES: JSON {"<SERIAL>": "<name>"}; anything unreadable -> {}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        log.error("ADDON_DEVICE_NAMES is not valid JSON - device names not used")
        return {}
    if not isinstance(data, dict):
        log.error("ADDON_DEVICE_NAMES must be a JSON object - device names not used")
        return {}
    return {str(k).strip().upper().replace(":", ""): str(v).strip()
            for k, v in data.items() if str(v).strip()}


def _envf(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


@dataclass
class Config:
    tcp_host: str = os.getenv("LOCAL_TCP_HOST", "0.0.0.0")
    tcp_port: int = int(os.getenv("LOCAL_TCP_PORT", "11000"))
    mqtt_host: str = os.getenv("MQTT_BROKER", "localhost")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_user: str = os.getenv("MQTT_USER", "")
    mqtt_pass: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_prefix: str = os.getenv("MQTT_PREFIX", "ambientika")
    ha_discovery: bool = os.getenv("HA_DISCOVERY", "false").lower() == "true"
    # "local" (default): compact discovery of this bridge. "addon": entities,
    # topics and values of the Home Assistant add-on (discovery switched on).
    ha_discovery_profile: str = os.getenv("HA_DISCOVERY_PROFILE", "local").strip().lower()
    discovery_prefix: str = os.getenv("DISCOVERY_PREFIX", "homeassistant")
    addon_device_names: dict = field(
        default_factory=lambda: parse_device_names(os.getenv("ADDON_DEVICE_NAMES", "")))
    average_window_min: float = _envf("AVERAGE_WINDOW_MIN", "30")
    setup_role: int = int(os.getenv("DEVICE_ROLE", "0"))
    setup_zone: int = int(os.getenv("DEVICE_ZONE", "0"))
    setup_house: int = int(os.getenv("HOUSE_ID", "1"))
    # Global setup (DEVICE_ROLE/ZONE/HOUSE_ID) only when explicitly enabled.
    send_setup: bool = os.getenv("SEND_SETUP", "false").lower() == "true"
    setup_devices: dict = field(
        default_factory=lambda: parse_setup_devices(os.getenv("SETUP_DEVICES", "")))
    setup_allow_role_change: bool = (
        os.getenv("SETUP_ALLOW_ROLE_CHANGE", "false").lower() == "true")
    # scheduler
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    scheduler_tick: int = int(os.getenv("SCHEDULER_TICK", "30"))
    # NeuraCell-X control parameters (adapt per project)
    neuracell_enabled: bool = os.getenv("NEURACELL_ENABLED", "true").lower() == "true"
    neuracell_tick: int = int(os.getenv("NEURACELL_TICK", "60"))
    radon_threshold: float = _envf("RADON_THRESHOLD", "100")    # Bq/m³
    dewpoint_enabled: bool = os.getenv("DEWPOINT_ENABLED", "true").lower() == "true"
    dewpoint_margin: float = _envf("DEWPOINT_MARGIN", "1.0")     # °C hysteresis
    radon_protect_mode: int = int(os.getenv("RADON_PROTECT_MODE", str(MODE_INTAKE)))
    radon_protect_fan: int = int(os.getenv("RADON_PROTECT_FAN", str(FAN_LOW)))
    # Radon/weather inputs older than this (seconds) are treated as unknown so a
    # dead sensor can neither silently disable nor latch protection forever.
    nc_input_ttl: float = _envf("NC_INPUT_TTL", "900")
    # A unit that is offline when a protection ends is restored when it comes
    # back, as long as it still shows the protective mode and not longer than this.
    pending_restore_ttl: float = _envf("PENDING_RESTORE_TTL", "86400")
    # Persist baselines / pending restores so a bridge restart during a
    # protection does not leave units OFF (empty = in memory only).
    state_file: str = os.getenv("STATE_FILE", "")


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------
class LocalBridge:
    def __init__(self, cfg: Config, now_fn: Callable[[], _dt.datetime] = _dt.datetime.now,
                 mono_fn: Callable[[], float] = time.monotonic,
                 wall_fn: Callable[[], float] = time.time):
        self.cfg = cfg
        self.mono_fn = mono_fn            # ages / retries: immune to clock jumps
        self.wall_fn = wall_fn            # only to age a saved state across a restart
        # Protective targets come from the environment: never let a typo put an
        # unconfirmed code on the wire during radon protection.
        if cfg.radon_protect_mode not in VALID_MODE_CODES:
            log.error("RADON_PROTECT_MODE %s invalid -> INTAKE", cfg.radon_protect_mode)
            cfg.radon_protect_mode = MODE_INTAKE
        if cfg.radon_protect_fan not in VALID_SPEED_CODES:
            log.error("RADON_PROTECT_FAN %s invalid -> LOW", cfg.radon_protect_fan)
            cfg.radon_protect_fan = FAN_LOW
        self.now_fn = now_fn
        self.devices: dict[str, Device] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.mqtt = self._make_mqtt_client()
        if cfg.mqtt_user:
            self.mqtt.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass)
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message
        # Single mutex serialising every decision that reads protection state and
        # then issues device writes (commands, schedule, NeuraCell). Prevents the
        # "protection committed after the writes" race and re-entrant double-apply.
        self._lock = asyncio.Lock()
        # schedule state
        self.schedules: dict[str, dict] = {}          # serial -> {weekday:[slots]}
        self._sched_applied: dict[str, Optional[tuple]] = {}  # serial -> last applied key
        # NeuraCell state
        self.protection = "NONE"                       # NONE | DEWPOINT | RADON
        self.baseline: dict[str, tuple] = {}           # serial -> (mode, speed)
        # last known *normal* (non-protection) target per device, used as the
        # restore baseline so a fast protection re-trip can't capture the
        # protective codes as "normal".
        self.normal_codes: dict[str, tuple] = {}
        self._dew_blocked = False                      # hysteresis latch
        self.nc_manual_radon = False
        self.nc_radon_value: Optional[float] = None
        self.nc_radon_ts: Optional[_dt.datetime] = None
        self.nc_manual_dewpoint = False
        self.weather: dict = {}                        # {temperature, humidity} outdoor
        self.weather_ts: Optional[_dt.datetime] = None
        self._last_nc_json: Optional[str] = None       # publish-gate for neuracell state
        # Restores waiting for the unit's confirmation:
        # serial -> {"target": (mode, speed), "since": mono, "seq": int,
        #            "attempts": int, "last_try": mono|None}
        self.pending_restore: dict[str, dict] = {}
        # Protective modes that may be active on a unit (every protective write
        # adds its mode; cleared once the unit reports a normal state again).
        self.prot_modes: dict[str, set] = {}
        self._status_seq = 0             # increases with every decoded status frame
        # After a restart during a protection: keep protecting until the input
        # that caused it reports again (or NC_INPUT_TTL passes). key -> mono deadline
        #   "radon_value"  cleared by ambientika/radon/value
        #   "radon_manual" cleared by ambientika/radon/alarm
        #   "dew_manual"   cleared by ambientika/dewpoint/block
        # (the automatic dew-point latch itself is saved and restored)
        self._holds: dict[str, float] = {}
        # Add-on profile (HA_DISCOVERY_PROFILE=addon)
        if cfg.ha_discovery_profile not in ("local", "addon"):
            log.error("HA_DISCOVERY_PROFILE %r unknown -> local", cfg.ha_discovery_profile)
            cfg.ha_discovery_profile = "local"
        self.addon = cfg.ha_discovery_profile == "addon"
        if self.addon:
            cfg.ha_discovery = True
        self._addon_pending: dict[str, dict] = {}       # serial -> merged command
        self._addon_flush: dict[str, asyncio.Task] = {}
        self._addon_sent: dict[str, dict] = {}          # serial -> {"codes", "at"}
        self._addon_last_speed: dict[str, int] = {}     # last regular level 0..2
        self._addon_last_light: dict[str, int] = {}     # last light level while not Off
        self._addon_samples: dict[str, list] = {}       # serial -> [(mono, temp, hum)]
        self._addon_diag: dict[str, dict] = {}
        self._addon_diag_at: dict[str, float] = {}     # serial -> mono of last publish
        self._addon_disc: set = set()                   # serials with discovery sent
        self._addon_nc_json: Optional[str] = None
        self._nc_hold_until = float("inf") if self.addon else 0.0
        self._nc_deferred: Optional[str] = None
        self._nc_deferred_due = False
        self._started_iso = self._iso_now()
        self._load_state()

    def _make_mqtt_client(self):
        """Build a paho client on the 2.x callback API when available, else 1.x.
        Keeps the code warning-free on paho-mqtt 2.x and functional on 1.x."""
        cid = "ambientika-local-bridge"
        cav = getattr(mqtt, "CallbackAPIVersion", None)
        if cav is not None:
            try:
                return mqtt.Client(callback_api_version=cav.VERSION2, client_id=cid)
            except Exception:  # noqa: BLE001
                pass
        return mqtt.Client(client_id=cid)

    # ---- MQTT plumbing ----------------------------------------------------
    def _topic(self, serial: str, leaf: str) -> str:
        return f"{self.cfg.mqtt_prefix}/{serial}/{leaf}"

    def _on_mqtt_connect(self, client, _userdata, _flags, reason_code=0,
                         _properties=None, *_args):
        log.info("MQTT connected rc=%s", reason_code)
        p = self.cfg.mqtt_prefix
        # After a reconnect the broker may have published the last will (offline)
        # or lost its retained data - announce the bridge again on every connect.
        client.publish(f"{p}/bridge/availability", "online", retain=True)
        client.subscribe(f"{p}/+/set")
        client.subscribe(f"{p}/+/schedule/set")
        client.subscribe(f"{p}/+/schedule/+/set")
        client.subscribe(f"{p}/radon/alarm")
        client.subscribe(f"{p}/radon/value")
        client.subscribe(f"{p}/dewpoint/block")
        client.subscribe(f"{p}/weather")
        if self.addon:
            client.subscribe(f"{p}/+/set/+")
            # bridge state is touched on the asyncio thread only
            if self.loop:
                self.loop.call_soon_threadsafe(self._publish_addon_static)
            else:
                self._publish_addon_static()

    def _on_mqtt_message(self, _client, _userdata, msg):
        # Runs on the paho network thread: do NOTHING here except marshal the
        # work onto the asyncio loop, so all shared state is touched from one
        # thread only. Any error is logged rather than killing the paho thread.
        try:
            parts = msg.topic.split("/")
            raw = msg.payload.decode() if msg.payload else ""
            if not self.loop:
                return

            def go(coro):
                self.loop.call_soon_threadsafe(asyncio.create_task, coro)

            # reserved control topics: {prefix}/radon|dewpoint|weather/...
            if len(parts) >= 2 and parts[1] in ("radon", "dewpoint", "weather"):
                go(self._handle_control(parts, raw))
                return
            # add-on command topic: {prefix}/<serial>/set/<attribute>
            if len(parts) == 4 and parts[2] == "set":
                if self.addon:
                    go(self._handle_addon_command(parts[1], parts[3], raw))
                return
            try:
                payload = json.loads(raw or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            if len(parts) == 3 and parts[2] == "set":                    # command
                go(self._handle_command(parts[1], payload))
            elif len(parts) == 4 and parts[2] == "schedule" and parts[3] == "set":
                go(self._astore_week(parts[1], payload))                  # full week
            elif len(parts) == 5 and parts[2] == "schedule" and parts[4] == "set":
                go(self._astore_day(parts[1], parts[3], payload))         # single day
        except Exception as exc:  # noqa: BLE001
            log.warning("mqtt dispatch error on %s: %s",
                        getattr(msg, "topic", "?"), exc)

    # ---- commands ---------------------------------------------------------
    async def _handle_command(self, serial: str, payload: dict):
        dev = self.devices.get(serial)
        if not dev:
            log.warning("command for unknown/offline device %s", serial)
            return
        if not isinstance(payload, dict):
            log.warning("refused command to %s: payload is not a JSON object", serial)
            return
        if payload.get("resetFilter"):
            await self._send(dev, encode_filter_reset(serial))
            return
        async with self._lock:
            if self.protection != "NONE":
                log.info("command for %s suppressed — NeuraCell %s active",
                         serial, self.protection)
                return
            await self._apply_command(dev, payload)

    async def _apply_command(self, dev: Device, payload: dict):
        cur = dev.raw_codes
        try:
            mode_code = mode_to_code(payload.get("mode"), cur["mode"])
            speed_code = speed_to_code(payload.get("fanSpeed"), cur["speed"])
        except (TypeError, ValueError, OverflowError, AttributeError) as exc:
            log.warning("refused command to %s: unreadable payload %r (%s)",
                        dev.serial, payload, exc)
            return
        if mode_code is None or speed_code is None:          # never encode None
            log.warning("skip command with unresolved codes for %s: %s",
                        dev.serial, payload)
            return
        # Echoed (not user-chosen) humidity/light codes the unit reported outside
        # the table are replaced by confirmed defaults; explicit values are checked.
        echo_hum = cur["humidity"] if cur["humidity"] in VALID_HUMIDITY_CODES else 1
        echo_light = cur["light"] if cur["light"] in VALID_LIGHT_CODES else LEGACY_LIGHT_CODE
        try:
            humidity_code = _generic_to_code(payload.get("humidityLevel"), _REV_HUM, echo_hum)
            light_code = _generic_to_code(payload.get("lightSensor"), _REV_LIGHT, echo_light)
        except (TypeError, ValueError, OverflowError) as exc:
            log.warning("refused command to %s: unreadable payload %r (%s)",
                        dev.serial, payload, exc)
            return
        if not await self._send_mode(dev, mode_code, speed_code, humidity_code, light_code):
            return
        self.normal_codes[dev.serial] = (mode_code, speed_code)   # remember normal target
        if dev.serial in self.pending_restore and (
                payload.get("mode") is not None
                or mode_code not in self.prot_modes.get(dev.serial, ())):
            self._retarget_pending(dev, mode_code, speed_code)   # the user's choice wins
        log.info("-> %s cmd=%s (mode=%s speed=%s)", dev.serial, payload, mode_code, speed_code)

    async def _send_mode(self, dev: Device, mode_code: int, speed_code: int,
                         humidity_code: int, light_code: int) -> bool:
        """The ONLY path that writes a mode command. Refuses unconfirmed codes."""
        if not codes_valid(mode_code, speed_code, humidity_code, light_code):
            log.warning("refused command to %s: unconfirmed code(s) mode=%s speed=%s "
                        "humidity=%s light=%s", dev.serial, mode_code, speed_code,
                        humidity_code, light_code)
            return False
        return await self._send(dev, encode_mode_command(
            dev.serial, mode_code, speed_code, humidity_code, light_code))

    async def _send_codes(self, dev: Device, mode_code: int, speed_code: int,
                          protective: bool = False) -> bool:
        """Write mode/speed, echoing the unit's humidity/light codes.

        An echoed humidity/light code outside the table is replaced by a
        confirmed default. For protective writes (radon, dew-point, restore) an
        unexpected speed is replaced by LOW as well — protection must not depend
        on a clean echo."""
        cur = dev.raw_codes
        hum, light = cur["humidity"], cur["light"]
        # Echoed codes outside the table are never written back: confirmed defaults.
        if hum not in VALID_HUMIDITY_CODES:
            hum = 1                                       # NORMAL
        if light not in VALID_LIGHT_CODES:
            light = LEGACY_LIGHT_CODE
        if protective and speed_code not in VALID_SPEED_CODES:
            speed_code = FAN_LOW
        ok = await self._send_mode(dev, mode_code, speed_code, hum, light)
        if protective and not ok:
            log.error("protective write to %s refused (mode=%s speed=%s)",
                      dev.serial, mode_code, speed_code)
        return ok

    def _baseline_for(self, dev: Device) -> Optional[tuple]:
        """Restore target: last confirmed normal state, else the current echo if valid."""
        if dev.serial in self.normal_codes:
            return self.normal_codes[dev.serial]
        if not dev.last_status:
            return None              # no status yet: the first status protects it anyway
        m_, s_ = dev.raw_codes["mode"], dev.raw_codes["speed"]
        if m_ in VALID_MODE_CODES and s_ in VALID_SPEED_CODES:
            return (m_, s_)
        log.error("no confirmed normal state for %s — it will not be restored", dev.serial)
        return None

    # ---- schedule ---------------------------------------------------------
    def _store_week(self, serial: str, week: dict):
        self.schedules[serial] = week
        self._sched_applied.pop(serial, None)
        log.info("schedule (week) stored for %s: %d days",
                 serial, sum(1 for d in week.values() if d))

    def _store_day(self, serial: str, weekday: str, slots):
        if weekday not in WEEKDAYS:
            return
        self.schedules.setdefault(serial, {})[weekday] = normalize_week({weekday: slots}).get(weekday, [])
        self._sched_applied.pop(serial, None)
        log.info("schedule (%s) stored for %s", weekday, serial)

    async def _astore_week(self, serial: str, payload):
        # runs on the loop thread (marshalled from the paho callback)
        self._store_week(serial, normalize_week(payload))

    async def _astore_day(self, serial: str, weekday: str, slots):
        self._store_day(serial, weekday, slots)

    async def _scheduler_tick(self, now: Optional[_dt.datetime] = None):
        """Edge-triggered: apply a slot's mode/fanSpeed once when it becomes
        active. Suspended while a NeuraCell protection is engaged."""
        if not self.cfg.scheduler_enabled:
            return
        async with self._lock:
            if self.protection != "NONE":
                return
            now = now or self.now_fn()
            weekday = WEEKDAYS[now.weekday()]
            hhmm = now.strftime("%H:%M")
            for serial, week in list(self.schedules.items()):
                dev = self.devices.get(serial)
                if not dev:
                    continue
                slot = active_slot(week.get(weekday, []), hhmm)
                key = None if slot is None else (weekday, slot.get("start"), slot.get("end"),
                                                 slot.get("mode"), slot.get("fanSpeed"))
                if key == self._sched_applied.get(serial):
                    continue
                self._sched_applied[serial] = key
                if slot is None:
                    continue
                try:
                    mode_code = mode_to_code(slot.get("mode"), dev.raw_codes["mode"])
                    speed_code = speed_to_code(slot.get("fanSpeed"), dev.raw_codes["speed"])
                except (TypeError, ValueError, OverflowError) as exc:
                    log.warning("schedule slot for %s unreadable (%s) — skipped", serial, exc)
                    continue
                if mode_code is None or speed_code is None:
                    continue
                if not await self._send_codes(dev, mode_code, speed_code):
                    continue
                self.normal_codes[serial] = (mode_code, speed_code)
                if serial in self.pending_restore and (
                        slot.get("mode") is not None
                        or mode_code not in self.prot_modes.get(serial, ())):
                    self._retarget_pending(dev, mode_code, speed_code)
                log.info("schedule -> %s %s slot %s..%s mode=%s fan=%s", serial, weekday,
                         slot.get("start"), slot.get("end"), slot.get("mode"), slot.get("fanSpeed"))

    async def _scheduler_loop(self):
        while True:
            try:
                await self._scheduler_tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler tick error: %s", exc)
            await asyncio.sleep(self.cfg.scheduler_tick)

    # ---- NeuraCell-X : radon protection + dew-point control ---------------
    async def _handle_control(self, parts: list, raw: str):
        key = parts[1]
        leaf = parts[2] if len(parts) > 2 else ""
        # a manual topic answering again ends its post-restart hold
        self._holds.pop({("radon", "alarm"): "radon_manual",
                         ("dewpoint", "block"): "dew_manual"}.get((key, leaf), ""), None)
        if key == "weather":
            try:
                w = json.loads(raw or "{}")
            except Exception:  # noqa: BLE001
                w = {}
            self.weather = w if isinstance(w, dict) else {}   # reject non-object JSON
            self.weather_ts = self.now_fn()
        elif key == "radon" and leaf == "alarm":
            self.nc_manual_radon = raw.strip().upper() in ("ON", "1", "TRUE")
        elif key == "radon" and leaf == "value":
            try:
                self.nc_radon_value = float(raw)
                self.nc_radon_ts = self.now_fn()
                self._holds.pop("radon_value", None)    # a fresh measurement decides
            except (TypeError, ValueError):
                self.nc_radon_value = None
        elif key == "dewpoint" and leaf == "block":
            self.nc_manual_dewpoint = raw.strip().upper() in ("ON", "1", "TRUE")
        await self._neuracell_evaluate()

    def _indoor_dewpoint(self) -> Optional[float]:
        dps = [d.last_status.get("dewPoint") for d in list(self.devices.values())
               if d.last_status.get("dewPoint") is not None]
        return round(sum(dps) / len(dps), 2) if dps else None

    def _fresh(self, ts: Optional[_dt.datetime]) -> bool:
        if ts is None:
            return False
        return (self.now_fn() - ts).total_seconds() <= self.cfg.nc_input_ttl

    def _outdoor_dewpoint(self) -> Optional[float]:
        if not self.weather or not self._fresh(self.weather_ts):
            return None
        return dew_point(self.weather.get("temperature"), self.weather.get("humidity"))

    def _radon_active(self) -> bool:
        if self.nc_manual_radon:
            return True
        if self._held("radon_value") or self._held("radon_manual"):
            return True                              # restarted during RADON: input not back yet
        if self.nc_radon_value is None or not self._fresh(self.nc_radon_ts):
            return False
        return self.nc_radon_value >= self.cfg.radon_threshold

    def _dewpoint_block(self, indoor_dp, outdoor_dp) -> bool:
        # `_dew_blocked` is the AUTO hysteresis latch only; the manual override
        # is a separate OR term so toggling it never pollutes (or gets stuck in)
        # the auto latch.
        if not self.cfg.dewpoint_enabled:
            self._dew_blocked = False
        elif indoor_dp is not None and outdoor_dp is not None:
            margin = self.cfg.dewpoint_margin
            # True hysteresis band (±margin): block when the outside air is
            # clearly moister than inside; release only when clearly drier.
            # Prevents flapping (and physical OFF/on cycling) around a single
            # threshold.
            if self._dew_blocked:
                self._dew_blocked = not (outdoor_dp < indoor_dp - margin)
            else:
                self._dew_blocked = outdoor_dp >= indoor_dp + margin
        # else: enabled but data missing -> hold the last auto latch unchanged
        return self.nc_manual_dewpoint or self._dew_blocked or self._held("dew_manual")

    def _held(self, key: str) -> bool:
        until = self._holds.get(key)
        if until is None:
            return False
        if self.mono_fn() < until:
            return True
        del self._holds[key]
        log.warning("NeuraCell-X: no fresh %s input within NC_INPUT_TTL after restart", key)
        return False

    async def _neuracell_evaluate(self):
        if not self.cfg.neuracell_enabled:
            return
        async with self._lock:
            indoor_dp = self._indoor_dewpoint()
            outdoor_dp = self._outdoor_dewpoint()
            radon = self._radon_active()
            dew = self._dewpoint_block(indoor_dp, outdoor_dp)
            new = "RADON" if radon else ("DEWPOINT" if dew else "NONE")
            if new != self.protection:
                await self._apply_protection(new)
            if self.protection == "NONE" and self.pending_restore:
                await self._process_pending()
            self._publish_neuracell(radon, dew, indoor_dp, outdoor_dp)

    async def _send_protective(self, dev: Device, mode: int, speed: int) -> bool:
        """A protective write (radon/dew-point). Remembers that the unit may now
        show this mode, so a later restore recognises it — across several
        protection phases, too."""
        ms = self.prot_modes.setdefault(dev.serial, set())
        if mode not in ms:                         # write-ahead: saved before it goes out
            ms.add(mode)
            self._save_state()
        return await self._send_codes(dev, mode, speed, protective=True)

    def _confirm_normal(self, serial: str, mode: int, speed: int):
        """The unit reports a non-protective state again: that is its normal."""
        self.pending_restore.pop(serial, None)
        self.prot_modes.pop(serial, None)
        if mode in VALID_MODE_CODES and speed in VALID_SPEED_CODES:
            self.normal_codes[serial] = (mode, speed)

    def _retarget_pending(self, dev: Device, mode: int, speed: int):
        """A user command / schedule slot was just sent while a restore is open:
        it becomes the new target, still confirmed by the unit's next status
        (the write may have gone into a dead socket)."""
        p = self.pending_restore[dev.serial]
        p.update(target=(mode, speed), seq=dev.status_seq, attempts=1,
                 last_try=self.mono_fn())
        p.pop("gave_up", None)
        self._save_state()

    def _pending_baseline(self, serial: str, p: dict) -> tuple:
        """Baseline for a unit whose restore is still open when a new protection
        trips: if it already reported a non-protective state after the release,
        that state is its normal; otherwise the restore target is."""
        dev = self.devices.get(serial)
        if dev and dev.last_status and dev.status_seq > p["seq"]:
            em, es = dev.raw_codes.get("mode"), dev.raw_codes.get("speed")
            if (em not in self.prot_modes.get(serial, ()) or em == p["target"][0]) \
                    and em in VALID_MODE_CODES and es in VALID_SPEED_CODES:
                return (em, es)
        return p["target"]

    async def _try_restore(self, dev: Device, p: dict) -> None:
        m, sp = p["target"]
        p["attempts"] += 1
        p["last_try"] = self.mono_fn()
        p["seq"] = dev.status_seq                  # judge only a status after this try
        if not await self._send_codes(dev, m, sp, protective=True):
            p["attempts"] -= 1                     # nothing went out: not an attempt
            p["last_try"] = None

    async def _process_pending(self):
        """Confirm or repeat restores after a protection ended.

        Caller holds self._lock and protection is NONE. Each restore is judged
        by a status the unit sent AFTER the last try:
          * shows the target mode         -> done
          * still shows a protective mode -> send again (max RESTORE_MAX_ATTEMPTS,
                                             at least RESTORE_RETRY_MIN apart)
          * shows anything else           -> someone changed it; that wins
        This covers units that were offline at release and connections that
        died silently (the write 'succeeded' into a dead socket)."""
        now = self.mono_fn()
        changed = False
        for s, p in list(self.pending_restore.items()):
            try:
                if now - p["since"] > self.cfg.pending_restore_ttl:
                    log.warning("NeuraCell-X: restore of %s not confirmed within "
                                "PENDING_RESTORE_TTL — given up", s)
                    del self.pending_restore[s]; changed = True
                    continue
                dev = self.devices.get(s)
                if not dev or not dev.last_status or dev.status_seq <= p["seq"]:
                    continue
                em, es = dev.raw_codes.get("mode"), dev.raw_codes.get("speed")
                if em not in VALID_MODE_CODES:
                    continue                           # unreadable status: wait for the next
                if em == p["target"][0]:
                    self._confirm_normal(s, em, es); changed = True
                    log.info("NeuraCell-X: %s confirmed restore (mode=%s)", s, em)
                elif em in self.prot_modes.get(s, ()):
                    if p["attempts"] >= RESTORE_MAX_ATTEMPTS:
                        if not p.get("gave_up"):
                            p["gave_up"] = True
                            log.error("NeuraCell-X: %s still in protective mode %s after %d "
                                      "restore attempts — not retried", s, em, p["attempts"])
                        continue
                    if p["last_try"] is not None and now - p["last_try"] < RESTORE_RETRY_MIN:
                        continue
                    await self._try_restore(dev, p); changed = True
                    log.warning("NeuraCell-X: %s still in protective mode %s — restore "
                                "to mode=%s speed=%s (attempt %d)", s, em,
                                p["target"][0], p["target"][1], p["attempts"])
                else:
                    log.info("NeuraCell-X: %s was changed meanwhile (mode=%s) — restore "
                             "dropped", s, em)
                    self._confirm_normal(s, em, es); changed = True
            except Exception as exc:  # noqa: BLE001 — one bad entry must not stop the rest
                log.error("NeuraCell-X: pending restore of %s dropped: %s", s, exc)
                self.pending_restore.pop(s, None); changed = True
        if changed:
            self._save_state()

    # ---- state persistence (baselines / pending restores) ----------------
    def _save_state(self):
        if not self.cfg.state_file:
            return
        now = self.mono_fn()
        data = {
            "version": 2,
            "saved_at": self.wall_fn(),
            "protection": self.protection,
            "dew_latch": self._dew_blocked,
            "radon_value_active": (self.nc_radon_value is not None
                                   and self._fresh(self.nc_radon_ts)
                                   and self.nc_radon_value >= self.cfg.radon_threshold)
                                  or "radon_value" in self._holds,
            "radon_manual": self.nc_manual_radon or "radon_manual" in self._holds,
            "dew_manual": self.nc_manual_dewpoint or "dew_manual" in self._holds,
            "baseline": {s: list(t) for s, t in self.baseline.items()},
            "pending": {s: {"target": list(p["target"]),
                            "age": max(0.0, now - p["since"])}
                        for s, p in self.pending_restore.items()},
            "prot_modes": {s: sorted(v) for s, v in self.prot_modes.items() if v},
        }
        tmp = self.cfg.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.cfg.state_file)
        except OSError as exc:
            log.error("could not save state to %s: %s", self.cfg.state_file, exc)

    def _load_state(self):
        """Pick up where a previous run stopped.

        * Saved baselines of an active protection stay baselines: the units keep
          their REAL previous mode. RADON is held until a fresh radon value
          arrives (at most NC_INPUT_TTL); the dew-point hysteresis latch is
          restored, so DEWPOINT holds until fresh data says otherwise.
        * Saved pending restores continue (age carried over).
        Every entry is checked on its own; a broken one is skipped and logged."""
        if not self.cfg.state_file or not os.path.exists(self.cfg.state_file):
            return
        if not self.cfg.neuracell_enabled:
            log.warning("NEURACELL_ENABLED=false — saved protection state not loaded")
            return
        try:
            with open(self.cfg.state_file, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            if data.get("version") != 2:
                raise ValueError(f"unsupported version {data.get('version')!r}")
        except Exception as exc:  # noqa: BLE001
            log.error("could not load state from %s: %s — ignored", self.cfg.state_file, exc)
            return
        now = self.mono_fn()
        saved_at = data.get("saved_at")
        if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool):
            file_age = max(0.0, float(self.wall_fn()) - float(saved_at))
        else:
            file_age = 0.0
        for s, modes_ in (data.get("prot_modes") or {}).items() if isinstance(
                data.get("prot_modes"), dict) else ():
            if not isinstance(modes_, list):
                log.error("state: prot_modes of %s skipped (not a list)", s)
                continue
            ms = {x for x in modes_ if isinstance(x, int) and not isinstance(x, bool)}
            ms &= VALID_MODE_CODES
            if ms:
                self.prot_modes[str(s).upper()] = ms
        kind = data.get("protection")
        baseline = data.get("baseline") if isinstance(data.get("baseline"), dict) else {}
        if kind in ("DEWPOINT", "RADON"):
            for s, t in baseline.items():
                if self._valid_target(t):
                    self.baseline[str(s).upper()] = (t[0], t[1])
                else:
                    log.error("state: baseline of %s skipped", s)
        for key in ("radon_value", "radon_manual", "dew_manual"):
            flag = {"radon_value": "radon_value_active"}.get(key, key)
            if kind in ("DEWPOINT", "RADON") and data.get(flag) is True:
                self._holds[key] = now + self.cfg.nc_input_ttl
        if kind in ("DEWPOINT", "RADON") and data.get("dew_latch") is True:
            self._dew_blocked = True
        if kind in ("DEWPOINT", "RADON") and self.baseline:
            # Resume IN the protection: the first evaluation either keeps it or
            # releases it through the normal path (restore + confirmation).
            self.protection = kind
        pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
        for s, p in pending.items():
            try:
                t = p["target"]
                if not self._valid_target(t):
                    raise ValueError("invalid target")
                age = float(p.get("age", 0.0)) + file_age
                if not age >= 0:
                    raise ValueError("invalid age")
                key = str(s).upper()
                if key in self.baseline:
                    continue
                self.pending_restore[key] = {"target": (t[0], t[1]), "since": now - age,
                                             "seq": 0, "attempts": 0, "last_try": None}
            except Exception as exc:  # noqa: BLE001
                log.error("state: pending restore of %s skipped (%s)", s, exc)
        if self.baseline or self.pending_restore:
            log.warning("NeuraCell-X: state loaded — %s, %d baseline(s), %d pending restore(s)",
                        kind, len(self.baseline), len(self.pending_restore))

    @staticmethod
    def _valid_target(t) -> bool:
        return (isinstance(t, (list, tuple)) and len(t) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in t)
                and t[0] in VALID_MODE_CODES and t[1] in VALID_SPEED_CODES)

    async def _apply_protection(self, new: str):
        # Caller holds self._lock.
        prev = self.protection
        # Save baselines the first time we leave NONE (from the *normal* target,
        # not the last device echo, which may still be a protective code).
        if prev == "NONE" and new != "NONE":
            # Units still waiting for a restore keep their REAL normal state as
            # baseline (their current echo is still the old protective mode).
            for s, p in list(self.pending_restore.items()):
                self.baseline.setdefault(s, self._pending_baseline(s, p))
            self.pending_restore.clear()
            for s, dev in list(self.devices.items()):
                if s in self.baseline:
                    continue
                base = self._baseline_for(dev)
                if base is not None:
                    self.baseline[s] = base
        # Commit the new state BEFORE issuing any writes, so concurrent commands
        # and schedule ticks are suppressed for the whole apply, and a second
        # evaluate can't re-enter and double-apply.
        self.protection = new
        if new == "RADON":
            for dev in list(self.devices.values()):
                if not dev.last_status:
                    continue        # protected on its first status, after its state is known
                await self._send_protective(dev, self.cfg.radon_protect_mode,
                                            self.cfg.radon_protect_fan)
            if prev != new:
                log.warning("NeuraCell-X: RADON protection ON -> all units INTAKE/LOW")
        elif new == "DEWPOINT":
            for dev in list(self.devices.values()):
                if not dev.last_status:
                    continue        # protected on its first status, after its state is known
                await self._send_protective(dev, MODE_OFF, dev.raw_codes["speed"])
            if prev != new:
                log.warning("NeuraCell-X: dew-point block ON -> all units OFF")
        elif new == "NONE":
            now = self.mono_fn()
            for s, (m, sp) in list(self.baseline.items()):
                p = {"target": (m, sp), "since": now, "seq": self._status_seq,
                     "attempts": 0, "last_try": None}
                self.pending_restore[s] = p
                dev = self.devices.get(s)
                if dev and dev.last_status:
                    await self._try_restore(dev, p)      # confirmed by its next status
                else:
                    log.warning("NeuraCell-X: %s not reachable at release — restore to "
                                "mode=%s speed=%s when it reports back", s, m, sp)
            if prev != new:
                log.warning("NeuraCell-X: all clear -> exact restore of previous modes")
            self.baseline.clear()
        self._save_state()

    async def _protect_new_device(self, dev: Device):
        """A unit that (re)connects while a protection is active gets the
        protective target immediately, and its baseline is remembered."""
        async with self._lock:
            if self.protection == "NONE":
                return
            if dev.serial not in self.baseline:
                base = self._baseline_for(dev)
                if base is not None:
                    self.baseline[dev.serial] = base
                    self._save_state()
            if self.protection == "RADON":
                await self._send_protective(dev, self.cfg.radon_protect_mode,
                                            self.cfg.radon_protect_fan)
            elif self.protection == "DEWPOINT":
                await self._send_protective(dev, MODE_OFF, dev.raw_codes["speed"])

    def _publish_neuracell(self, radon, dew, indoor_dp, outdoor_dp):
        state = {
            "activeProtection": self.protection,
            "radonProtectionActive": radon,
            "ventilationBlockedDewpoint": dew and not radon,
            "radon": {"value": self.nc_radon_value,
                      "threshold": self.cfg.radon_threshold,
                      "manual": self.nc_manual_radon},
            "dewpoint": {"indoor": indoor_dp, "outdoor": outdoor_dp,
                         "margin": self.cfg.dewpoint_margin,
                         "manual": self.nc_manual_dewpoint},
            "protectedDevices": (sorted(k for k, d in self.devices.items() if d.last_status)
                                 if self.protection != "NONE" else []),
        }
        if self.pending_restore:
            state["pendingRestore"] = sorted(self.pending_restore)
        if self.addon:
            self._publish_addon_neuracell(radon, dew, indoor_dp, outdoor_dp)
        js = json.dumps(state, sort_keys=True)
        if js == self._last_nc_json:            # publish only on real change
            self._nc_deferred = None
            return
        wait = self._nc_hold_until - self.mono_fn()
        if wait > 0:                            # add-on profile: entities still moving
            self._nc_deferred = js
            if not self._nc_deferred_due and self.loop and wait != float("inf"):
                self._nc_deferred_due = True
                self.loop.call_later(wait + 0.05, self._publish_nc_deferred)
            return
        self._last_nc_json = js
        self.mqtt.publish(f"{self.cfg.mqtt_prefix}/neuracell/state", js, retain=True)

    def _publish_nc_deferred(self):
        self._nc_deferred_due = False
        js, self._nc_deferred = self._nc_deferred, None
        if js is None:
            return
        if self.mono_fn() < self._nc_hold_until:   # hold renewed by a reconnect
            self._nc_deferred = js
            self._nc_deferred_due = True
            self.loop.call_later(self._nc_hold_until - self.mono_fn() + 0.05,
                                 self._publish_nc_deferred)
            return
        if js != self._last_nc_json:
            self._last_nc_json = js
            self.mqtt.publish(f"{self.cfg.mqtt_prefix}/neuracell/state", js, retain=True)

    async def _neuracell_loop(self):
        while True:
            try:
                await self._neuracell_evaluate()
            except Exception as exc:  # noqa: BLE001
                log.warning("neuracell tick error: %s", exc)
            await asyncio.sleep(self.cfg.neuracell_tick)

    # ---- MQTT publish (status) -------------------------------------------
    def _publish_status(self, dev: Device):
        data = dict(dev.last_status)
        data.update(dev.firmware)
        self.mqtt.publish(self._topic(dev.serial, "status"), json.dumps(data), retain=True)
        self.mqtt.publish(self._topic(dev.serial, "availability"), "online", retain=True)

    def _publish_offline(self, serial: str):
        self.mqtt.publish(self._topic(serial, "availability"), "offline", retain=True)

    def _publish_discovery(self, serial: str):
        if not self.cfg.ha_discovery:
            return
        if self.addon:
            self._publish_addon_discovery(serial)
            return
        dev_obj = {
            "identifiers": [f"ambientika_{serial}"],
            "manufacturer": "Ambientika",
            "model": "Ambientika Smart/Office (local)",
            "name": f"Ambientika {serial}",
        }
        avail = [{"topic": self._topic(serial, "availability"),
                  "payload_available": "online", "payload_not_available": "offline"}]
        base, st, cmd = "homeassistant", self._topic(serial, "status"), self._topic(serial, "set")

        def cfg(component, key, payload):
            payload.update({"availability": avail, "device": dev_obj,
                            "unique_id": f"ambientika_{serial}_{key}"})
            self.mqtt.publish(f"{base}/{component}/ambientika_{serial}/{key}/config",
                              json.dumps(payload), retain=True)

        cfg("select", "mode", {
            "name": "Mode", "state_topic": st, "value_template": "{{ value_json.mode }}",
            "command_topic": cmd, "command_template": '{"mode": "{{ value }}"}',
            "options": list(APP_MODE_TO_PROTO.keys())})
        cfg("number", "fan_speed", {
            "name": "Fan speed", "state_topic": st,
            "value_template": "{{ value_json.fanSpeed }}",
            "command_topic": cmd, "command_template": '{"fanSpeed": {{ value }}}',
            "min": 0, "max": 100, "step": 5, "unit_of_measurement": "%"})
        cfg("sensor", "temperature", {
            "name": "Temperature", "state_topic": st, "device_class": "temperature",
            "unit_of_measurement": "°C", "value_template": "{{ value_json.temperature }}"})
        cfg("sensor", "humidity", {
            "name": "Humidity", "state_topic": st, "device_class": "humidity",
            "unit_of_measurement": "%", "value_template": "{{ value_json.humidity }}"})
        cfg("sensor", "dew_point", {
            "name": "Dew point", "state_topic": st, "device_class": "temperature",
            "unit_of_measurement": "°C", "value_template": "{{ value_json.dewPoint }}"})
        cfg("sensor", "air_quality", {
            "name": "Air quality", "state_topic": st,
            "value_template": "{{ value_json.airQualityLabel }}"})
        cfg("binary_sensor", "filter", {
            "name": "Filter alarm", "state_topic": st, "device_class": "problem",
            "value_template": "{{ 'ON' if value_json.filterAlarm else 'OFF' }}"})

    # ---- Home Assistant: add-on profile -------------------------------------
    def _iso_now(self) -> str:
        return _dt.datetime.fromtimestamp(self.wall_fn(), tz=_dt.timezone.utc).isoformat(
            timespec="seconds")

    def _addon_device_name(self, serial: str) -> str:
        return self.cfg.addon_device_names.get(serial, f"Ambientika {serial}")

    def _publish_addon_static(self):
        """Bridge and NeuraCell-X entities (retained, once per broker connection)."""
        p, base = self.cfg.mqtt_prefix, self.cfg.discovery_prefix
        configs = addon_bridge_discovery(p, base)
        if self.cfg.neuracell_enabled:
            configs += addon_neuracell_discovery(p, base)
        for topic, payload in configs:
            self.mqtt.publish(topic, json.dumps(payload), retain=True)
        self._addon_disc.clear()            # units again with their next status
        self._addon_nc_json = None
        self._nc_hold_until = self.mono_fn() + ADDON_NC_HOLD_S

    def _publish_addon_discovery(self, serial: str):
        for topic, payload in addon_discovery_configs(
                self.cfg.mqtt_prefix, self.cfg.discovery_prefix, serial,
                self._addon_device_name(serial)):
            self.mqtt.publish(topic, json.dumps(payload), retain=True)
        self._addon_disc.add(serial)

    def _addon_averages(self, serial: str, temp, hum) -> tuple:
        now = self.mono_fn()
        window = max(1.0, self.cfg.average_window_min) * 60.0
        rows = [r for r in self._addon_samples.get(serial, []) if now - r[0] <= window]
        rows.append((now, temp, hum))
        self._addon_samples[serial] = rows
        return (round(sum(r[1] for r in rows) / len(rows), 1),
                round(sum(r[2] for r in rows) / len(rows), 1))

    def _addon_zone(self, serial: str):
        t = self.cfg.setup_devices.get(serial)
        return getattr(t, "zone", None) if t is not None else None

    def _publish_addon_diag(self, serial: str):
        d = self._addon_diag.setdefault(serial, {
            "last_status": None, "poll_failures_total": 0, "poll_failures_consecutive": 0,
            "last_failure": None, "last_failure_reason": None, "last_outage_start": None,
            "last_outage_end": None, "last_outage_polls": None})
        d["bridge_started"] = self._started_iso
        self._addon_diag_at[serial] = self.mono_fn()
        self.mqtt.publish(f"{self.cfg.mqtt_prefix}/{serial}/diagnostics",
                          json.dumps(d), retain=True)

    def _publish_addon_state(self, dev: Device, frame: bytes):
        try:
            data = addon_fields_from_frame(frame)
        except ValueError as exc:
            log.warning("add-on state for %s not published: %s", dev.serial, exc)
            return
        if dev.serial not in self._addon_disc:
            self._publish_addon_discovery(dev.serial)
        data["temperature_avg"], data["humidity_avg"] = self._addon_averages(
            dev.serial, data["temperature"], data["humidity"])
        data["zone_index"] = self._addon_zone(dev.serial)
        speed = dev.raw_codes.get("speed")
        if speed in (0, 1, 2):
            self._addon_last_speed[dev.serial] = speed
        light = dev.raw_codes.get("light")
        if dev.raw_codes.get("mode") != MODE_OFF and light in VALID_LIGHT_CODES:
            self._addon_last_light[dev.serial] = light      # an Off unit reports a default
        sent = self._addon_sent.get(dev.serial)
        if sent and all(dev.raw_codes.get(k) == v for k, v in sent["codes"].items()
                        if k in ("mode", "speed")):
            self._addon_sent.pop(dev.serial, None)          # the unit reports it: done
        self.mqtt.publish(f"{self.cfg.mqtt_prefix}/{dev.serial}/state",
                          json.dumps(data), retain=True)
        diag = self._addon_diag.get(dev.serial)
        now = self._iso_now()
        first = diag is None
        if first:
            self._publish_addon_diag(dev.serial)
            diag = self._addon_diag[dev.serial]
        diag["last_status"] = now
        outage_over = bool(diag["poll_failures_consecutive"])
        if outage_over:
            diag["poll_failures_consecutive"] = 0
            diag["last_outage_end"] = now
        last = self._addon_diag_at.get(dev.serial)
        if first or outage_over or last is None or self.mono_fn() - last >= ADDON_DIAG_MIN_S:
            self._publish_addon_diag(dev.serial)

    def _addon_note_disconnect(self, serial: str):
        """A lost unit connection counts as a failed poll (local equivalent)."""
        self._publish_addon_diag(serial)                    # make sure the record exists
        d = self._addon_diag[serial]
        now = self._iso_now()
        d["poll_failures_total"] += 1
        d["poll_failures_consecutive"] += 1
        d["last_failure"] = now
        d["last_failure_reason"] = "connection closed"
        if d["poll_failures_consecutive"] == 1:
            d["last_outage_start"] = now
            d["last_outage_end"] = None
        self._publish_addon_diag(serial)

    def _publish_addon_neuracell(self, radon: bool, dew: bool, indoor_dp, outdoor_dp):
        state = {
            "radon_protection": bool(radon),
            "radon": self.nc_radon_value,
            "radon_meter_connected": (self._fresh(self.nc_radon_ts)
                                      if self.nc_radon_ts is not None else None),
            "dewpoint_controller_connected": None,          # no dew-point controller link
            "dewpoint_block": bool(dew and not radon),
            "dewpoint_fallback": False,
            "indoor_dew_point": round(indoor_dp, 1) if indoor_dp is not None else None,
            "outdoor_dew_point": round(outdoor_dp, 1) if outdoor_dp is not None else None,
        }
        js = json.dumps(state, sort_keys=True)
        if js == self._addon_nc_json:
            return
        self._addon_nc_json = js
        self.mqtt.publish(f"{self.cfg.mqtt_prefix}/neuracell/addon_state", js, retain=True)

    async def _handle_addon_command(self, serial: str, attr: str, raw: str):
        cmd = parse_addon_command(attr, raw)
        if cmd is None:
            log.warning("add-on command %s=%r for %s not understood - ignored",
                        attr, raw, serial)
            return
        if cmd.get("resetFilter"):
            await self._handle_command(serial, cmd)
            return
        if attr == "fan_speed" and (raw or "").strip().strip('"').upper() in ("NIGHT", "TURBO"):
            log.warning("fan speed %r for %s is set by the unit itself - sending %s instead",
                        raw, serial, cmd["fanSpeed"])
        self._addon_pending.setdefault(serial, {}).update(cmd)
        task = self._addon_flush.get(serial)
        if task is None or task.done():
            self._addon_flush[serial] = asyncio.create_task(self._addon_flush_later(serial))

    async def _addon_flush_later(self, serial: str):
        await asyncio.sleep(ADDON_COALESCE_S)
        cmd = self._addon_pending.pop(serial, None)
        if not cmd:
            return
        dev = self.devices.get(serial)
        payload = self._addon_full_payload(dev, cmd) if dev else cmd
        sendable = dev is not None and self.protection == "NONE"
        await self._handle_command(serial, payload)
        if sendable:
            codes = {"mode": mode_to_code(payload.get("mode"), None),
                     "speed": speed_to_code(payload.get("fanSpeed"), None),
                     "humidity": _generic_to_code(payload.get("humidityLevel"), _REV_HUM, None),
                     "light": _generic_to_code(payload.get("lightSensor"), _REV_LIGHT, None)}
            self._addon_sent[serial] = {"codes": codes, "at": self.mono_fn()}

    def _addon_full_payload(self, dev: Device, cmd: dict) -> dict:
        """Complete a per-attribute command with the unit's state: the state just
        sent while the unit has not reported it yet, else its last report. A
        unit-chosen fan level (Night) is replaced by its last regular level."""
        base = {k: dev.raw_codes.get(k) for k in ("mode", "speed", "humidity", "light")}
        if base["mode"] == MODE_OFF and dev.serial in self._addon_last_light:
            base["light"] = self._addon_last_light[dev.serial]
        sent = self._addon_sent.get(dev.serial)
        if sent and self.mono_fn() - sent["at"] < ADDON_OVERLAY_S:
            base.update({k: v for k, v in sent["codes"].items() if v is not None})
        out = {}
        if base["mode"] in VALID_MODE_CODES:
            out["mode"] = base["mode"]
        speed = base["speed"]
        if speed not in (0, 1, 2):
            speed = self._addon_last_speed.get(dev.serial, FAN_LOW)
        out["fanSpeed"] = FAN_SPEED[speed]
        if base["humidity"] in VALID_HUMIDITY_CODES:
            out["humidityLevel"] = HUMIDITY_LEVEL[base["humidity"]]
        if base["light"] in VALID_LIGHT_CODES:
            out["lightSensor"] = LIGHT_SENS[base["light"]]
        out.update(cmd)
        return out

    # ---- TCP --------------------------------------------------------------
    async def _send(self, dev: Device, frame: bytes) -> bool:
        try:
            async with dev.send_lock:            # serialise writes per device
                dev.writer.write(frame)
                await dev.writer.drain()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("write to %s failed: %s", dev.serial, exc)
            return False

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("device connected from %s", peer)
        serial: Optional[str] = None
        buf = b""
        junk = bytearray()              # bytes of frame types we do not know
        seen_unknown: set = set()       # leading bytes already logged at INFO
        conn_len: Optional[int] = None  # status length proven on this connection
        # Serial we trust for the resync MAC check: only from a firmware frame or
        # from a frame that arrived cleanly (no junk before it). A frame found
        # while resyncing could itself be a phantom and must not lock out the unit.
        trusted: Optional[str] = None
        after_resync = False            # previous consumed frame was found while resyncing
        try:
            while True:
                chunk = await reader.read(256)
                if not chunk:
                    break
                buf += chunk
                while buf:
                    b0 = buf[0]
                    if b0 in KNOWN_DEVICE_FRAMES:
                        if len(buf) < 8:
                            break                         # need type, 0x00 and MAC
                        # A real frame starts with <type> 0x00. While resyncing
                        # after unknown bytes it must also carry the MAC of the
                        # unit already trusted on this connection; otherwise it is
                        # a 0x01/0x03/0x04 byte inside a frame we do not know.
                        if buf[1] != 0x00 or (junk and trusted
                                              and parse_serial(buf) != trusted):
                            if len(junk) < 64:
                                junk.append(b0)
                            buf = buf[1:]
                            continue
                    if b0 == FRAME_STATUS:                # status (19 or 21 bytes)
                        n = resolve_status_len(buf, self._known_radio_fw(buf), conn_len)
                        if n is None:
                            break                         # wait for the full frame
                    elif b0 == FRAME_FIRMWARE:            # firmware info (18 bytes)
                        n = FIRMWARE_LEN if len(buf) >= FIRMWARE_LEN else None
                        if n is None:
                            break
                    elif b0 == FRAME_PING:                # master keep-alive, no state
                        n = PING_LEN
                    else:
                        # Unknown leading byte: no other unit->bridge frame is
                        # known. Logged, never answered. Drop one byte and
                        # resync, keep it for the log.
                        if len(junk) < 64:
                            junk.append(b0)
                        buf = buf[1:]
                        continue
                    # --- a complete known frame is consumed from here on ---
                    # decided only on consumption; a frame directly after one found
                    # while resyncing is not trusted either (phantom chains)
                    clean = not junk and not after_resync
                    if b0 == FRAME_PING:            # a ping never ends a resync
                        after_resync = after_resync or bool(junk)
                    else:
                        after_resync = bool(junk)
                    if junk:
                        self._log_unknown(serial, bytes(junk), seen_unknown)
                        junk.clear()
                    frame, buf = buf[:n], buf[n:]
                    if b0 == FRAME_PING:
                        continue
                    if b0 == FRAME_STATUS and n == STATUS_LEN_LEGACY:
                        conn_len = n                      # proven: keep for this connection
                    serial = parse_serial(frame)
                    if clean:
                        trusted = serial
                    dev = self._register(serial, writer)
                    if b0 == FRAME_FIRMWARE:
                        dev.firmware = decode_firmware(frame)
                        self._publish_discovery(serial)
                        continue
                    first = not dev.last_status
                    dev.last_status = decode_status(frame)
                    self._status_seq += 1
                    dev.status_seq = self._status_seq
                    dev.raw_codes = status_raw_codes(frame)
                    if (self.protection == "NONE"
                            and serial not in self.pending_restore
                            and serial not in self.baseline
                            and dev.raw_codes["mode"] in VALID_MODE_CODES
                            and dev.raw_codes["speed"] in VALID_SPEED_CODES):
                        # track the true normal target for the restore baseline
                        # (only confirmed mode/speed, so a restore is never refused)
                        self.normal_codes[serial] = (dev.raw_codes["mode"],
                                                     dev.raw_codes["speed"])
                    need_setup = not dev.setup_sent
                    if need_setup:
                        dev.setup_sent = True             # decide once per connection
                        target, why = setup_decision(serial, self.cfg,
                                                     status_role_code(frame))
                        if target is not None:
                            await self._send(dev, encode_setup(
                                serial, target.role, target.zone, target.house))
                            log.info("setup -> %s role=%s zone=%s house=%s", serial,
                                     target.role, target.zone, target.house)
                        else:
                            log.info("no setup for %s: %s", serial, why)
                    self._publish_status(dev)
                    if self.addon:
                        self._publish_addon_state(dev, frame)
                    # (re)apply protection on first connect or after a reconnect
                    if self.protection != "NONE" and (first or need_setup):
                        await self._protect_new_device(dev)
                    await self._neuracell_evaluate()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("connection error (%s): %s", serial, exc)
        finally:
            if junk:
                self._log_unknown(serial, bytes(junk), seen_unknown)
            if serial and self.devices.get(serial) and self.devices[serial].writer is writer:
                self._publish_offline(serial)
                if self.addon:
                    self._addon_note_disconnect(serial)
                self.devices.pop(serial, None)
                log.info("device %s disconnected", serial)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _known_radio_fw(self, buf: bytes) -> Optional[str]:
        """Radio firmware of the unit whose frame is pending, if already reported."""
        if len(buf) < 8:
            return None
        dev = self.devices.get(parse_serial(buf))
        return dev.firmware.get("radioFw") if dev else None

    @staticmethod
    def _log_unknown(serial: Optional[str], data: bytes, seen: set):
        """Unknown frame types are logged (first of each type at INFO), never answered."""
        level = logging.DEBUG if data[0] in seen else logging.INFO
        seen.add(data[0])
        log.log(level, "unknown frame type 0x%02X from %s (%d bytes, not answered): %s",
                data[0], serial or "?", len(data), data[:32].hex())

    def _register(self, serial: str, writer: asyncio.StreamWriter) -> Device:
        dev = self.devices.get(serial)
        if dev is None:
            dev = Device(serial=serial, writer=writer)
            self.devices[serial] = dev
            log.info("registered device %s", serial)
        elif dev.writer is not writer:
            # Reconnect on a new transport: keep runtime state (raw_codes,
            # last_status, firmware) but swap the writer and re-send setup once.
            dev.writer = writer
            dev.setup_sent = False
            log.info("device %s reconnected (writer swapped)", serial)
        return dev

    # ---- lifecycle --------------------------------------------------------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        avail_topic = f"{self.cfg.mqtt_prefix}/bridge/availability"
        # Last-will so the broker flips the bridge offline if we die unexpectedly.
        self.mqtt.will_set(avail_topic, "offline", retain=True)
        self.mqtt.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.mqtt.loop_start()
        self.mqtt.publish(avail_topic, "online", retain=True)
        server = await asyncio.start_server(
            self._handle_conn, self.cfg.tcp_host, self.cfg.tcp_port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        log.info("Ambientika LOCAL bridge listening on %s (cloud-free)", addrs)
        tasks = [asyncio.create_task(server.serve_forever())]
        if self.cfg.scheduler_enabled:
            tasks.append(asyncio.create_task(self._scheduler_loop()))
        if self.cfg.neuracell_enabled:
            tasks.append(asyncio.create_task(self._neuracell_loop()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            server.close()
            try:
                self.mqtt.publish(avail_topic, "offline", retain=True)
                self.mqtt.loop_stop()
                self.mqtt.disconnect()
            except Exception:  # noqa: BLE001
                pass


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bridge = LocalBridge(Config())

    async def _amain():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        task = asyncio.create_task(bridge.run())
        await stop.wait()
        task.cancel()

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
