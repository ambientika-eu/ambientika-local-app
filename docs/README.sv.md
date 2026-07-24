🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · **SV** · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Lokal app för Ambientika ventilationsenheter – styrning i hemnätverket, valfritt 100% molnfri.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Vad är det här?

En helt **lokal** webbapp (PWA) för styrning av Ambientika ventilationsenheter –  
styrningen körs helt lokalt i hemnätverket, utan att data delas med tredje part. Enhetsanslutningen finns i två **driftlägen** (se nedan) — inklusive en **100% molnfri** variant utan molnserver och utan permanent internetanslutning.

Appen körs i hemnätverket på en Raspberry Pi, NAS eller valfri Linux-server  
och är åtkomlig via webbläsare (mobil och PC) – även som installerbar app.

---

## 🔀 Driftlägen

Appen och styrningen körs alltid lokalt i hemnätverket. För **enhetsanslutningen** finns det två lägen:

- **Standard – molnbrygga:** `docker-compose.yml` startar `ambientika-mqtt-bridge`, som frågar av Ambientika-molnet och tillhandahåller data lokalt via MQTT. Kräver en engångsinloggning i molnet (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) och internet.
- **100% molnfri – lokal brygga:** `docker-compose.local.yml` kommunicerar med enheterna via den lokala TCP-bryggan (`ambientika_local_bridge.py`, port 11000) direkt i LAN:et — **utan molnserver och utan permanent internetanslutning**. Konfiguration: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.sv.md) och [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.sv.md).

Daggpunkts-/fuktskyddsfunktionen körs ändå autonomt i enheten och är oberoende av båda.

## 🏗️ Arkitektur

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

## ⚡ Snabbstart (Docker Compose)

> **Standardläge (molnbrygga).** För 100% molnfri drift, se **Driftlägen** ovan samt `README_LOCAL_CLOUDLESS.md` och `CLOUD-INTEGRATION.md`.

### 1. Klona repositoryt

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Konfiguration

```bash
cp .env.example .env
```

Redigera sedan `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Starta

```bash
docker compose up -d
```

### 4. Öppna appen

Webbläsare: **http://DEINE-IP:8080**

> På mobilen: Lägg till på hemskärmen → precis som en inbyggd app!

---

## 📱 Funktioner

| Funktion | Status |
|---------|--------|
| Alla enheter i överblick | ✅ |
| Byt läge (HRV / Natt / Boost / Eco / Av) | ✅ |
| Styr fläkthastighet | ✅ |
| Temperatur / fukt / CO₂ i realtid | ✅ |
| Visning av filterlarm | ✅ |
| **NeuraCell-X® – Radonskydd (patentsökt)** | ✅ |
| **NeuraCell-X® – Daggpunktsstyrning** | ✅ |
| PWA – installerbar på mobil och PC | ✅ |
| Offline-läge (Service Worker) | ✅ |
| WebSocket-liveuppdateringar | ✅ |
| 100% lokal – ingen molnserver | ✅ |
| GDPR-kompatibel | ✅ |

---

## 🛡️ NeuraCell-X® (patentsökt)

Appen ger dig **NeuraCell-X®** – Ambientikas patentsökta skyddsteknik –
direkt på en egen flik, helt lokalt:

- ☢️ **Radonskydd (högsta prioritet).** Vid radonlarm växlar NeuraCell-X *alla*
  enheter till **tilluftsläge (steg 1)** och skapar ett lätt övertryck mot
  inträngande radon. Livevisning av radonvärde (Bq/m³) och tröskel.
- 💧 **Daggpunktsstyrning.** Om ventilation skulle öka inomhusfukten stänger NeuraCell-X
  **av** enheterna; vid goda förhållanden friges de igen. Visning av
  daggpunkt inne/ute. **Radonskydd har alltid företräde.**
- 🔄 **Exakt återställning.** När alla skyddsfunktioner är inaktiva återställs det
  tidigare aktiva läget exakt för varje enhet.
- 🧪 **Självtest / manuell åsidosättning** direkt från appen med ett knapptryck.

> Själva logiken körs i [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (statusämnet `ambientika/neuracell/state`). Appen visar livestatusen och kan
> utlösa radonskydd respektive daggpunktsspärr via `ambientika/radon/alarm` och
> `ambientika/dewpoint/block`.

**Nya API-endpunkter:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

API:et nås på `http://DEINE-IP:8080/api`.  
Interaktiv dokumentation: **http://DEINE-IP:8080/docs**

| Endpoint | Metod | Beskrivning |
|----------|---------|--------------|
| `/api/devices` | GET | Alla enheter med status |
| `/api/devices/{id}` | GET | Enskild enhet |
| `/api/devices/{id}/command` | POST | Skicka kommando |
| `/api/health` | GET | Systemstatus |
| `/ws` | WebSocket | Liveuppdateringar |

### Exempel: Byt läge

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Exempel: Fläkt till 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Projektstruktur

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

## 🔧 Manuell installation (utan Docker)

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

## 🌍 Länkar

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licens

MIT License – © Ambientika / SUEDWIND
