# Ambientika Local App 🏠

> **Lokale App für Ambientika Lüftungsgeräte – Steuerung im Heimnetz, wahlweise 100% cloudfrei.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Was ist das?

Eine vollständig **lokale** Web-App (PWA) zur Steuerung von Ambientika Lüftungsgeräten –  
die Steuerung läuft vollständig lokal im Heimnetz, ohne Datenweitergabe an Dritte. Die Geräteanbindung gibt es in zwei **Betriebsmodi** (siehe unten) — inklusive einer **100% cloudfreien** Variante ohne Cloud-Server und ohne dauerhafte Internetverbindung.

Die App läuft im Heimnetzwerk auf einem Raspberry Pi, NAS oder jedem Linux-Server  
und ist per Browser (Handy & PC) erreichbar – auch als installierbare App.

---

## 🔀 Betriebsmodi

Die App und die Steuerung laufen immer lokal im Heimnetz. Für die **Geräteanbindung** gibt es zwei Modi:

- **Standard – Cloud-Bridge:** `docker-compose.yml` startet die `ambientika-mqtt-bridge`, die die Ambientika-Cloud abfragt und die Daten lokal per MQTT bereitstellt. Benötigt eine einmalige Cloud-Anmeldung (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) und Internet.
- **100% cloudfrei – lokale Bridge:** `docker-compose.local.yml` spricht die Geräte über die lokale TCP-Bridge (`ambientika_local_bridge.py`, Port 11000) direkt im LAN an — **ohne Cloud-Server und ohne dauerhafte Internetverbindung**. Einrichtung: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.md) und [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.md).

Die Taupunkt-/Feuchteschutz-Funktion läuft ohnehin autonom im Gerät und ist von beidem unabhängig.

## 🏗️ Architektur

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

> **Standard-Modus (Cloud-Bridge).** Für den 100% cloudfreien Betrieb siehe **Betriebsmodi** oben sowie `README_LOCAL_CLOUDLESS.md` und `CLOUD-INTEGRATION.md`.

### 1. Repository klonen

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Konfiguration

```bash
cp .env.example .env
```

Dann `.env` bearbeiten:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Starten

```bash
docker compose up -d
```

### 4. App öffnen

Browser: **http://DEINE-IP:8080**

> Auf dem Handy: Zum Startbildschirm hinzufügen → wie eine native App!

---

## 📱 Features

| Feature | Status |
|---------|--------|
| Alle Geräte auf einen Blick | ✅ |
| Modus wechseln (HRV / Nacht / Boost / Eco / Aus) | ✅ |
| Lüftergeschwindigkeit steuern | ✅ |
| Temperatur / Feuchte / CO₂ Echtzeit | ✅ |
| Filter-Alarm Anzeige | ✅ |
| **NeuraCell-X® – Radon-Schutz (Patent angemeldet)** | ✅ |
| **NeuraCell-X® – Taupunktsteuerung** | ✅ |
| PWA – installierbar auf Handy & PC | ✅ |
| Offline-Modus (Service Worker) | ✅ |
| WebSocket Live-Updates | ✅ |
| 100% lokal – kein Cloud-Server | ✅ |
| DSGVO-konform | ✅ |

---

## 🛡️ NeuraCell-X® (zum Patent angemeldet)

Die App bringt **NeuraCell-X®** – die zum Patent angemeldete Schutztechnologie von Ambientika –
direkt auf einen eigenen Tab, vollständig lokal:

- ☢️ **Radon-Schutz (höchste Priorität).** Bei Radon-Alarm schaltet NeuraCell-X *alle*
  Geräte in den **Zuluftmodus (Stufe 1)** und erzeugt einen leichten Überdruck gegen
  eindringendes Radon. Live-Anzeige von Radonwert (Bq/m³) und Schwelle.
- 💧 **Taupunktsteuerung.** Würde Lüften die Raumfeuchte erhöhen, schaltet NeuraCell-X
  die Geräte **aus**; bei guten Bedingungen wird wieder freigegeben. Anzeige von
  Taupunkt innen/außen. **Radon-Schutz hat immer Vorrang.**
- 🔄 **Exakte Wiederherstellung.** Sind alle Schutzfunktionen inaktiv, wird der zuvor
  aktive Modus je Gerät exakt wiederhergestellt.
- 🧪 **Selbsttest / manuelle Übersteuerung** direkt aus der App per Knopfdruck.

> Die eigentliche Logik läuft in der [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (Statusthema `ambientika/neuracell/state`). Die App zeigt den Live-Status und kann
> Radon-Schutz bzw. Taupunkt-Sperre über `ambientika/radon/alarm` und
> `ambientika/dewpoint/block` auslösen.

**Neue API-Endpunkte:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

Die API ist unter `http://DEINE-IP:8080/api` erreichbar.  
Interaktive Dokumentation: **http://DEINE-IP:8080/docs**

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/devices` | GET | Alle Geräte mit Status |
| `/api/devices/{id}` | GET | Einzelnes Gerät |
| `/api/devices/{id}/command` | POST | Befehl senden |
| `/api/health` | GET | System-Status |
| `/ws` | WebSocket | Live-Updates |

### Beispiel: Modus wechseln

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Beispiel: Lüfter auf 60%

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

## 🔧 Manuelle Installation (ohne Docker)

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

## 📄 Lizenz

MIT License – © Ambientika / SUEDWIND
