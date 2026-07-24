🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · **PL** · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Lokalna aplikacja do urządzeń wentylacyjnych Ambientika – sterowanie w sieci domowej, opcjonalnie w 100% bez chmury.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Co to jest?

W pełni **lokalna** aplikacja internetowa (PWA) do sterowania urządzeniami wentylacyjnymi Ambientika –  
sterowanie odbywa się w pełni lokalnie w sieci domowej, bez przekazywania danych stronom trzecim. Połączenie z urządzeniami dostępne jest w dwóch **trybach pracy** (patrz poniżej) — w tym w **w 100% bezchmurowym** wariancie bez serwera chmurowego i bez stałego połączenia z internetem.

Aplikacja działa w sieci domowej na urządzeniu Raspberry Pi, NAS lub dowolnym serwerze z systemem Linux  
i jest dostępna przez przeglądarkę (telefon i komputer) – również jako aplikacja do zainstalowania.

---

## 🔀 Tryby pracy

Aplikacja i sterowanie zawsze działają lokalnie w sieci domowej. Dla **połączenia z urządzeniami** dostępne są dwa tryby:

- **Standard – Bridge chmurowy:** `docker-compose.yml` uruchamia `ambientika-mqtt-bridge`, który odpytuje chmurę Ambientika i udostępnia dane lokalnie przez MQTT. Wymaga jednorazowego logowania do chmury (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) oraz internetu.
- **W 100% bez chmury – lokalny Bridge:** `docker-compose.local.yml` komunikuje się z urządzeniami przez lokalny Bridge TCP (`ambientika_local_bridge.py`, port 11000) bezpośrednio w sieci LAN — **bez serwera chmurowego i bez stałego połączenia z internetem**. Konfiguracja: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.pl.md) oraz [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.pl.md).

Funkcja ochrony punktu rosy / ochrony przed wilgocią i tak działa autonomicznie w urządzeniu i jest niezależna od obu tych trybów.

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

## ⚡ Szybki start (Docker Compose)

> **Tryb standardowy (Bridge chmurowy).** Informacje o pracy w 100% bez chmury znajdziesz w sekcji **Tryby pracy** powyżej oraz w plikach `README_LOCAL_CLOUDLESS.md` i `CLOUD-INTEGRATION.md`.

### 1. Klonowanie repozytorium

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Konfiguracja

```bash
cp .env.example .env
```

Następnie edytuj plik `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Uruchomienie

```bash
docker compose up -d
```

### 4. Otwieranie aplikacji

Przeglądarka: **http://DEINE-IP:8080**

> Na telefonie: dodaj do ekranu głównego → jak natywna aplikacja!

---

## 📱 Funkcje

| Funkcja | Status |
|---------|--------|
| Wszystkie urządzenia na jeden rzut oka | ✅ |
| Zmiana trybu (HRV / Noc / Boost / Eco / Wył.) | ✅ |
| Sterowanie prędkością wentylatora | ✅ |
| Temperatura / wilgotność / CO₂ w czasie rzeczywistym | ✅ |
| Wskaźnik alarmu filtra | ✅ |
| **NeuraCell-X® – Ochrona przed radonem (zgłoszone do opatentowania)** | ✅ |
| **NeuraCell-X® – Sterowanie punktem rosy** | ✅ |
| PWA – instalowalna na telefonie i komputerze | ✅ |
| Tryb offline (Service Worker) | ✅ |
| Aktualizacje na żywo przez WebSocket | ✅ |
| 100% lokalnie – brak serwera chmurowego | ✅ |
| Zgodne z RODO | ✅ |

---

## 🛡️ NeuraCell-X® (zgłoszone do opatentowania)

Aplikacja udostępnia **NeuraCell-X®** – zgłoszoną do opatentowania technologię ochronną Ambientika –
bezpośrednio na osobnej karcie, w pełni lokalnie:

- ☢️ **Ochrona przed radonem (najwyższy priorytet).** Przy alarmie radonowym NeuraCell-X przełącza *wszystkie*
  urządzenia w **tryb nawiewu (poziom 1)** i wytwarza lekkie nadciśnienie przeciwdziałające
  przenikaniu radonu. Podgląd na żywo wartości radonu (Bq/m³) i progu.
- 💧 **Sterowanie punktem rosy.** Jeśli wentylacja zwiększyłaby wilgotność w pomieszczeniu, NeuraCell-X
  **wyłącza** urządzenia; przy dobrych warunkach ponownie je odblokowuje. Wskazanie
  punktu rosy wewn./zewn. **Ochrona przed radonem ma zawsze pierwszeństwo.**
- 🔄 **Dokładne przywrócenie.** Gdy wszystkie funkcje ochronne są nieaktywne, poprzednio
  aktywny tryb zostaje dokładnie przywrócony dla każdego urządzenia.
- 🧪 **Autotest / ręczne nadpisanie** bezpośrednio z aplikacji jednym naciśnięciem przycisku.

> Właściwa logika działa w [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (temat statusu `ambientika/neuracell/state`). Aplikacja pokazuje status na żywo i może
> wyzwalać ochronę przed radonem lub blokadę punktu rosy przez `ambientika/radon/alarm` i
> `ambientika/dewpoint/block`.

**Nowe punkty końcowe API:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

API jest dostępne pod adresem `http://DEINE-IP:8080/api`.  
Dokumentacja interaktywna: **http://DEINE-IP:8080/docs**

| Endpoint | Metoda | Opis |
|----------|---------|--------------|
| `/api/devices` | GET | Wszystkie urządzenia ze statusem |
| `/api/devices/{id}` | GET | Pojedyncze urządzenie |
| `/api/devices/{id}/command` | POST | Wysłanie polecenia |
| `/api/health` | GET | Status systemu |
| `/ws` | WebSocket | Aktualizacje na żywo |

### Przykład: zmiana trybu

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Przykład: wentylator na 60%

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

## 🔧 Instalacja ręczna (bez Dockera)

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

## 🌍 Linki

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licencja

Licencja MIT – © Ambientika / SUEDWIND
