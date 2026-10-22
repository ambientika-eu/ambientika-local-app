🌐 **DE** · [EN](docs/README.en.md) · [IT](docs/README.it.md) · [FR](docs/README.fr.md) · [ES](docs/README.es.md) · [NL](docs/README.nl.md) · [PL](docs/README.pl.md) · [PT](docs/README.pt.md) · [SV](docs/README.sv.md) · [DA](docs/README.da.md) · [CS](docs/README.cs.md)

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
- **Lokal – ohne Server:** `docker-compose.local.yml` spricht die Geräte über die lokale TCP-Bridge (`ambientika_local_bridge.py`, Port 11000) direkt im LAN an — **ohne Cloud-Server und ohne dauerhafte Internetverbindung**. Einrichtung: [`README_LOCAL_CLOUDLESS.md`](docs/README_LOCAL_CLOUDLESS.de.md) und [`CLOUD-INTEGRATION.md`](docs/CLOUD-INTEGRATION.de.md).

> Der Standardmodus ist im Feld bewährt und für den Regelbetrieb vorgesehen. Der lokale Modus ist die **Rückfallebene**: Er hält die Anlage bedienbar, wenn der Ambientika-Server nicht erreichbar ist, und deckt Objekte ohne dauerhafte Internetanbindung ab. Er ersetzt den Standardmodus nicht.

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
| **Free-Cooling-Wächter – Mindestabstand, Haltezeit, Sensor am Haus, Protokoll** | ✅ |
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

## ❄️ Free-Cooling-Wächter

Der SMART-Modus der Geräte vergleicht den Innenwert des Masters mit dem Außenwert
einer **Wetterstation in der Umgebung**. Liegen beide nah beieinander, entscheidet
allein die Abweichung der Station darüber, ob Free Cooling anläuft – und dann wird
unter Umständen gekühlt, obwohl es draußen wärmer ist. Der Wächter ersetzt diese
Entscheidung lokal, mit einem Außenwert vom Haus:

| | |
|---|---|
| **Start** | innen > 24 °C **und** außen > 20 °C **und** innen − außen ≥ **3,0 K** |
| **Ende** | innen − außen < **1,0 K**, innen ≤ 23,5 °C oder außen ≤ 19,7 °C (20 °C − 0,3 K Totband) |
| **Sofort** | Außenluft nicht kühler als innen, kein gültiger Außenwert (älter als 60 min), Wächter aus |
| **Haltezeit** | nach jedem Umschalten mindestens **15 min** im neuen Zustand |

- Die Master laufen im Ruhezustand in **AUTO** (Wärmerückgewinnung). Zum Kühlen setzt
  der Wächter den **Luftstrom Master→Slave** (tagsüber Stufe 2, nachts Stufe 1), wie es
  auch die SMART-Firmware beim Free Cooling tut; der Innensensor sitzt unten im Gehäuse und
  erfasst die Raumtemperatur, der gemittelte Wert der Bridge und die Hysterese am Ende
  fangen den Einfluss des Luftstroms ab (Slave→Master ist als `cool_mode` wählbar).
  Danach stellt der Wächter **exakt zurück**. Solange er aktiv ist, ersetzt er SMART auf
  den gesteuerten Geräten durch den Ruhemodus (abschaltbar).
- **Robust gegen die Praxis:** Ein Start gilt erst als bestätigt, wenn das Gerät den
  Kühlmodus meldet; ein verspätet angekommener Befehl wird übernommen, eine nicht
  bestätigte Rückschaltung wiederholt (sichtbar als „Rückkehr nicht bestätigt“). Nach einer
  NeuraCell-X-Schutzphase oder einer Unterbrechung der Bridge gilt eine Schonfrist, alte
  Gerätewerte (> 10 min) oder eine offline gemeldete Bridge sperren jede Entscheidung,
  retained MQTT-Werte werden ignoriert.
- **Außenwert** – ein Sensor am Haus geht immer vor, eine Wetterstation ist nur die
  Rückfallebene:
  - MQTT `ambientika/weather` → `{"temperature": 21.4, "humidity": 60}` (Shelly, ESPHome,
    Home Assistant …) oder `ambientika/dewpoint/outdoor_temp` (nackte Zahl, dasselbe Topic
    wie die NeuraCell-X-Taupunktberechnung)
  - HTTP `POST /api/freecooling/outdoor` `{"temperature": 21.4, "humidity": 60, "kind": "sensor"}`
  - OpenWeatherMap vom Backend (`OWM_API_KEY`, `OWM_LOCATION` in `.env`); die PWA meldet
    ihren OpenWeatherMap-Wert ebenfalls als Rückfallebene
- **Unangetastet** bleiben Aus, Außer Haus, Überwachung und manuell gewählte
  Luftstrom-Modi. Stellt jemand während des Kühlens um, zieht sich der Wächter für eine
  Pause (60 min) zurück. **NeuraCell-X (Radon/Taupunkt) hat immer Vorrang.**
- **Protokoll** – jede Umschaltung wird mit Innen-/Außenwert, Quelle und Alter des
  Außenwerts, Modus vorher/nachher und Grund in SQLite festgehalten (400 Tage) und lässt
  sich als CSV exportieren. Damit ist die Arbeitsweise der Anlage belegbar.

Einschalten und einstellen im Tab **❄️ Free Cooling** der App (oder `FREECOOLING_ENABLED=true`
in `.env`; Werte aus der App gewinnen und überleben einen Neustart).
Standardmäßig steuert der Wächter alle **Master** – die Slaves einer Zone folgen ihrem Master; sollen
bestimmte Geräte ausdrücklich angesteuert werden, lassen sie sich in der Geräteliste auswählen.
Die Nachtstufe greift bei Dunkelheit (Dämmerungssensor des Geräts) oder in einer festen
Nachtzeit (`22:00-06:00`, Zeitzone `TZ`).

**API:** `GET /api/freecooling` (Status), `GET|PUT /api/freecooling/config`,
`GET /api/freecooling/log` und `/log.csv`, `POST /api/freecooling/outdoor`,
`POST /api/freecooling/evaluate`.

---

## 🔌 REST API

Die API ist unter `http://DEINE-IP:8080/api` erreichbar.  
Interaktive Dokumentation: **http://DEINE-IP:8080/docs**

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/devices` | GET | Alle Geräte mit Status |
| `/api/devices/{id}` | GET | Einzelnes Gerät |
| `/api/devices/{id}/command` | POST | Befehl senden |
| `/api/freecooling` | GET | Free-Cooling-Wächter: Status, Außenwert, Geräte |
| `/api/freecooling/config` | GET / PUT | Wächter einstellen (wird gespeichert) |
| `/api/freecooling/log` | GET | Umschalt-Protokoll (`/log.csv` als CSV) |
| `/api/freecooling/outdoor` | POST | Außenwert melden |
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
│   ├── freecooling/       # Free-Cooling-Wächter (Regel, Außenwert, Protokoll, API)
│   ├── history/           # lokale Messwert-Historie (SQLite)
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

---

## Betrieb ganz ohne unseren Server

Es gibt zwei Wege, die Anlage ohne den Ambientika-Server zu betreiben:

- **Lokaler Betriebsmodus dieser App** — `docker-compose.local.yml` ersetzt die Server-Abfrage durch die lokale Bridge. Einrichtung: [`README_LOCAL_CLOUDLESS.de.md`](docs/README_LOCAL_CLOUDLESS.de.md)
- **Eigenständiger lokaler Server** — ersetzt den Server vollständig, sodass auch die Ambientika-App unverändert gegen ihn läuft: [ambientika-local-standalone](https://github.com/ambientika-eu/ambientika-local-standalone)

Beide setzen auf einer Anlage auf, die einmalig über die Ambientika-App in Betrieb genommen wurde; danach bleibt alles im Haus.

Der Standardmodus über den Ambientika-Server ist im Feld bewährt und bleibt die empfohlene Variante für den Regelbetrieb. Die serverlosen Varianten sind als **Rückfallebene** gedacht — für Wartungsfenster, Netzstörungen oder Objekte ohne dauerhafte Internetanbindung. Sie sind neu und noch nicht an allen Firmwareständen erprobt und werden deshalb im Beobachtungsmodus ausgeliefert.
