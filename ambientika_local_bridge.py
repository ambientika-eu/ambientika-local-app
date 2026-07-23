#!/usr/bin/env python3
"""
ambientika_local_bridge.py
==========================

Cloud-independent local bridge for Ambientika Smart / Office ventilation units.

The device firmware opens a persistent *outbound* raw-TCP connection to whatever
host:port it was provisioned with (normally app.ambientika.eu:11000). This module
implements the server side of that connection locally, so the units can be
controlled and monitored entirely on the LAN — no internet, no SUEDWIND cloud.

Drop-in replacement for the cloud-polling MQTT bridge in the ambientika-local-app
stack: it publishes the same topics AND speaks the same field vocabulary the app
expects (friendly mode names + fanSpeed 0-100 %), so the FastAPI backend and PWA
work unchanged with zero cloud contact.

Now cloud-free end-to-end, including:
  • device monitoring + control                    (status <-> commands)
  • weekly schedule execution (Wochenzeitplan)      (schedule/* topics)
  • NeuraCell-X: radon protection + dew-point       (radon/dewpoint topics)
    control, with priority and exact restore.

    Device (WiFi, TCP:11000) <-> [THIS bridge] <-> MQTT <-> local-app / HA

--------------------------------------------------------------------------------
CLEAN-ROOM NOTE
--------------------------------------------------------------------------------
Fresh implementation written only from the documented binary-protocol spec
(PROTOCOL.md / CLOUD-INTEGRATION.md). No source copied from
sragas/ambientika-local-control ("personal use only") or its fork.

--------------------------------------------------------------------------------
SAFETY / PRODUCT SIGN-OFF (read before shipping)
--------------------------------------------------------------------------------
This drives real ventilation and, via NeuraCell-X, radon and moisture behaviour.
1. Validate against real hardware first — the binary offsets are reverse-engineered.
   In particular the SIGNED decoding of temperature and RSSI (see _s8) must be
   confirmed on a real unit.
2. Devices only reach this server if redirected to it (BLE H_<host>:11000, or a
   static route/DNAT for 185.214.203.87). See CLOUD-INTEGRATION.md.
3. The control THRESHOLDS and mappings are sensible defaults, not certified:
     - operating-mode / fan mappings  (>>> MAPPING <<<)
     - radon threshold, dew-point margin, protection targets  (>>> CONTROL <<<)
   Have them reviewed/signed off and tuned to the product spec.
4. Dew-point control needs OUTDOOR temperature+humidity, which the device packet
   does not contain. Publish it locally to `ambientika/weather`
   ({"temperature": t, "humidity": rh}); without it, auto dew-point is inactive
   and only the manual `ambientika/dewpoint/block` override works.

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
from dataclasses import dataclass, field
from typing import Callable, Optional

import paho.mqtt.client as mqtt

log = logging.getLogger("ambientika.local")


# ---------------------------------------------------------------------------
# Wire-protocol enums  (source: PROTOCOL.md)
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

_REV_MODE = {v: k for k, v in OPERATING_MODE.items()}
_REV_SPEED = {v: k for k, v in FAN_SPEED.items()}
_REV_HUM = {v: k for k, v in HUMIDITY_LEVEL.items()}
_REV_LIGHT = {v: k for k, v in LIGHT_SENS.items()}

# ---------------------------------------------------------------------------
# App-vocabulary mapping  (local-app: HRV|NIGHT|BOOST|ECO|SMART|OFF, fan 0-100)
# ---------------------------------------------------------------------------
# >>> MAPPING <<<  friendly app mode  <->  wire operating-mode code
APP_MODE_TO_PROTO = {
    "SMART": 0, "ECO": 1, "HRV": 2, "NIGHT": 3, "BOOST": 6, "OFF": 11,
}
PROTO_TO_APP_MODE = {v: k for k, v in APP_MODE_TO_PROTO.items()}

# >>> MAPPING <<<  fan level  <->  percentage the PWA slider uses.
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
    if s.isdigit():
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
    if s.replace(".", "", 1).isdigit():
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
    if s.isdigit():
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
# Packet codec  (byte layout verified against PROTOCOL.md examples)
# ---------------------------------------------------------------------------
def parse_serial(buf: bytes) -> str:
    return buf[2:8].hex().upper()


def serial_to_mac(serial: str) -> bytes:
    return bytes.fromhex(serial)


def decode_status(buf: bytes) -> dict:
    """21-byte device status -> app-compatible dict (+ raw fields for HA/debug)."""
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
    return {
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
        "lightSensor": LIGHT_SENS.get(buf[19], f"UNKNOWN_{buf[19]}"),
        "rssi": _s8(buf[20]),                # signed dBm
        "modeProtocol": OPERATING_MODE.get(mode_code, f"UNKNOWN_{mode_code}"),
        "fanLevel": FAN_SPEED.get(speed_code, f"UNKNOWN_{speed_code}"),
    }


def status_raw_codes(buf: bytes) -> dict:
    return {"mode": buf[8], "speed": buf[9], "humidity": buf[10], "light": buf[19]}


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
    # Clamp house_id into the unsigned 32-bit range so a mis-set HOUSE_ID can
    # never raise OverflowError (which would loop the device on every connect).
    hid = max(0, min(int(house_id), 0xFFFFFFFF))
    return bytes(
        [0x02, 0x00]
        + list(serial_to_mac(serial))
        + [0x00, role & 0xFF, zone & 0xFF, 0x00]
        + list(hid.to_bytes(4, "little"))
    )


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
        if h.isdigit() and mn.isdigit():
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
    # per-device write serialisation so two coroutines never overlap drain()
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
    setup_role: int = int(os.getenv("DEVICE_ROLE", "0"))
    setup_zone: int = int(os.getenv("DEVICE_ZONE", "0"))
    setup_house: int = int(os.getenv("HOUSE_ID", "1"))
    send_setup: bool = os.getenv("SEND_SETUP", "true").lower() == "true"
    # scheduler
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    scheduler_tick: int = int(os.getenv("SCHEDULER_TICK", "30"))
    # NeuraCell-X  >>> CONTROL <<<
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


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------
class LocalBridge:
    def __init__(self, cfg: Config, now_fn: Callable[[], _dt.datetime] = _dt.datetime.now):
        self.cfg = cfg
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
        client.subscribe(f"{p}/+/set")
        client.subscribe(f"{p}/+/schedule/set")
        client.subscribe(f"{p}/+/schedule/+/set")
        client.subscribe(f"{p}/radon/alarm")
        client.subscribe(f"{p}/radon/value")
        client.subscribe(f"{p}/dewpoint/block")
        client.subscribe(f"{p}/weather")

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
        mode_code = mode_to_code(payload.get("mode"), cur["mode"])
        speed_code = speed_to_code(payload.get("fanSpeed"), cur["speed"])
        if mode_code is None or speed_code is None:          # never encode None
            log.warning("skip command with unresolved codes for %s: %s",
                        dev.serial, payload)
            return
        humidity_code = _generic_to_code(payload.get("humidityLevel"), _REV_HUM, cur["humidity"])
        light_code = _generic_to_code(payload.get("lightSensor"), _REV_LIGHT, cur["light"])
        await self._send(dev, encode_mode_command(
            dev.serial, mode_code, speed_code, humidity_code, light_code))
        self.normal_codes[dev.serial] = (mode_code, speed_code)   # remember normal target
        log.info("-> %s cmd=%s (mode=%s speed=%s)", dev.serial, payload, mode_code, speed_code)

    async def _send_codes(self, dev: Device, mode_code: int, speed_code: int):
        cur = dev.raw_codes
        await self._send(dev, encode_mode_command(
            dev.serial, mode_code, speed_code, cur["humidity"], cur["light"]))

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
                mode_code = mode_to_code(slot.get("mode"), dev.raw_codes["mode"])
                speed_code = speed_to_code(slot.get("fanSpeed"), dev.raw_codes["speed"])
                if mode_code is None or speed_code is None:
                    continue
                await self._send_codes(dev, mode_code, speed_code)
                self.normal_codes[serial] = (mode_code, speed_code)
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
        return self.nc_manual_dewpoint or self._dew_blocked

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
            self._publish_neuracell(radon, dew, indoor_dp, outdoor_dp)

    async def _apply_protection(self, new: str):
        # Caller holds self._lock.
        prev = self.protection
        # Save baselines the first time we leave NONE (from the *normal* target,
        # not the last device echo, which may still be a protective code).
        if prev == "NONE" and new != "NONE":
            for s, dev in list(self.devices.items()):
                self.baseline[s] = self.normal_codes.get(
                    s, (dev.raw_codes["mode"], dev.raw_codes["speed"]))
        # Commit the new state BEFORE issuing any writes, so concurrent commands
        # and schedule ticks are suppressed for the whole apply, and a second
        # evaluate can't re-enter and double-apply.
        self.protection = new
        if new == "RADON":
            for dev in list(self.devices.values()):
                await self._send_codes(dev, self.cfg.radon_protect_mode, self.cfg.radon_protect_fan)
            if prev != new:
                log.warning("NeuraCell-X: RADON protection ON -> all units INTAKE/LOW")
        elif new == "DEWPOINT":
            for dev in list(self.devices.values()):
                await self._send_codes(dev, MODE_OFF, dev.raw_codes["speed"])
            if prev != new:
                log.warning("NeuraCell-X: dew-point block ON -> all units OFF")
        elif new == "NONE":
            for s, (m, sp) in list(self.baseline.items()):
                dev = self.devices.get(s)
                if dev:
                    await self._send_codes(dev, m, sp)
            if prev != new:
                log.warning("NeuraCell-X: all clear -> exact restore of previous modes")
            self.baseline.clear()

    async def _protect_new_device(self, dev: Device):
        """A unit that (re)connects while a protection is active gets the
        protective target immediately, and its baseline is remembered."""
        async with self._lock:
            if self.protection == "NONE":
                return
            self.baseline.setdefault(dev.serial, self.normal_codes.get(
                dev.serial, (dev.raw_codes["mode"], dev.raw_codes["speed"])))
            if self.protection == "RADON":
                await self._send_codes(dev, self.cfg.radon_protect_mode, self.cfg.radon_protect_fan)
            elif self.protection == "DEWPOINT":
                await self._send_codes(dev, MODE_OFF, dev.raw_codes["speed"])

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
            "protectedDevices": sorted(self.baseline.keys()),
        }
        js = json.dumps(state, sort_keys=True)
        if js == self._last_nc_json:            # publish only on real change
            return
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

    # ---- TCP --------------------------------------------------------------
    async def _send(self, dev: Device, frame: bytes):
        try:
            async with dev.send_lock:            # serialise writes per device
                dev.writer.write(frame)
                await dev.writer.drain()
        except Exception as exc:  # noqa: BLE001
            log.warning("write to %s failed: %s", dev.serial, exc)

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("device connected from %s", peer)
        serial: Optional[str] = None
        buf = b""
        try:
            while True:
                chunk = await reader.read(256)
                if not chunk:
                    break
                buf += chunk
                while buf:
                    b0 = buf[0]
                    if b0 == 0x01:                        # status (21 bytes)
                        if len(buf) < 21:
                            break                         # wait for the full frame
                        frame, buf = buf[:21], buf[21:]
                        serial = parse_serial(frame)
                        dev = self._register(serial, writer)
                        first = not dev.last_status
                        dev.last_status = decode_status(frame)
                        dev.raw_codes = status_raw_codes(frame)
                        if self.protection == "NONE":
                            # track the true normal target for the restore baseline
                            self.normal_codes[serial] = (dev.raw_codes["mode"],
                                                         dev.raw_codes["speed"])
                        need_setup = self.cfg.send_setup and not dev.setup_sent
                        if need_setup:
                            await self._send(dev, encode_setup(
                                serial, self.cfg.setup_role, self.cfg.setup_zone, self.cfg.setup_house))
                            dev.setup_sent = True
                        self._publish_status(dev)
                        # (re)apply protection on first connect or after a reconnect
                        if self.protection != "NONE" and (first or need_setup):
                            await self._protect_new_device(dev)
                        await self._neuracell_evaluate()
                    elif b0 == 0x03:                      # firmware info (18 bytes)
                        if len(buf) < 18:
                            break
                        frame, buf = buf[:18], buf[18:]
                        serial = parse_serial(frame)
                        dev = self._register(serial, writer)
                        dev.firmware = decode_firmware(frame)
                        self._publish_discovery(serial)
                    else:
                        # Unknown leading byte: drop one byte and resync, instead
                        # of stalling this connection forever.
                        buf = buf[1:]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("connection error (%s): %s", serial, exc)
        finally:
            if serial and self.devices.get(serial) and self.devices[serial].writer is writer:
                self._publish_offline(serial)
                self.devices.pop(serial, None)
                log.info("device %s disconnected", serial)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

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
