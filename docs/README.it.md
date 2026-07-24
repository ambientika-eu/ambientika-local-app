🌐 [DE](../README.md) · [EN](README.en.md) · **IT** · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **App locale per i dispositivi di ventilazione Ambientika – controllo nella rete domestica, opzionalmente 100% senza cloud.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Che cos'è?

Una web app (PWA) completamente **locale** per il controllo dei dispositivi di ventilazione Ambientika –  
il controllo funziona interamente in locale nella rete domestica, senza trasmissione di dati a terzi. Il collegamento ai dispositivi è disponibile in due **modalità operative** (vedi sotto) — inclusa una variante **100% senza cloud** senza server cloud e senza connessione internet permanente.

L'app funziona nella rete domestica su un Raspberry Pi, un NAS o qualsiasi server Linux  
ed è raggiungibile tramite browser (smartphone e PC) – anche come app installabile.

---

## 🔀 Modalità operative

L'app e il controllo funzionano sempre in locale nella rete domestica. Per il **collegamento ai dispositivi** esistono due modalità:

- **Standard – Cloud-Bridge:** `docker-compose.yml` avvia il bridge `ambientika-mqtt-bridge`, che interroga il cloud Ambientika e mette a disposizione i dati in locale tramite MQTT. Richiede un accesso cloud una tantum (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) e internet.
- **100% senza cloud – bridge locale:** `docker-compose.local.yml` comunica con i dispositivi tramite il bridge TCP locale (`ambientika_local_bridge.py`, porta 11000) direttamente nella LAN — **senza server cloud e senza connessione internet permanente**. Configurazione: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.it.md) e [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.it.md).

La funzione di controllo del punto di rugiada / protezione dall'umidità funziona comunque in modo autonomo nel dispositivo ed è indipendente da entrambe le modalità.

## 🏗️ Architettura

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

> **Modalità standard (Cloud-Bridge).** Per il funzionamento 100% senza cloud vedi **Modalità operative** sopra, nonché `README_LOCAL_CLOUDLESS.md` e `CLOUD-INTEGRATION.md`.

### 1. Clonare il repository

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configurazione

```bash
cp .env.example .env
```

Poi modificare `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Avvio

```bash
docker compose up -d
```

### 4. Aprire l'app

Browser: **http://DEINE-IP:8080**

> Sullo smartphone: Aggiungi alla schermata Home → come un'app nativa!

---

## 📱 Funzionalità

| Funzionalità | Stato |
|---------|--------|
| Tutti i dispositivi a colpo d'occhio | ✅ |
| Cambio modalità (HRV / Notte / Boost / Eco / Spento) | ✅ |
| Controllo della velocità della ventola | ✅ |
| Temperatura / Umidità / CO₂ in tempo reale | ✅ |
| Visualizzazione allarme filtro | ✅ |
| **NeuraCell-X® – Protezione dal radon (brevetto in corso di registrazione)** | ✅ |
| **NeuraCell-X® – Controllo del punto di rugiada** | ✅ |
| PWA – installabile su smartphone e PC | ✅ |
| Modalità offline (Service Worker) | ✅ |
| Aggiornamenti live via WebSocket | ✅ |
| 100% locale – nessun server cloud | ✅ |
| Conforme al GDPR | ✅ |

---

## 🛡️ NeuraCell-X® (brevetto in corso di registrazione)

L'app porta **NeuraCell-X®** – la tecnologia di protezione di Ambientika in corso di registrazione brevettuale –
direttamente in una scheda dedicata, in modo completamente locale:

- ☢️ **Protezione dal radon (massima priorità).** In caso di allarme radon, NeuraCell-X commuta *tutti*
  i dispositivi in **modalità immissione (livello 1)** e genera una leggera sovrapressione contro
  la penetrazione del radon. Visualizzazione in tempo reale del valore di radon (Bq/m³) e della soglia.
- 💧 **Controllo del punto di rugiada.** Se la ventilazione dovesse aumentare l'umidità dell'ambiente, NeuraCell-X
  **spegne** i dispositivi; in condizioni favorevoli vengono riattivati. Visualizzazione del
  punto di rugiada interno/esterno. **La protezione dal radon ha sempre la precedenza.**
- 🔄 **Ripristino esatto.** Quando tutte le funzioni di protezione sono inattive, viene ripristinata esattamente
  per ciascun dispositivo la modalità precedentemente attiva.
- 🧪 **Autotest / forzatura manuale** direttamente dall'app con la pressione di un pulsante.

> La logica vera e propria viene eseguita nella [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (topic di stato `ambientika/neuracell/state`). L'app mostra lo stato in tempo reale e può
> attivare la protezione dal radon o il blocco per punto di rugiada tramite `ambientika/radon/alarm` e
> `ambientika/dewpoint/block`.

**Nuovi endpoint API:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

L'API è raggiungibile all'indirizzo `http://DEINE-IP:8080/api`.  
Documentazione interattiva: **http://DEINE-IP:8080/docs**

| Endpoint | Metodo | Descrizione |
|----------|---------|--------------|
| `/api/devices` | GET | Tutti i dispositivi con stato |
| `/api/devices/{id}` | GET | Singolo dispositivo |
| `/api/devices/{id}/command` | POST | Invia comando |
| `/api/health` | GET | Stato del sistema |
| `/ws` | WebSocket | Aggiornamenti live |

### Esempio: cambio modalità

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Esempio: ventola al 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Struttura del progetto

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

## 🔧 Installazione manuale (senza Docker)

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

## 🌍 Link

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licenza

MIT License – © Ambientika / SUEDWIND
