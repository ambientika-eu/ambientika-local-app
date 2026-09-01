#!/usr/bin/env python3
"""
ambientika_protocol.py
======================

Wire codec for the Ambientika Smart / Office raw-TCP protocol (port 11000),
extracted out of ``ambientika_local_bridge.py`` so the framing rules can be
unit-tested on their own and reviewed independently of the MQTT plumbing.

What changed versus the inline codec in the bridge
--------------------------------------------------
1. **Variable-length status frames.** The old demux read a fixed 21 bytes for
   every ``0x01`` frame. Units with radio firmware 0.0.11 emit **19** bytes
   (upstream issue #5): the layout is identical on offsets 0..18 and simply
   omits the two fields that were added later — light sensor and RSSI. The
   length is now resolved per device, primarily from the firmware frame and
   only as a fallback by inspecting the stream.

2. **Never guess.** ``resolve_status_len`` returns ``None`` when it cannot
   decide yet. The caller waits for more bytes instead of decoding at the
   wrong offsets. A status whose layout is unconfirmed is not published.

3. **Sensor calibration.** Optional per-device temperature / humidity offsets
   are applied before the dew point is computed, and the raw readings are kept
   alongside the corrected ones so the correction stays auditable.

Nothing here talks to the network or to MQTT — it is pure functions plus one
small dataclass, so every rule below is covered by ``test_protocol.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Frame types and lengths
# ---------------------------------------------------------------------------
FRAME_STATUS = 0x01
FRAME_FIRMWARE = 0x03
FRAME_SERVER = 0x02          # server -> device (command / setup / filter reset)

STATUS_LEN_MODERN = 21       # ... + lightSensor + rssi
STATUS_LEN_LEGACY = 19       # radio fw 0.0.11 and older
FIRMWARE_LEN = 18

#: Radio firmware at or above this version emits the 21-byte status frame.
#: Confirmed boundary is unknown; 0.0.11 is legacy, 0.0.28 is modern. Anything
#: in between must be resolved by the firmware frame, not by this constant, so
#: the threshold is deliberately set at the first version known to be modern.
FW_MODERN_MIN = (0, 0, 28)

#: Byte offsets shared by both layouts.
OFF_MODE = 8
OFF_SPEED = 9
OFF_HUMIDITY_LEVEL = 10
OFF_TEMPERATURE = 11
OFF_HUMIDITY = 12
OFF_AIR_QUALITY = 13
OFF_HUMIDITY_ALARM = 14
OFF_FILTER_STATUS = 15
OFF_NIGHT_ALARM = 16
OFF_DEVICE_ROLE = 17
OFF_LAST_MODE = 18
#: Present only in the 21-byte layout.
OFF_LIGHT_SENSOR = 19
OFF_RSSI = 20

# ---------------------------------------------------------------------------
# Enums (unchanged from the bridge — kept here so the codec is self-contained)
# ---------------------------------------------------------------------------
OPERATING_MODE = {
    0: "SMART", 1: "AUTO", 2: "MANUAL_HEAT_RECOVERY", 3: "NIGHT",
    4: "AWAY_HOME", 5: "SURVEILLANCE", 6: "TIMED_EXPULSION", 7: "EXPULSION",
    8: "INTAKE", 9: "MASTER_SLAVE_FLOW", 10: "SLAVE_MASTER_FLOW", 11: "OFF",
}
FAN_SPEED = {0: "LOW", 1: "MEDIUM", 2: "HIGH", 3: "NIGHT"}
HUMIDITY_LEVEL = {0: "DRY", 1: "NORMAL", 2: "MOIST"}
DEVICE_ROLE = {0: "MASTER", 1: "SLAVE_EQUAL_MASTER", 2: "SLAVE_OPPOSITE_MASTER"}
AIR_QUALITY = {0: "VERY_GOOD", 1: "GOOD", 2: "MEDIUM", 3: "POOR", 4: "BAD"}
FILTER_STATUS = {0: "GOOD", 1: "MEDIUM", 2: "BAD"}
LIGHT_SENS = {0: "NOT_AVAILABLE", 1: "OFF", 2: "LOW", 3: "MEDIUM"}

APP_MODE_TO_PROTO = {
    "SMART": 0, "ECO": 1, "HRV": 2, "NIGHT": 3, "BOOST": 6, "OFF": 11,
}
PROTO_TO_APP_MODE = {v: k for k, v in APP_MODE_TO_PROTO.items()}
LEVEL_TO_PCT = {0: 40, 1: 70, 2: 100, 3: 15}

#: Default light-sensor code assumed for legacy units, which have no such
#: sensor. Matches the bridge's previous default so command encoding is
#: unchanged for these devices.
LEGACY_LIGHT_CODE = 1        # "OFF"


def _s8(b: int) -> int:
    """Unsigned byte -> signed 8-bit. Temperature and RSSI are both signed."""
    return b - 256 if b >= 128 else b


# ---------------------------------------------------------------------------
# Firmware version handling
# ---------------------------------------------------------------------------
def parse_fw_tuple(text: Optional[str]) -> Optional[tuple]:
    """'0.0.11' -> (0, 0, 11). Returns None for anything unparseable."""
    if not text:
        return None
    parts = str(text).split(".")
    try:
        return tuple(int(p) for p in parts)
    except (TypeError, ValueError):
        return None


def status_len_for_firmware(radio_fw: Optional[str]) -> Optional[int]:
    """Expected status length for a radio firmware string, or None if unknown.

    Only versions at or above :data:`FW_MODERN_MIN` are claimed as modern; the
    versions in between stay undecided on purpose, so a device in that range
    falls through to the stream probe rather than being mis-parsed.
    """
    ver = parse_fw_tuple(radio_fw)
    if ver is None:
        return None
    if ver >= FW_MODERN_MIN:
        return STATUS_LEN_MODERN
    if ver <= (0, 0, 11):
        return STATUS_LEN_LEGACY
    return None


def probe_status_len(buf: bytes) -> Optional[int]:
    """Decide the status length by looking at the bytes, or None if undecidable.

    Two independent checks, both of which must be unambiguous:

    * If a *new* frame appears to start at offset 19 — a known leading byte
      followed by the constant ``0x00`` at offset 20 — then the current frame
      ended at 19 and this is a legacy unit.
    * Otherwise, offsets 19/20 must look like a plausible light-sensor code
      (0..3) and a plausible RSSI (negative dBm). A light-sensor reading paired
      with an RSSI of exactly 0 dBm does not occur in practice, which is what
      makes the two cases separable.
    """
    if len(buf) < STATUS_LEN_MODERN:
        return None
    if buf[OFF_LIGHT_SENSOR] in (FRAME_STATUS, FRAME_FIRMWARE) and buf[OFF_RSSI] == 0x00:
        return STATUS_LEN_LEGACY
    if buf[OFF_LIGHT_SENSOR] <= 3 and _s8(buf[OFF_RSSI]) < 0:
        return STATUS_LEN_MODERN
    return None


def resolve_status_len(buf: bytes, radio_fw: Optional[str] = None,
                       forced: Optional[int] = None) -> Optional[int]:
    """Resolve how many bytes the pending status frame occupies.

    Order of authority: an explicit override, then the device's reported
    firmware, then the stream probe. Returns None when the decision has to
    wait for more bytes — the caller must not decode in that case.
    """
    if forced in (STATUS_LEN_LEGACY, STATUS_LEN_MODERN):
        return forced if len(buf) >= forced else None
    by_fw = status_len_for_firmware(radio_fw)
    if by_fw is not None:
        return by_fw if len(buf) >= by_fw else None
    return probe_status_len(buf)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Calibration:
    """Per-device sensor correction, in physical units.

    ``temp`` is added to the reported temperature in °C, ``rh`` to the reported
    relative humidity in percentage points. The corrected humidity is clamped
    to 0..100 so a large offset can never produce an impossible reading or a
    dew point built on one.
    """
    temp: float = 0.0
    rh: float = 0.0

    def apply(self, temp_c: int, rh_pct: int) -> tuple:
        # With no offset configured the readings are passed through untouched,
        # including their integer type. Returning 27.0 where the bridge used to
        # publish 27 would be a silent payload change for every existing
        # installation, so the zero case must be a true no-op.
        if not self.active:
            return temp_c, rh_pct
        t = round(temp_c + self.temp, 1)
        r = round(rh_pct + self.rh, 1)
        r = max(0.0, min(100.0, r))
        return t, r

    @property
    def active(self) -> bool:
        return self.temp != 0.0 or self.rh != 0.0


NO_CALIBRATION = Calibration()


def parse_calibration(raw) -> dict:
    """Build {SERIAL: Calibration} from a JSON object.

    Accepts ``{"1C9DC2430444": {"temp": -3.2, "rh": 6}}``. Unknown keys and
    unparseable values are ignored rather than raising, so a typo in the
    environment cannot stop the bridge from starting.
    """
    import json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for serial, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        try:
            out[str(serial).upper()] = Calibration(
                temp=float(spec.get("temp", 0.0)),
                rh=float(spec.get("rh", 0.0)),
            )
        except (TypeError, ValueError):
            continue
    return out


def dew_point(temp_c, rh_pct) -> Optional[float]:
    """Dew point in °C, Magnus-Tetens with Sonntag coefficients."""
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
# Decoding
# ---------------------------------------------------------------------------
#: Ranges a genuine reading must fall into. These are deliberately generous —
#: the point is not to validate the sensor but to catch a *mis-parsed frame*,
#: where the bytes land in the wrong fields and produce something physically
#: impossible. A reverse-engineered layout that drifts shows up here first.
PLAUSIBLE_TEMP_C = (-40, 60)
PLAUSIBLE_RH_PCT = (0, 100)
PLAUSIBLE_RSSI_DBM = (-100, -10)


def implausible_fields(decoded: dict) -> list:
    """Return a list of reasons the decoded status cannot be a real reading.

    An empty list means nothing obviously wrong was found — which is not the
    same as "the mapping is correct". This catches gross misparsing, not a
    subtly wrong offset.
    """
    problems = []

    temp = decoded.get("temperature")
    if temp is None or not (PLAUSIBLE_TEMP_C[0] <= temp <= PLAUSIBLE_TEMP_C[1]):
        problems.append(f"temperature {temp} outside {PLAUSIBLE_TEMP_C} °C")

    rh = decoded.get("humidity")
    if rh is None or not (PLAUSIBLE_RH_PCT[0] <= rh <= PLAUSIBLE_RH_PCT[1]):
        problems.append(f"humidity {rh} outside {PLAUSIBLE_RH_PCT} %")

    if str(decoded.get("modeProtocol", "")).startswith("UNKNOWN"):
        problems.append(f"unknown operating mode: {decoded.get('modeProtocol')}")

    if str(decoded.get("fanLevel", "")).startswith("UNKNOWN"):
        problems.append(f"unknown fan level: {decoded.get('fanLevel')}")

    if str(decoded.get("deviceRole", "")).startswith("UNKNOWN"):
        problems.append(f"unknown device role: {decoded.get('deviceRole')}")

    rssi = decoded.get("rssi")
    if rssi is not None and not (PLAUSIBLE_RSSI_DBM[0] <= rssi <= PLAUSIBLE_RSSI_DBM[1]):
        # An RSSI of exactly 0 is the signature of the old fixed-21 misparse.
        problems.append(f"rssi {rssi} dBm outside {PLAUSIBLE_RSSI_DBM}")

    return problems


def parse_serial(buf: bytes) -> str:
    return buf[2:8].hex().upper()


def serial_to_mac(serial: str) -> bytes:
    return bytes.fromhex(serial)


def app_mode_name(code: int) -> str:
    return PROTO_TO_APP_MODE.get(code, OPERATING_MODE.get(code, f"UNKNOWN_{code}"))


class StatusFrameLengthError(ValueError, IndexError):
    """A status frame whose length the codec does not know.

    Derived from both ``ValueError`` and ``IndexError`` on purpose: the old
    in-bridge decoder simply indexed past the end of a short buffer, so the
    repository's existing tests and any caller written against them catch
    ``IndexError``. Raising a named error with a useful message should not cost
    anybody a rewrite.
    """


def decode_status(buf: bytes, calibration: Calibration = NO_CALIBRATION) -> dict:
    """Decode a 19- or 21-byte status frame into the app's vocabulary.

    Raises :class:`StatusFrameLengthError` on a length the codec does not know,
    so an unexpected frame surfaces as an error instead of silently producing
    wrong readings.
    """
    n = len(buf)
    if n not in (STATUS_LEN_LEGACY, STATUS_LEN_MODERN):
        raise StatusFrameLengthError(f"unsupported status frame length: {n}")

    mode_code = buf[OFF_MODE]
    speed_code = buf[OFF_SPEED]
    temp_raw = _s8(buf[OFF_TEMPERATURE])
    rh_raw = buf[OFF_HUMIDITY]
    temp, rh = calibration.apply(temp_raw, rh_raw)

    aq_raw = buf[OFF_AIR_QUALITY]
    if aq_raw <= 0:                       # 0 = sensor not ready / no reading yet
        aq_class, aq_label = 0, "UNKNOWN_SENSOR"
    else:
        aq_class = min(aq_raw - 1, 4)
        aq_label = AIR_QUALITY.get(aq_class, f"UNKNOWN_{aq_raw}")

    out = {
        "serial": parse_serial(buf),
        "name": parse_serial(buf),
        "mode": app_mode_name(mode_code),
        "fanSpeed": LEVEL_TO_PCT.get(speed_code, 0),
        "temperature": temp,
        "humidity": rh,
        "airQuality": aq_class,
        "filterAlarm": buf[OFF_FILTER_STATUS] == 2,
        "dewPoint": dew_point(temp, rh),
        "online": True,
        "airQualityLabel": aq_label,
        "humidityLevel": HUMIDITY_LEVEL.get(buf[OFF_HUMIDITY_LEVEL],
                                            f"UNKNOWN_{buf[OFF_HUMIDITY_LEVEL]}"),
        "humidityAlarm": bool(buf[OFF_HUMIDITY_ALARM]),
        "filterStatus": FILTER_STATUS.get(buf[OFF_FILTER_STATUS],
                                          f"UNKNOWN_{buf[OFF_FILTER_STATUS]}"),
        "nightAlarm": bool(buf[OFF_NIGHT_ALARM]),
        "deviceRole": DEVICE_ROLE.get(buf[OFF_DEVICE_ROLE],
                                      f"UNKNOWN_{buf[OFF_DEVICE_ROLE]}"),
        "lastMode": app_mode_name(buf[OFF_LAST_MODE]),
        "modeProtocol": OPERATING_MODE.get(mode_code, f"UNKNOWN_{mode_code}"),
        "fanLevel": FAN_SPEED.get(speed_code, f"UNKNOWN_{speed_code}"),
        "statusFrameLength": n,
    }

    # Keep the uncorrected readings visible whenever a correction was applied,
    # so the published value can always be traced back to what the unit sent.
    if calibration.active:
        out["temperatureRaw"] = temp_raw
        out["humidityRaw"] = rh_raw
        out["calibration"] = {"temp": calibration.temp, "rh": calibration.rh}

    if n >= STATUS_LEN_MODERN:
        out["lightSensor"] = LIGHT_SENS.get(buf[OFF_LIGHT_SENSOR],
                                            f"UNKNOWN_{buf[OFF_LIGHT_SENSOR]}")
        out["rssi"] = _s8(buf[OFF_RSSI])
    else:
        # The legacy firmware has neither field. Report them as absent rather
        # than inventing a value, so an automation can tell the difference.
        out["lightSensor"] = "NOT_AVAILABLE"
        out["rssi"] = None

    return out


def status_raw_codes(buf: bytes) -> dict:
    """The four codes a mode command has to echo back unchanged."""
    n = len(buf)
    light = buf[OFF_LIGHT_SENSOR] if n >= STATUS_LEN_MODERN else LEGACY_LIGHT_CODE
    return {
        "mode": buf[OFF_MODE],
        "speed": buf[OFF_SPEED],
        "humidity": buf[OFF_HUMIDITY_LEVEL],
        "light": light,
    }


def status_role_code(buf: bytes) -> int:
    """The unit's own master/slave role, as reported by the unit itself."""
    return buf[OFF_DEVICE_ROLE]


def decode_firmware(buf: bytes) -> dict:
    return {
        "serial": parse_serial(buf),
        "radioFw": f"{buf[8]}.{buf[9]}.{buf[10]}",
        "microFw": f"{buf[11]}.{buf[12]}.{buf[13]}",
        "radioAtFw": f"{buf[14]}.{buf[15]}.{buf[16]}.{buf[17]}",
    }


# ---------------------------------------------------------------------------
# Encoding (unchanged wire format)
# ---------------------------------------------------------------------------
def encode_mode_command(serial: str, mode_code: int, speed_code: int,
                        humidity_code: int, light_code: int) -> bytes:
    return bytes(
        [FRAME_SERVER, 0x00]
        + list(serial_to_mac(serial))
        + [0x01, mode_code & 0xFF, speed_code & 0xFF,
           humidity_code & 0xFF, light_code & 0xFF]
    )


def encode_filter_reset(serial: str) -> bytes:
    return bytes([FRAME_SERVER, 0x00] + list(serial_to_mac(serial)) + [0x03])


def encode_setup(serial: str, role: int, zone: int, house_id: int) -> bytes:
    hid = max(0, min(int(house_id), 0xFFFFFFFF))
    return bytes(
        [FRAME_SERVER, 0x00]
        + list(serial_to_mac(serial))
        + [0x00, role & 0xFF, zone & 0xFF, 0x00]
        + list(hid.to_bytes(4, "little"))
    )


# ---------------------------------------------------------------------------
# Stream framing
# ---------------------------------------------------------------------------
class FrameReader:
    """Splits the device's TCP byte stream into frames.

    One reader per connection. TCP delivers an arbitrarily chopped stream, so
    the reader buffers and yields only complete frames. Replaces the fixed
    ``buf[:21]`` slice in the bridge's connection handler.

    The firmware frame is what teaches the reader how long this device's status
    frames are, so ``feed`` yields firmware frames as soon as they arrive and
    the caller is expected to hand the radio version back via
    :meth:`set_radio_fw`. Until the length is known the reader falls back to the
    stream probe, and while even that is undecided it holds the bytes rather
    than decoding them at the wrong offsets.
    """

    def __init__(self, radio_fw: Optional[str] = None,
                 forced_len: Optional[int] = None,
                 max_buffer: int = 4096):
        self.buf = bytearray()
        self.radio_fw = radio_fw
        self.forced_len = forced_len
        self.max_buffer = max_buffer
        self.resync_count = 0

    def set_radio_fw(self, radio_fw: Optional[str]) -> None:
        self.radio_fw = radio_fw

    @property
    def known_status_len(self) -> Optional[int]:
        if self.forced_len:
            return self.forced_len
        return status_len_for_firmware(self.radio_fw)

    def feed(self, chunk: bytes):
        """Append bytes and yield ``(kind, frame)`` for every complete frame.

        ``kind`` is ``"status"`` or ``"firmware"``. Unknown leading bytes are
        dropped one at a time to resynchronise instead of stalling the
        connection forever.
        """
        self.buf.extend(chunk)
        # A runaway buffer means we are permanently out of sync; drop the oldest
        # bytes so a wedged connection recovers rather than growing without end.
        if len(self.buf) > self.max_buffer:
            del self.buf[:len(self.buf) - self.max_buffer]

        while self.buf:
            b0 = self.buf[0]

            if b0 == FRAME_STATUS:
                n = resolve_status_len(bytes(self.buf), self.radio_fw, self.forced_len)
                if n is None:
                    return                       # wait for more bytes
                if len(self.buf) < n:
                    return
                frame = bytes(self.buf[:n])
                del self.buf[:n]
                yield ("status", frame)

            elif b0 == FRAME_FIRMWARE:
                if len(self.buf) < FIRMWARE_LEN:
                    return
                frame = bytes(self.buf[:FIRMWARE_LEN])
                del self.buf[:FIRMWARE_LEN]
                yield ("firmware", frame)

            else:
                del self.buf[:1]
                self.resync_count += 1
