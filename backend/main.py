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
from pydantic import BaseModel

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

# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
mqtt_client = mqtt.Client(client_id="ambientika-local-app")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("MQTT connected – subscribing to %s/+/status", MQTT_PREFIX)
        client.subscribe(f"{MQTT_PREFIX}/+/status")
        client.subscribe(f"{MQTT_PREFIX}/+/availability")
    else:
        logger.warning("MQTT connection failed rc=%s", rc)

def on_message(client, userdata, msg):
    try:
        parts = msg.topic.split("/")
        if len(parts) < 3:
            return
        device_id, kind = parts[1], parts[2]
        payload = json.loads(msg.payload.decode())
        if kind == "status":
            devices[device_id] = {**payload, "lastSeen": int(time.time())}
        elif kind == "availability":
            if device_id in devices:
                devices[device_id]["online"] = (
                    payload == "online" or payload.get("state") == "online"
                )
        asyncio.run_coroutine_threadsafe(
            broadcast({"event": kind, "deviceId": device_id,
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
    yield
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
class DeviceCommand(BaseModel):
    mode:     Optional[str] = None   # HRV | NIGHT | BOOST | ECO | SMART | OFF
    fanSpeed: Optional[int] = None   # 0-100

class DeviceInfo(BaseModel):
    deviceId:    str
    name:        Optional[str]   = None
    mode:        Optional[str]   = None
    fanSpeed:    Optional[int]   = None
    temperature: Optional[float] = None
    humidity:    Optional[int]   = None
    airQuality:  Optional[int]   = None
    filterAlarm: Optional[bool]  = None
    online:      Optional[bool]  = None
    lastSeen:    Optional[int]   = None

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
    return [DeviceInfo(deviceId=did, **state) for did, state in devices.items()]

@app.get("/api/devices/{device_id}", response_model=DeviceInfo, tags=["Devices"])
async def get_device(device_id: str):
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceInfo(deviceId=device_id, **devices[device_id])

@app.post("/api/devices/{device_id}/command", tags=["Devices"])
async def send_command(device_id: str, cmd: DeviceCommand):
    """Send mode / fanSpeed command to a device via MQTT."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    payload = cmd.dict(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="Provide mode or fanSpeed")
    topic = f"{MQTT_PREFIX}/{device_id}/set"
    mqtt_client.publish(topic, json.dumps(payload), qos=1)
    logger.info("Command → %s: %s", device_id, payload)
    return {"status": "ok", "topic": topic, "payload": payload}

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
