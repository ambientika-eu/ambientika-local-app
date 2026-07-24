🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · **DA** · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Lokal app til Ambientika ventilationsenheder – styring i hjemmenettet, valgfrit 100% cloudfri.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Hvad er dette?

En fuldstændig **lokal** web-app (PWA) til styring af Ambientika ventilationsenheder –  
styringen kører fuldstændigt lokalt i hjemmenettet, uden videregivelse af data til tredjeparter. Enhedstilslutningen fås i to **driftstilstande** (se nedenfor) — inklusive en **100% cloudfri** variant uden cloud-server og uden permanent internetforbindelse.

Appen kører i hjemmenetværket på en Raspberry Pi, NAS eller enhver Linux-server  
og er tilgængelig via browser (mobil og PC) – også som en installerbar app.

---

## 🔀 Driftstilstande

Appen og styringen kører altid lokalt i hjemmenettet. For **enhedstilslutningen** findes der to tilstande:

- **Standard – Cloud-Bridge:** `docker-compose.yml` starter `ambientika-mqtt-bridge`, som forespørger Ambientika-cloud'en og stiller dataene til rådighed lokalt via MQTT. Kræver et engangs cloud-login (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) og internet.
- **100% cloudfri – lokal bridge:** `docker-compose.local.yml` kommunikerer med enhederne via den lokale TCP-bridge (`ambientika_local_bridge.py`, port 11000) direkte i LAN'et — **uden cloud-server og uden permanent internetforbindelse**. Opsætning: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.da.md) og [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.da.md).

Dugpunkts-/fugtbeskyttelsesfunktionen kører under alle omstændigheder autonomt i enheden og er uafhængig af begge dele.

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

## ⚡ Quick Start (Docker Compose)

> **Standardtilstand (Cloud-Bridge).** For 100% cloudfri drift se **Driftstilstande** ovenfor samt `README_LOCAL_CLOUDLESS.md` og `CLOUD-INTEGRATION.md`.

### 1. Klon repositoryet

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Konfiguration

```bash
cp .env.example .env
```

Rediger derefter `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Start

```bash
docker compose up -d
```

### 4. Åbn appen

Browser: **http://DEINE-IP:8080**

> På mobilen: Føj til startskærm → som en native app!

---

## 📱 Funktioner

| Funktion | Status |
|---------|--------|
| Alle enheder på ét blik | ✅ |
| Skift tilstand (HRV / Nat / Boost / Eco / Fra) | ✅ |
| Styr ventilatorhastighed | ✅ |
| Temperatur / fugt / CO₂ i realtid | ✅ |
| Filteralarm-visning | ✅ |
| **NeuraCell-X® – Radonbeskyttelse (patentanmeldt)** | ✅ |
| **NeuraCell-X® – Dugpunktsstyring** | ✅ |
| PWA – installerbar på mobil og PC | ✅ |
| Offline-tilstand (Service Worker) | ✅ |
| WebSocket live-opdateringer | ✅ |
| 100% lokal – ingen cloud-server | ✅ |
| GDPR-kompatibel | ✅ |

---

## 🛡️ NeuraCell-X® (patentanmeldt)

Appen bringer **NeuraCell-X®** – Ambientikas patentanmeldte beskyttelsesteknologi –
direkte til sin egen fane, fuldstændigt lokalt:

- ☢️ **Radonbeskyttelse (højeste prioritet).** Ved radonalarm skifter NeuraCell-X *alle*
  enheder til **indblæsningstilstand (trin 1)** og skaber et let overtryk mod
  indtrængende radon. Live-visning af radonværdi (Bq/m³) og tærskel.
- 💧 **Dugpunktsstyring.** Hvis ventilation ville øge rumfugtigheden, slår NeuraCell-X
  enhederne **fra**; ved gode forhold frigives de igen. Visning af
  dugpunkt inde/ude. **Radonbeskyttelse har altid forrang.**
- 🔄 **Nøjagtig gendannelse.** Når alle beskyttelsesfunktioner er inaktive, gendannes den forrige
  aktive tilstand nøjagtigt for hver enhed.
- 🧪 **Selvtest / manuel tilsidesættelse** direkte fra appen med et tryk på en knap.

> Selve logikken kører i [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (statusemne `ambientika/neuracell/state`). Appen viser live-status og kan
> udløse radonbeskyttelse hhv. dugpunktsspærring via `ambientika/radon/alarm` og
> `ambientika/dewpoint/block`.

**Nye API-endepunkter:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

API'et er tilgængeligt på `http://DEINE-IP:8080/api`.  
Interaktiv dokumentation: **http://DEINE-IP:8080/docs**

| Endpoint | Metode | Beskrivelse |
|----------|---------|--------------|
| `/api/devices` | GET | Alle enheder med status |
| `/api/devices/{id}` | GET | Enkelt enhed |
| `/api/devices/{id}/command` | POST | Send kommando |
| `/api/health` | GET | Systemstatus |
| `/ws` | WebSocket | Live-opdateringer |

### Eksempel: Skift tilstand

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Eksempel: Ventilator til 60%

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

## 🔧 Manuel installation (uden Docker)

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

## 📄 Licens

MIT License – © Ambientika / SUEDWIND
