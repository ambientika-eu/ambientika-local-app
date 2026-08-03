#!/usr/bin/env python3
"""
Ambientika Local App – FastAPI Backend
======================================
Runs 100% locally – no SUEDWIND cloud server required.
Communicates with Ambientika devices via MQTT bridge.
Exposes REST + WebSocket API for the PWA.

Features:
- Device control (mode, fanSpeed)
- Weekly schedule (Wochenzeitplan) per device/day
- Smart-mode logic overview

Usage:
  pip install -r requirements.txt
  uvicorn main:app --host 0.0.0.0 --port 8080 --reload
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

# Verbindliche Text<->Zahl-Mappings der Historie werden auch hier fuer die
# Normalisierung des realen Bridge-States wiederverwendet (eine Quelle).
from history import mappings as M

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER   = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_PREFIX   = os.getenv("MQTT_PREFIX", "ambientika")
LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger("ambientika-local")

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------
devices:   Dict[str, Dict[str, Any]] = {}
schedules: Dict[str, Dict[str, List[Dict]]] = {}
# schedules[device_id][weekday] = [ {start, end, mode, fanSpeed}, ... ]
# weekday: "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun"

ws_clients: List[WebSocket] = []

# NeuraCell-X (patent pending) – radon protection + dew-point control status
# Populated from the retained "ambientika/neuracell/state" topic published by the bridge.
neuracell_state: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Bridge-State-Normalisierung
# ---------------------------------------------------------------------------
# Die Bridge liefert native Feldnamen (operating_mode, fan_speed, air_quality,
# filters_status, device_role, zone_index ...). Fuer die bestehende PWA werden
# zusaetzlich kompatible Alias-Felder erzeugt, ohne die Rohdaten zu verlieren.
#
# OperatingMode (IntEnum, ambientika_py): Index == nativer Enumwert 0..11.
OPERATING_MODE_NAMES = [
    "Smart", "Auto", "ManualHeatRecovery", "Night", "AwayHome", "Surveillance",
    "TimedExpulsion", "Expulsion", "Intake", "MasterSlaveFlow", "SlaveMasterFlow", "Off",
]
# OperatingMode-Name -> altes PWA-Token (nur fuer die Anzeige der alten Oberflaeche).
_MODE_NAME_TO_TOKEN = {
    "Smart": "SMART", "Auto": "AUTO", "ManualHeatRecovery": "MANUAL_HRV",
    "Night": "NIGHT", "AwayHome": "AWAY", "Surveillance": "MONITORING",
    "TimedExpulsion": "TIMED_EXHAUST", "Expulsion": "EXHAUST", "Intake": "SUPPLY",
    "MasterSlaveFlow": "MS_FLOW", "SlaveMasterFlow": "SM_FLOW", "Off": "OFF",
}
# Erlaubte Kommando-Attribute an die Bridge (<prefix>/<serial>/set/<attr>).
_SETTABLE_ATTRS = ("operating_mode", "fan_speed", "humidity_level", "light_sensor_level")


def _mode_num_to_name(num: Optional[int]) -> Optional[str]:
    if num is None:
        return None
    if 0 <= int(num) < len(OPERATING_MODE_NAMES):
        return OPERATING_MODE_NAMES[int(num)]
    return None


def normalize_state(serial: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Realen Bridge-State in das interne Geraete-Dict uebersetzen.

    Enthaelt die ROHFELDER der Bridge (verlustfrei) UND kompatible Alias-Felder
    fuer die bestehende PWA. Vorhandene Felder (z.B. online aus availability)
    werden vom Aufrufer beibehalten.
    """
    d: Dict[str, Any] = dict(payload)   # Rohdaten der Bridge unveraendert uebernehmen
    d["serial"] = serial
    d["deviceId"] = serial
    # Bridge-State enthaelt keinen Klarnamen -> Seriennummer als Anzeigename.
    d.setdefault("name", payload.get("name") or serial)

    # -- Betriebsmodus --
    op_mode = payload.get("operating_mode")
    if op_mode is not None:
        d["mode"] = _MODE_NAME_TO_TOKEN.get(str(op_mode), str(op_mode))

    # -- Rolle / Zone --
    role = payload.get("device_role")
    if role is not None:
        d["role"] = str(role).upper()
    zi = payload.get("zone_index")
    if zi is not None:
        d["zone"] = f"Zone {zi}"        # truthy + lesbar (zone_index 0 bleibt erhalten)

    # -- Luftqualitaet (numerisch-sicher fuer die Alt-UI + Smart-Endpoint) --
    aq_voc, aq_text, aq_num = M.air_quality_normalize(payload.get("air_quality"))
    d["airQuality"] = aq_voc            # nur Zahl (ppm) oder None -> nie String
    d["airQualityText"] = aq_text
    d["airQualityLevel"] = aq_num

    # -- Luefter (3-stufig) --
    fan_num = M.fan_speed_to_num(payload.get("fan_speed"))
    d["fanSpeed"] = fan_num             # 1..3 (Kompat-Zahl)
    d["fanSpeedText"] = payload.get("fan_speed")

    # -- Filterampel + Alt-Alarm-Bool --
    fil_text, fil_num = M.filter_status_normalize(payload.get("filters_status"))
    d["filterStatus"] = fil_text
    d["filterStatusNum"] = fil_num
    d["filterAlarm"] = M.filter_num_is_alarm(fil_num)

    return d


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
mqtt_client = mqtt.Client(client_id="ambientika-local-app")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("MQTT connected – subscribing to %s/+/state", MQTT_PREFIX)
        # Realer Bridge-Contract: Zustand kommt auf <prefix>/<serial>/state.
        client.subscribe(f"{MQTT_PREFIX}/+/state")
        client.subscribe(f"{MQTT_PREFIX}/+/availability")
        client.subscribe(f"{MQTT_PREFIX}/neuracell/state")  # NeuraCell-X status
    else:
        logger.warning("MQTT connection failed rc=%s", rc)

def on_message(client, userdata, msg):
    try:
        parts = msg.topic.split("/")
        if len(parts) < 3:
            return
        # --- NeuraCell-X status (patent-pending radon + dew-point control) ---
        if parts[1] == "neuracell":
            global neuracell_state
            try:
                neuracell_state = json.loads(msg.payload.decode())
            except Exception:
                return
            asyncio.run_coroutine_threadsafe(
                broadcast({"event": "neuracell", "data": neuracell_state}), loop)
            return
        device_id, kind = parts[1], parts[2]
        if kind == "availability":
            # Availability-Payload ist ein Klartext ("online"/"offline"),
            # gelegentlich auch ein kleines JSON {"state": "..."}.
            raw = msg.payload.decode().strip()
            try:
                pj = json.loads(raw)
                online = (pj.get("state") == "online") if isinstance(pj, dict) else (pj == "online")
            except Exception:
                online = (raw.lower() == "online")
            prev = devices.get(device_id, {})
            prev["serial"] = device_id
            prev.setdefault("deviceId", device_id)
            prev["online"] = online
            prev["lastSeen"] = int(time.time())
            devices[device_id] = prev
            event = "availability"
        elif kind == "state":
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return
            norm = normalize_state(device_id, payload)
            norm["lastSeen"] = int(time.time())
            # Online-Status aus availability nicht ueberschreiben.
            if "online" in devices.get(device_id, {}):
                norm["online"] = devices[device_id]["online"]
            devices[device_id] = norm
            event = "status"   # Kompat: die PWA lauscht auf 'status'
        else:
            return
        asyncio.run_coroutine_threadsafe(
            broadcast({"event": event, "deviceId": device_id,
                       "data": devices.get(device_id, {})}),
            loop,
        )
    except Exception as exc:
        logger.error("MQTT message error: %s", exc)

async def broadcast(message: dict):
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)

loop: asyncio.AbstractEventLoop

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    if MQTT_USER:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        logger.info("MQTT loop started")
    except Exception as exc:
        logger.warning("Could not connect to MQTT broker: %s", exc)
    history_sampler.start()
    yield
    await history_sampler.stop()
    mqtt_client.loop_stop()
    mqtt_client.disconnect()

app = FastAPI(
    title="Ambientika Local App API",
    description="Local REST + WebSocket API for Ambientika ventilation units",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
# --- Lokale Messwert-Historie (SQLite + MQTT/HA, keine Cloud) ---
from history.sampler import HistorySampler
from history.routes import make_history_router

history_sampler = HistorySampler(
    get_devices=lambda: devices,
    mqtt_client=mqtt_client,
    mqtt_prefix=MQTT_PREFIX,
)
app.include_router(make_history_router(history_sampler))


class DeviceCommand(BaseModel):
    # Kompat: die PWA schickt weiterhin mode/fanSpeed. Zusaetzlich koennen die
    # nativen Bridge-Attribute direkt gesetzt werden. Alle optional.
    mode:               Optional[str] = None   # PWA-Token ODER OperatingMode-Name ODER 0..11
    fanSpeed:           Optional[Any] = None   # 'Low'/'Medium'/'High', 1..3 oder 0..100 (Legacy)
    operating_mode:     Optional[str] = None   # OperatingMode-Name (direkt)
    fan_speed:          Optional[str] = None   # 'Low'|'Medium'|'High' (direkt)
    humidity_level:     Optional[str] = None   # 'Dry'|'Normal'|'Moist'
    light_sensor_level: Optional[str] = None   # 'NotAvailable'|'Off'|'Low'|'Medium'

class DeviceInfo(BaseModel):
    # extra='allow' -> die verlustfrei uebernommenen Bridge-Rohfelder
    # (operating_mode, filters_status, zone_index ...) bleiben in der Antwort.
    model_config = ConfigDict(extra="allow")
    deviceId:       str
    serial:         Optional[str]   = None
    name:           Optional[str]   = None
    mode:           Optional[str]   = None   # PWA-Token (aus operating_mode)
    role:           Optional[str]   = None
    zone:           Optional[str]   = None
    temperature:    Optional[float] = None
    humidity:       Optional[int]   = None
    airQuality:     Optional[int]   = None   # VOC/ppm-Zahl oder None (nie String)
    airQualityText: Optional[str]   = None   # 5-stufige Kategorie
    airQualityLevel: Optional[int]  = None   # 0..4
    fanSpeed:       Optional[int]   = None   # 1..3
    fanSpeedText:   Optional[str]   = None   # Low|Medium|High
    filterAlarm:    Optional[bool]  = None
    filterStatus:   Optional[str]   = None   # gruen|gelb|rot
    online:         Optional[bool]  = None
    lastSeen:       Optional[int]   = None

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

class TimeSlot(BaseModel):
    start:    str            # "HH:MM"
    end:      str            # "HH:MM"
    mode:     str            # HRV | NIGHT | BOOST | ECO | SMART | OFF
    fanSpeed: Optional[int] = None  # 0-100, None = auto

class DaySchedule(BaseModel):
    slots: List[TimeSlot]

class WeekSchedule(BaseModel):
    mon: Optional[List[TimeSlot]] = []
    tue: Optional[List[TimeSlot]] = []
    wed: Optional[List[TimeSlot]] = []
    thu: Optional[List[TimeSlot]] = []
    fri: Optional[List[TimeSlot]] = []
    sat: Optional[List[TimeSlot]] = []
    sun: Optional[List[TimeSlot]] = []

# ---------------------------------------------------------------------------
# REST – Devices
# ---------------------------------------------------------------------------
@app.get("/api/devices", response_model=List[DeviceInfo], tags=["Devices"])
async def list_devices():
    """Return all known Ambientika devices with their current state."""
    return [DeviceInfo(**{**state, "deviceId": did}) for did, state in devices.items()]

@app.get("/api/devices/{device_id}", response_model=DeviceInfo, tags=["Devices"])
async def get_device(device_id: str):
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceInfo(**{**devices[device_id], "deviceId": device_id})


def _resolve_commands(cmd: "DeviceCommand") -> Dict[str, str]:
    """Uebersetzt ein Kommando in native Bridge-Attribute + Enum-Namen.

    Rueckgabe: {attr: enum_name} fuer je <prefix>/<serial>/set/<attr>.
    Akzeptiert sowohl die Alt-PWA-Form (mode/fanSpeed) als auch native Felder.
    """
    out: Dict[str, str] = {}

    # Betriebsmodus: Token/Name/Zahl -> OperatingMode-Name
    mode_val = cmd.operating_mode if cmd.operating_mode is not None else cmd.mode
    if mode_val is not None:
        num = None
        s = str(mode_val).strip()
        if s.isdigit():
            num = int(s)
        else:
            num = M.mode_to_num(s)
        name = _mode_num_to_name(num)
        if name is None:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {mode_val}")
        out["operating_mode"] = name

    # Luefter: Name/Stufe/Prozent -> FanSpeed-Name
    fan_val = cmd.fan_speed if cmd.fan_speed is not None else cmd.fanSpeed
    if fan_val is not None:
        fan_name = M.fan_num_to_name(M.fan_speed_to_num(fan_val))
        if fan_name is None:
            raise HTTPException(status_code=400, detail=f"Unknown fanSpeed: {fan_val}")
        out["fan_speed"] = fan_name

    # Feuchtestufe / Lichtsensor: direkt als Enum-Name (validiert)
    if cmd.humidity_level is not None:
        if M.humidity_level_to_num(cmd.humidity_level) is None:
            raise HTTPException(status_code=400, detail=f"Unknown humidity_level: {cmd.humidity_level}")
        out["humidity_level"] = str(cmd.humidity_level).capitalize()
    if cmd.light_sensor_level is not None:
        out["light_sensor_level"] = str(cmd.light_sensor_level)

    return out

@app.post("/api/devices/{device_id}/command", tags=["Devices"])
async def send_command(device_id: str, cmd: DeviceCommand):
    """Send a command to a device via MQTT (per-attribute set/<attr> topics)."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    attrs = _resolve_commands(cmd)
    if not attrs:
        raise HTTPException(status_code=400, detail="Provide mode / fanSpeed / humidity_level / light_sensor_level")
    published = []
    for attr, value in attrs.items():
        if attr not in _SETTABLE_ATTRS:
            continue
        topic = f"{MQTT_PREFIX}/{device_id}/set/{attr}"
        mqtt_client.publish(topic, value, qos=1)   # Bridge parst per Enum-Name
        published.append({"topic": topic, "value": value})
        logger.info("Command → %s: %s = %s", device_id, attr, value)
    return {"status": "ok", "commands": published}

@app.post("/api/devices/{device_id}/filter/reset", tags=["Devices"])
async def reset_filter(device_id: str):
    """Filterzaehler/-alarm des Geraets zuruecksetzen.

    Publiziert set/reset_filter; die Bridge ruft den Cloud-Endpunkt
    device/reset-filter auf. Funktioniert ZUSTANDSUNABHAENGIG - also auch
    schon bei 'gelb' (verschmutzt), bevor der App-eigene Reset-Button (erst
    bei 'rot') erscheint.
    """
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    topic = f"{MQTT_PREFIX}/{device_id}/set/reset_filter"
    mqtt_client.publish(topic, "PRESS", qos=1)
    logger.info("Filter-Reset → %s", device_id)
    return {"status": "ok", "topic": topic}

# ---------------------------------------------------------------------------
# REST – Schedule (Wochenzeitplan)
# ---------------------------------------------------------------------------
@app.get("/api/devices/{device_id}/schedule",
         response_model=WeekSchedule, tags=["Schedule"])
async def get_schedule(device_id: str):
    """Return the weekly schedule for a device."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    raw = schedules.get(device_id, {})
    return WeekSchedule(**{day: raw.get(day, []) for day in WEEKDAYS})

@app.put("/api/devices/{device_id}/schedule",
         response_model=WeekSchedule, tags=["Schedule"])
async def set_schedule(device_id: str, week: WeekSchedule):
    """Save (replace) the full weekly schedule for a device."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    schedules[device_id] = {
        day: [s.dict() for s in (getattr(week, day) or [])]
        for day in WEEKDAYS
    }
    logger.info("Schedule saved for %s", device_id)
    # Publish schedule to device via MQTT
    topic = f"{MQTT_PREFIX}/{device_id}/schedule/set"
    mqtt_client.publish(topic, json.dumps(schedules[device_id]), qos=1)
    return week

@app.get("/api/devices/{device_id}/schedule/{weekday}",
         response_model=List[TimeSlot], tags=["Schedule"])
async def get_day_schedule(device_id: str, weekday: str):
    """Return schedule slots for a single weekday (mon–sun)."""
    if weekday not in WEEKDAYS:
        raise HTTPException(status_code=400, detail=f"weekday must be one of {WEEKDAYS}")
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    return schedules.get(device_id, {}).get(weekday, [])

@app.put("/api/devices/{device_id}/schedule/{weekday}",
         response_model=List[TimeSlot], tags=["Schedule"])
async def set_day_schedule(device_id: str, weekday: str, slots: List[TimeSlot]):
    """Replace schedule slots for a single weekday."""
    if weekday not in WEEKDAYS:
        raise HTTPException(status_code=400, detail=f"weekday must be one of {WEEKDAYS}")
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    if device_id not in schedules:
        schedules[device_id] = {}
    schedules[device_id][weekday] = [s.dict() for s in slots]
    topic = f"{MQTT_PREFIX}/{device_id}/schedule/{weekday}/set"
    mqtt_client.publish(topic, json.dumps(schedules[device_id][weekday]), qos=1)
    logger.info("Day schedule saved for %s/%s", device_id, weekday)
    return slots

@app.delete("/api/devices/{device_id}/schedule/{weekday}",
            tags=["Schedule"])
async def clear_day_schedule(device_id: str, weekday: str):
    """Remove all slots for a single weekday."""
    if weekday not in WEEKDAYS:
        raise HTTPException(status_code=400, detail=f"weekday must be one of {WEEKDAYS}")
    if device_id in schedules and weekday in schedules[device_id]:
        schedules[device_id][weekday] = []
    return {"status": "ok", "device": device_id, "weekday": weekday, "slots": 0}

@app.post("/api/devices/{device_id}/schedule/{weekday}/copy",
          tags=["Schedule"])
async def copy_day_schedule(device_id: str, weekday: str, target_days: List[str]):
    """Copy a day's schedule to one or more other days."""
    if weekday not in WEEKDAYS:
        raise HTTPException(status_code=400, detail="Invalid source weekday")
    for t in target_days:
        if t not in WEEKDAYS:
            raise HTTPException(status_code=400, detail=f"Invalid target day: {t}")
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    source = schedules.get(device_id, {}).get(weekday, [])
    if device_id not in schedules:
        schedules[device_id] = {}
    for t in target_days:
        schedules[device_id][t] = list(source)
    logger.info("Copied schedule %s/%s → %s", device_id, weekday, target_days)
    return {"status": "ok", "source": weekday, "copied_to": target_days}

# ---------------------------------------------------------------------------
# REST – Smart Mode status
# ---------------------------------------------------------------------------
@app.get("/api/devices/{device_id}/smart", tags=["Smart Mode"])
async def get_smart_status(device_id: str):
    """
    Return Smart-Mode sensor readings and the current automatic decision
    (which sub-mode is active and why).
    """
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    d = devices[device_id]
    temp_in  = d.get("temperature")
    temp_out = d.get("outsideTemperature")
    hum_in   = d.get("humidity")
    hum_out  = d.get("outsideHumidity")
    voc      = d.get("airQuality")
    dark     = d.get("isDark", False)

    # Determine active sub-mode
    active_mode  = "HRV_MEDIUM"
    active_reason = "Default ventilation"

    free_cooling = (
        temp_in  is not None and temp_in  > 24 and
        temp_out is not None and temp_out > 20 and
        temp_out < temp_in
    )
    if free_cooling:
        active_mode   = "FREE_COOLING"
        active_reason = "T_in>24°C & T_out>20°C & T_out<T_in – Sommermodus"
    elif dark:
        active_mode   = "NIGHT"
        active_reason = "Dunkelheit erkannt – Nachtmodus"
    elif voc is not None and temp_out is not None:
        if voc > 600:
            active_mode   = "HRV_MEDIUM"
            active_reason = "Luftqualität innen schlechter als außen – mittlere Geschwindigkeit"
        else:
            active_mode   = "HRV_MIN"
            active_reason = "Luftqualität innen besser als außen – Minimalgeschwindigkeit"

    return {
        "deviceId": device_id,
        "sensors": {
            "temperature_in":  temp_in,
            "temperature_out": temp_out,
            "humidity_in":     hum_in,
            "humidity_out":    hum_out,
            "airQuality_voc":  voc,
            "isDark":          dark,
        },
        "activeMode":   active_mode,
        "activeReason": active_reason,
        "conditions": {
            "freeCoolingActive": free_cooling,
            "nightModeActive":   dark,
            "airQualityBad":     (voc or 0) > 600,
        },
        "logic": [
            {"condition": "Luftqualität innen schlechter als außen (Tag)",
             "result": "HRV mittlere Geschwindigkeit"},
            {"condition": "Luftqualität innen besser als außen (Tag)",
             "result": "HRV Minimalgeschwindigkeit"},
            {"condition": "Dunkelheit im Raum",
             "result": "Nachtmodus automatisch"},
            {"condition": "T_in>24°C & T_out>20°C & T_out<T_in",
             "result": "Free-Cooling (Sommermodus, kein WRG)"},
            {"condition": "Luftfeuchtigkeit-Vergleich innen/außen",
             "result": "Analoger Vergleich wie Luftqualität"},
        ],
    }

# ---------------------------------------------------------------------------
# REST – NeuraCell-X (patent-pending radon protection + dew-point control)
# ---------------------------------------------------------------------------
# The heavy lifting runs in the MQTT bridge (radon meter -> all devices to
# Intake/Low on alarm; dew-point control with radon priority). Here we only
# surface the live status and expose a manual override / self-test, so the PWA
# can show and drive NeuraCell-X directly.
#
# Bridge input topics (retained):
#   ambientika/radon/alarm   ON|OFF  -> force / clear radon protection
#   ambientika/dewpoint/block ON|OFF -> force / release dew-point ventilation block
RADON_ALARM_TOPIC    = "ambientika/radon/alarm"
DEWPOINT_BLOCK_TOPIC = "ambientika/dewpoint/block"


class NeuraRadonCommand(BaseModel):
    active: bool   # True = force radon protection, False = clear


class NeuraDewpointCommand(BaseModel):
    block: bool    # True = block ventilation, False = release


@app.get("/api/neuracell", tags=["NeuraCell-X"])
async def get_neuracell():
    """Return the live NeuraCell-X status (radon protection + dew-point control)."""
    return neuracell_state


@app.post("/api/neuracell/radon", tags=["NeuraCell-X"])
async def set_neuracell_radon(cmd: NeuraRadonCommand):
    """Manually force or clear NeuraCell-X radon protection (self-test / override)."""
    val = "ON" if cmd.active else "OFF"
    mqtt_client.publish(RADON_ALARM_TOPIC, val, qos=1, retain=True)
    logger.info("NeuraCell-X radon override -> %s", val)
    return {"status": "ok", "topic": RADON_ALARM_TOPIC, "value": val}


@app.post("/api/neuracell/dewpoint", tags=["NeuraCell-X"])
async def set_neuracell_dewpoint(cmd: NeuraDewpointCommand):
    """Manually block or release ventilation via NeuraCell-X dew-point control."""
    val = "ON" if cmd.block else "OFF"
    mqtt_client.publish(DEWPOINT_BLOCK_TOPIC, val, qos=1, retain=True)
    logger.info("NeuraCell-X dew-point override -> %s", val)
    return {"status": "ok", "topic": DEWPOINT_BLOCK_TOPIC, "value": val}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["System"])
async def health():
    return {
        "status":  "ok",
        "mqtt":    mqtt_client.is_connected(),
        "devices": len(devices),
        "version": "2.0.0",
    }

# ---------------------------------------------------------------------------
# WebSocket – real-time push to PWA
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    for did, state in devices.items():
        await ws.send_json({"event": "status", "deviceId": did, "data": state})
    if neuracell_state:
        await ws.send_json({"event": "neuracell", "data": neuracell_state})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_clients.remove(ws)

# ---------------------------------------------------------------------------
# Serve PWA
# ---------------------------------------------------------------------------
import pathlib
pwa_path = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if pwa_path.exists():
    app.mount("/", StaticFiles(directory=str(pwa_path), html=True), name="pwa")
