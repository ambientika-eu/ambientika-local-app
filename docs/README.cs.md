🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · **CS**

# Ambientika Local App 🏠

> **Lokální aplikace pro větrací jednotky Ambientika – ovládání v domácí síti, volitelně 100% bez cloudu.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Co to je?

Plně **lokální** webová aplikace (PWA) pro ovládání větracích jednotek Ambientika –  
ovládání běží zcela lokálně v domácí síti, bez předávání dat třetím stranám. Připojení zařízení je k dispozici ve dvou **provozních režimech** (viz níže) — včetně **100% bezcloudové** varianty bez cloudového serveru a bez trvalého připojení k internetu.

Aplikace běží v domácí síti na Raspberry Pi, NAS nebo jakémkoli Linux serveru  
a je dostupná přes prohlížeč (mobil i PC) – i jako instalovatelná aplikace.

---

## 🔀 Provozní režimy

Aplikace i ovládání běží vždy lokálně v domácí síti. Pro **připojení zařízení** existují dva režimy:

- **Standard – Cloud-Bridge:** `docker-compose.yml` spustí `ambientika-mqtt-bridge`, která se dotazuje cloudu Ambientika a data poskytuje lokálně přes MQTT. Vyžaduje jednorázové přihlášení do cloudu (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) a internet.
- **100% bez cloudu – lokální Bridge:** `docker-compose.local.yml` komunikuje se zařízeními přímo v síti LAN přes lokální TCP-Bridge (`ambientika_local_bridge.py`, port 11000) — **bez cloudového serveru a bez trvalého připojení k internetu**. Nastavení: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.cs.md) a [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.cs.md).

Funkce ochrany rosného bodu / ochrany proti vlhkosti běží tak jako tak autonomně v zařízení a je na obojím nezávislá.

## 🏗️ Architektura

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

## ⚡ Rychlý start (Docker Compose)

> **Standardní režim (Cloud-Bridge).** Pro 100% bezcloudový provoz viz **Provozní režimy** výše a také `README_LOCAL_CLOUDLESS.md` a `CLOUD-INTEGRATION.md`.

### 1. Naklonování repozitáře

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Konfigurace

```bash
cp .env.example .env
```

Poté upravte `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Spuštění

```bash
docker compose up -d
```

### 4. Otevření aplikace

Prohlížeč: **http://DEINE-IP:8080**

> Na mobilu: Přidat na plochu → jako nativní aplikace!

---

## 📱 Funkce

| Funkce | Stav |
|---------|--------|
| Všechna zařízení na první pohled | ✅ |
| Přepínání režimů (HRV / Noc / Boost / Eco / Vypnuto) | ✅ |
| Ovládání rychlosti ventilátoru | ✅ |
| Teplota / vlhkost / CO₂ v reálném čase | ✅ |
| Zobrazení alarmu filtru | ✅ |
| **NeuraCell-X® – Ochrana proti radonu (patent přihlášen)** | ✅ |
| **NeuraCell-X® – Řízení podle rosného bodu** | ✅ |
| PWA – instalovatelná na mobil i PC | ✅ |
| Offline režim (Service Worker) | ✅ |
| Živé aktualizace přes WebSocket | ✅ |
| 100% lokální – žádný cloudový server | ✅ |
| V souladu s GDPR | ✅ |

---

## 🛡️ NeuraCell-X® (patent přihlášen)

Aplikace přináší **NeuraCell-X®** – patentově přihlášenou ochrannou technologii od Ambientika –
přímo do vlastní záložky, plně lokálně:

- ☢️ **Ochrana proti radonu (nejvyšší priorita).** Při radonovém alarmu přepne NeuraCell-X *všechna*
  zařízení do **režimu přiváděného vzduchu (stupeň 1)** a vytvoří mírný přetlak proti
  pronikajícímu radonu. Živé zobrazení hodnoty radonu (Bq/m³) a prahu.
- 💧 **Řízení podle rosného bodu.** Pokud by větrání zvýšilo vlhkost v místnosti, NeuraCell-X
  zařízení **vypne**; při dobrých podmínkách je opět uvolní. Zobrazení
  rosného bodu uvnitř/venku. **Ochrana proti radonu má vždy přednost.**
- 🔄 **Přesné obnovení.** Jsou-li všechny ochranné funkce neaktivní, přesně se pro každé
  zařízení obnoví dříve aktivní režim.
- 🧪 **Samočinný test / ruční přepsání** přímo z aplikace stisknutím tlačítka.

> Samotná logika běží v [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (stavové téma `ambientika/neuracell/state`). Aplikace zobrazuje živý stav a může
> spustit ochranu proti radonu, resp. blokování podle rosného bodu, přes `ambientika/radon/alarm` a
> `ambientika/dewpoint/block`.

**Nové API endpointy:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

API je dostupné na `http://DEINE-IP:8080/api`.  
Interaktivní dokumentace: **http://DEINE-IP:8080/docs**

| Endpoint | Metoda | Popis |
|----------|---------|--------------|
| `/api/devices` | GET | Všechna zařízení se stavem |
| `/api/devices/{id}` | GET | Jednotlivé zařízení |
| `/api/devices/{id}/command` | POST | Odeslání příkazu |
| `/api/health` | GET | Stav systému |
| `/ws` | WebSocket | Živé aktualizace |

### Příklad: přepnutí režimu

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Příklad: ventilátor na 60 %

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Struktura projektu

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

## 🔧 Ruční instalace (bez Dockeru)

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

## 🌍 Odkazy

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licence

MIT License – © Ambientika / SUEDWIND
