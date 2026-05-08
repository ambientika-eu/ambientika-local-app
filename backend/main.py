#!/usr/bin/env python3
"""
Ambientika Local App – FastAPI Backend
======================================
Runs 100% locally – no SUEDWIND cloud server required.
Communicates with the Ambientika device via local RF/BLE gateway or
the existing MQTT bridge, and exposes a REST + WebSocket API for the PWA.

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
# Configuration (from environment variables with sensible defaults)
# ---------------------------------------------------------------------------
MQTT_BROKER   = os.getenv("MQTT_BROKER",   "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER",     "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_PREFIX   = os.getenv("MQTT_PREFIX",   "ambientika")
LOG_LEVEL     = os.getenv("LOG_LEVEL",     "INFO")

logging.basicConfig(level=getattr(logging, LOG_LEVEL))
logger = logging.getLogger("ambientika-local")

# ---------------------------------------------------------------------------
# In-memory device store  (device_id -> latest state dict)
# ---------------------------------------------------------------------------
devices: Dict[str, Dict[str, Any]] = {}
ws_clients: List[WebSocket] = []

# ---------------------------------------------------------------------------
# MQTT client
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
        topic_parts = msg.topic.split("/")
        if len(topic_parts) < 3:
            return
        device_id = topic_parts[1]
        kind      = topic_parts[2]
        payload   = json.loads(msg.payload.decode())

        if kind == "status":
            devices[device_id] = {**payload, "lastSeen": int(time.time())}
        elif kind == "availability":
            if device_id in devices:
                devices[device_id]["online"] = (payload == "online" or payload.get("state") == "online")

        # Push update to all connected WebSocket clients
        asyncio.run_coroutine_threadsafe(
            broadcast({"event": kind, "deviceId": device_id, "data": devices.get(device_id, {})}),
            loop
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
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class DeviceCommand(BaseModel):
    mode:     Optional[str] = None   # HRV | NIGHT | BOOST | ECO | OFF
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

# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/api/devices", response_model=List[DeviceInfo], tags=["Devices"])
async def list_devices():
    """Return all known Ambientika devices with their current state."""
    return [DeviceInfo(deviceId=did, **state) for did, state in devices.items()]

@app.get("/api/devices/{device_id}", response_model=DeviceInfo, tags=["Devices"])
async def get_device(device_id: str):
    """Return state of a single device."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceInfo(deviceId=device_id, **devices[device_id])

@app.post("/api/devices/{device_id}/command", tags=["Devices"])
async def send_command(device_id: str, cmd: DeviceCommand):
    """Send a mode / fanSpeed command to a device via MQTT."""
    if device_id not in devices:
        raise HTTPException(status_code=404, detail="Device not found")
    payload = cmd.dict(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="At least one of mode or fanSpeed required")
    topic = f"{MQTT_PREFIX}/{device_id}/set"
    mqtt_client.publish(topic, json.dumps(payload), qos=1)
    logger.info("Command sent to %s: %s", device_id, payload)
    return {"status": "ok", "topic": topic, "payload": payload}

@app.get("/api/health", tags=["System"])
async def health():
    """Simple health check."""
    return {
        "status": "ok",
        "mqtt": mqtt_client.is_connected(),
        "devices": len(devices),
        "version": "1.0.0",
    }

# ---------------------------------------------------------------------------
# WebSocket endpoint – real-time push to PWA
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    # Send current snapshot immediately on connect
    for did, state in devices.items():
        await ws.send_json({"event": "status", "deviceId": did, "data": state})
    try:
        while True:
            await ws.receive_text()  # keep-alive / ping
    except WebSocketDisconnect:
        ws_clients.remove(ws)

# ---------------------------------------------------------------------------
# Serve the PWA from /frontend/dist (static files)
# ---------------------------------------------------------------------------
import pathlib
pwa_path = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if pwa_path.exists():
    app.mount("/", StaticFiles(directory=str(pwa_path), html=True), name="pwa")
