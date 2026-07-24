🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · **NL** · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Lokale app voor Ambientika-ventilatietoestellen – besturing in het thuisnetwerk, desgewenst 100% cloudvrij.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Wat is dit?

Een volledig **lokale** web-app (PWA) voor de besturing van Ambientika-ventilatietoestellen –  
de besturing draait volledig lokaal in het thuisnetwerk, zonder gegevens door te geven aan derden. De apparaatkoppeling is er in twee **bedrijfsmodi** (zie hieronder) — inclusief een **100% cloudvrije** variant zonder cloudserver en zonder permanente internetverbinding.

De app draait in het thuisnetwerk op een Raspberry Pi, NAS of elke Linux-server  
en is via de browser (mobiel & pc) bereikbaar – ook als installeerbare app.

---

## 🔀 Bedrijfsmodi

De app en de besturing draaien altijd lokaal in het thuisnetwerk. Voor de **apparaatkoppeling** zijn er twee modi:

- **Standaard – Cloud-Bridge:** `docker-compose.yml` start de `ambientika-mqtt-bridge`, die de Ambientika-cloud bevraagt en de gegevens lokaal via MQTT beschikbaar stelt. Vereist een eenmalige cloud-aanmelding (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) en internet.
- **100% cloudvrij – lokale bridge:** `docker-compose.local.yml` spreekt de apparaten via de lokale TCP-bridge (`ambientika_local_bridge.py`, poort 11000) rechtstreeks in het LAN aan — **zonder cloudserver en zonder permanente internetverbinding**. Installatie: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.nl.md) en [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.nl.md).

De dauwpunt-/vochtbeschermingsfunctie draait sowieso autonoom in het apparaat en is van beide onafhankelijk.

## 🏗️ Architectuur

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

> **Standaardmodus (Cloud-Bridge).** Voor 100% cloudvrij gebruik zie **Bedrijfsmodi** hierboven evenals `README_LOCAL_CLOUDLESS.md` en `CLOUD-INTEGRATION.md`.

### 1. Repository klonen

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configuratie

```bash
cp .env.example .env
```

Bewerk vervolgens `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Starten

```bash
docker compose up -d
```

### 4. App openen

Browser: **http://DEINE-IP:8080**

> Op mobiel: Toevoegen aan het startscherm → net als een native app!

---

## 📱 Features

| Feature | Status |
|---------|--------|
| Alle apparaten in één oogopslag | ✅ |
| Modus wisselen (HRV / Nacht / Boost / Eco / Uit) | ✅ |
| Ventilatorsnelheid regelen | ✅ |
| Temperatuur / vocht / CO₂ realtime | ✅ |
| Filteralarm-weergave | ✅ |
| **NeuraCell-X® – Radonbescherming (patent aangevraagd)** | ✅ |
| **NeuraCell-X® – Dauwpuntregeling** | ✅ |
| PWA – installeerbaar op mobiel & pc | ✅ |
| Offline-modus (Service Worker) | ✅ |
| WebSocket live-updates | ✅ |
| 100% lokaal – geen cloudserver | ✅ |
| AVG-conform | ✅ |

---

## 🛡️ NeuraCell-X® (patent aangevraagd)

De app brengt **NeuraCell-X®** – de patent-aangevraagde beschermingstechnologie van Ambientika –
rechtstreeks op een eigen tab, volledig lokaal:

- ☢️ **Radonbescherming (hoogste prioriteit).** Bij een radonalarm schakelt NeuraCell-X *alle*
  apparaten in de **toevoerluchtmodus (stand 1)** en creëert een lichte overdruk tegen
  binnendringend radon. Live-weergave van radonwaarde (Bq/m³) en drempel.
- 💧 **Dauwpuntregeling.** Zou ventileren de ruimtevochtigheid verhogen, dan schakelt NeuraCell-X
  de apparaten **uit**; bij goede omstandigheden wordt weer vrijgegeven. Weergave van
  dauwpunt binnen/buiten. **Radonbescherming heeft altijd voorrang.**
- 🔄 **Exact herstel.** Zijn alle beschermingsfuncties inactief, dan wordt de eerder
  actieve modus per apparaat exact hersteld.
- 🧪 **Zelftest / handmatige override** rechtstreeks vanuit de app met één druk op de knop.

> De eigenlijke logica draait in de [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (statustopic `ambientika/neuracell/state`). De app toont de live-status en kan
> radonbescherming resp. dauwpuntblokkering via `ambientika/radon/alarm` en
> `ambientika/dewpoint/block` activeren.

**Nieuwe API-endpoints:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

De API is bereikbaar onder `http://DEINE-IP:8080/api`.  
Interactieve documentatie: **http://DEINE-IP:8080/docs**

| Endpoint | Methode | Beschrijving |
|----------|---------|--------------|
| `/api/devices` | GET | Alle apparaten met status |
| `/api/devices/{id}` | GET | Eén enkel apparaat |
| `/api/devices/{id}/command` | POST | Commando verzenden |
| `/api/health` | GET | Systeemstatus |
| `/ws` | WebSocket | Live-updates |

### Voorbeeld: modus wisselen

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Voorbeeld: ventilator op 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Projectstructuur

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

## 🔧 Handmatige installatie (zonder Docker)

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

## 📄 Licentie

MIT License – © Ambientika / SUEDWIND
