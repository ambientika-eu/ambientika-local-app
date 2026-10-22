🌐 [DE](../README.md) · **EN** · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Local app for Ambientika ventilation units – control within your home network, optionally 100% cloud-free.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 What is this?

A fully **local** web app (PWA) for controlling Ambientika ventilation units –  
control runs entirely locally within your home network, with no data shared with third parties. Device connectivity comes in two **operating modes** (see below) — including a **100% cloud-free** variant with no cloud server and no permanent internet connection.

The app runs in your home network on a Raspberry Pi, NAS, or any Linux server  
and is accessible via browser (phone & PC) – also as an installable app.

---

## 🔀 Operating modes

The app and control always run locally within your home network. For **device connectivity** there are two modes:

- **Standard – Cloud bridge:** `docker-compose.yml` starts the `ambientika-mqtt-bridge`, which queries the Ambientika cloud and provides the data locally via MQTT. Requires a one-time cloud sign-in (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) and internet.
- **100% cloud-free – local bridge:** `docker-compose.local.yml` talks to the units over the local TCP bridge (`ambientika_local_bridge.py`, port 11000) directly in the LAN — **without a cloud server and without a permanent internet connection**. Setup: [`README_LOCAL_CLOUDLESS.md`](../README_LOCAL_CLOUDLESS.md) and [`CLOUD-INTEGRATION.md`](../CLOUD-INTEGRATION.md).

The dew-point / anti-condensation protection function runs autonomously in the device anyway and is independent of both.

## 🏗️ Architecture

```
Ambientika Gerät (WiFi)
        │
        ▼
[Ambientika MQTT Bridge]   ← pollt Ambientika API, veröffentlicht lokal via MQTT
        │
        ▼
[Mosquitto MQTT Broker]    ← lokaler Message Bus (Docker)
        │
        ▼
[FastAPI Backend]          ← REST API + WebSocket (Port 8080)
        │
        ▼
[PWA Frontend]             ← Browser-App (Handy & PC)
```

---

## ⚡ Quick Start (Docker Compose)

> **Standard mode (cloud bridge).** For 100% cloud-free operation see **Operating modes** above as well as `README_LOCAL_CLOUDLESS.md` and `CLOUD-INTEGRATION.md`.

### 1. Clone the repository

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configuration

```bash
cp .env.example .env
```

Then edit `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Start

```bash
docker compose up -d
```

### 4. Open the app

Browser: **http://DEINE-IP:8080**

> On your phone: Add to home screen → just like a native app!

---

## 📱 Features

| Feature | Status |
|---------|--------|
| All devices at a glance | ✅ |
| Switch mode (HRV / Night / Boost / Eco / Off) | ✅ |
| Control fan speed | ✅ |
| Temperature / humidity / CO₂ in real time | ✅ |
| Filter alarm display | ✅ |
| **NeuraCell-X® – radon protection (patent pending)** | ✅ |
| **NeuraCell-X® – dew-point control** | ✅ |
| **Free-cooling guard – minimum margin, hold time, sensor at the house, log** | ✅ |
| PWA – installable on phone & PC | ✅ |
| Offline mode (Service Worker) | ✅ |
| WebSocket live updates | ✅ |
| 100% local – no cloud server | ✅ |
| GDPR-compliant | ✅ |

---

## 🛡️ NeuraCell-X® (patent pending)

The app brings **NeuraCell-X®** – Ambientika's patent-pending protection technology –
directly onto its own tab, fully locally:

- ☢️ **Radon protection (highest priority).** On a radon alarm, NeuraCell-X switches *all*
  devices into **intake mode (level 1)** and creates a slight overpressure against
  intruding radon. Live display of the radon value (Bq/m³) and threshold.
- 💧 **Dew-point control.** If ventilating would increase indoor humidity, NeuraCell-X switches
  the devices **off**; under good conditions it releases them again. Displays the
  dew point indoors/outdoors. **Radon protection always takes precedence.**
- 🔄 **Exact restore.** Once all protection functions are inactive, the previously
  active mode is restored exactly for each device.
- 🧪 **Self-test / manual override** directly from the app at the push of a button.

> The actual logic runs in the [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (status topic `ambientika/neuracell/state`). The app shows the live status and can
> trigger radon protection or the dew-point block via `ambientika/radon/alarm` and
> `ambientika/dewpoint/block`.

**New API endpoints:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## ❄️ Free-cooling guard

The units' SMART mode compares the master's indoor reading with the outdoor value of a
**weather station nearby**. When the two are close, the station's deviation alone decides
whether free cooling starts – and the units may cool although it is warmer outside. The
guard replaces that decision locally, with an outdoor value from the house:

| | |
|---|---|
| **Start** | indoor > 24 °C **and** outdoor > 20 °C **and** indoor − outdoor ≥ **3.0 K** |
| **End** | indoor − outdoor < **1.0 K**, indoor ≤ 23.5 °C or outdoor ≤ 19.7 °C (20 °C − 0.3 K dead band) |
| **Immediately** | outdoor air not cooler than indoor, no valid outdoor value (older than 60 min), guard switched off |
| **Hold time** | at least **15 min** in the new state after every switch |

- At rest the masters run in **AUTO** (heat recovery). To cool, the guard sets
  **Slave→Master airflow** (level 2 by day, level 1 at night): the master extracts room
  air, so its sensor keeps measuring the room — in Master→Slave airflow it would sit in the
  incoming outdoor air and the indoor reading would drop to the outdoor value at once.
  Afterwards the guard restores the previous state **exactly**. While active it replaces
  SMART on the controlled units with the rest mode (can be disabled).
- **Robust in practice:** a start counts as confirmed only once the unit reports the
  cooling mode; a command that arrives late is adopted, an unconfirmed restore is retried
  (visible as "Restore not confirmed"). After a NeuraCell-X protection episode or a bridge
  outage a grace period applies, stale device data (> 10 min) or a bridge reporting offline
  block every decision, retained MQTT values are ignored.
- **Outdoor value** – a sensor at the house always wins, a weather station is only the
  fallback:
  - MQTT `ambientika/weather` → `{"temperature": 21.4, "humidity": 60}` (Shelly, ESPHome,
    Home Assistant …) or `ambientika/dewpoint/outdoor_temp` (plain number, the same topic as
    the NeuraCell-X computed dew point)
  - HTTP `POST /api/freecooling/outdoor` `{"temperature": 21.4, "humidity": 60, "kind": "sensor"}`
  - OpenWeatherMap polled by the backend (`OWM_API_KEY`, `OWM_LOCATION` in `.env`); the PWA
    reports its OpenWeatherMap reading as a fallback as well
- Off, Away, Surveillance and manually chosen airflow modes are **left alone**. If someone
  changes the mode while the guard is cooling, the guard backs off for a pause (60 min).
  **NeuraCell-X (radon/dew point) always has priority.**
- **Log** – every switch is recorded in SQLite (400 days) with indoor/outdoor value, source
  and age of the outdoor value, mode before/after and the reason, exportable as CSV. The
  system's behaviour is thereby verifiable.

Enable and tune it on the **❄️ Free Cooling** tab of the app (or `FREECOOLING_ENABLED=true`
in `.env`; values from the app win and survive a restart). By default the guard controls all
**masters** – the slaves of a zone follow their master; specific units can be selected explicitly. The night level applies in darkness (the unit's
dusk sensor) or during fixed night hours (`22:00-06:00`, time zone `TZ`).

**API:** `GET /api/freecooling` (status), `GET|PUT /api/freecooling/config`,
`GET /api/freecooling/log` and `/log.csv`, `POST /api/freecooling/outdoor`,
`POST /api/freecooling/evaluate`.

---

## 🔌 REST API

The API is accessible at `http://DEINE-IP:8080/api`.  
Interactive documentation: **http://DEINE-IP:8080/docs**

| Endpoint | Method | Description |
|----------|---------|--------------|
| `/api/devices` | GET | All devices with status |
| `/api/devices/{id}` | GET | Single device |
| `/api/devices/{id}/command` | POST | Send command |
| `/api/health` | GET | System status |
| `/ws` | WebSocket | Live updates |

### Example: Switch mode

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Example: Fan to 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Project structure

```
ambientika-local-app/
├── backend/
│   ├── main.py            # FastAPI Backend (REST + WebSocket)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html         # PWA (Single File – kein Build nötig!)
│   ├── manifest.json      # PWA Manifest
│   └── sw.js              # Service Worker (Offline-Support)
├── docker-compose.yml     # Vollständiges Stack-Setup
└── README.md
```

---

## 🔧 Manual installation (without Docker)

```bash
# MQTT Broker
sudo apt install mosquitto mosquitto-clients

# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080

# Frontend: index.html einfach im Browser öffnen oder via nginx/caddy servieren
```

---

## 🌍 Links

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 License

MIT License – © Ambientika / SUEDWIND
