🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · **ES** · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Aplicación local para dispositivos de ventilación Ambientika: control en la red doméstica, opcionalmente 100% sin nube.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 ¿Qué es esto?

Una aplicación web (PWA) totalmente **local** para controlar los dispositivos de ventilación Ambientika:  
el control se ejecuta por completo de forma local en la red doméstica, sin transmitir datos a terceros. La conexión de los dispositivos ofrece dos **modos de funcionamiento** (véase más abajo), incluida una variante **100% sin nube** sin servidor en la nube y sin conexión permanente a internet.

La aplicación se ejecuta en la red doméstica en una Raspberry Pi, un NAS o cualquier servidor Linux  
y es accesible desde el navegador (móvil y PC), incluso como aplicación instalable.

---

## 🔀 Modos de funcionamiento

La aplicación y el control se ejecutan siempre de forma local en la red doméstica. Para la **conexión de los dispositivos** hay dos modos:

- **Estándar – Cloud-Bridge:** `docker-compose.yml` inicia la `ambientika-mqtt-bridge`, que consulta la nube de Ambientika y proporciona los datos localmente mediante MQTT. Requiere un inicio de sesión único en la nube (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) e internet.
- **100% sin nube – bridge local:** `docker-compose.local.yml` se comunica con los dispositivos a través del bridge TCP local (`ambientika_local_bridge.py`, puerto 11000) directamente en la LAN, **sin servidor en la nube y sin conexión permanente a internet**. Configuración: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.es.md) y [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.es.md).

La función de punto de rocío/protección contra la humedad funciona de todos modos de forma autónoma en el dispositivo y es independiente de ambos.

## 🏗️ Arquitectura

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

## ⚡ Inicio rápido (Docker Compose)

> **Modo estándar (Cloud-Bridge).** Para el funcionamiento 100% sin nube, consulta **Modos de funcionamiento** más arriba, así como `README_LOCAL_CLOUDLESS.md` y `CLOUD-INTEGRATION.md`.

### 1. Clonar el repositorio

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configuración

```bash
cp .env.example .env
```

Después, edita `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Iniciar

```bash
docker compose up -d
```

### 4. Abrir la aplicación

Navegador: **http://DEINE-IP:8080**

> En el móvil: añade a la pantalla de inicio → ¡como una aplicación nativa!

---

## 📱 Funciones

| Función | Estado |
|---------|--------|
| Todos los dispositivos de un vistazo | ✅ |
| Cambiar de modo (HRV / Noche / Boost / Eco / Apagado) | ✅ |
| Controlar la velocidad del ventilador | ✅ |
| Temperatura / humedad / CO₂ en tiempo real | ✅ |
| Indicación de alarma de filtro | ✅ |
| **NeuraCell-X® – Protección contra radón (patente en trámite)** | ✅ |
| **NeuraCell-X® – Control del punto de rocío** | ✅ |
| PWA – instalable en móvil y PC | ✅ |
| Modo sin conexión (Service Worker) | ✅ |
| Actualizaciones en directo por WebSocket | ✅ |
| 100% local – sin servidor en la nube | ✅ |
| Conforme al RGPD | ✅ |

---

## 🛡️ NeuraCell-X® (patente en trámite)

La aplicación lleva **NeuraCell-X®** – la tecnología de protección de Ambientika con patente en trámite –
directamente a su propia pestaña, totalmente en local:

- ☢️ **Protección contra radón (máxima prioridad).** En caso de alarma de radón, NeuraCell-X pone *todos*
  los dispositivos en **modo de impulsión (nivel 1)** y genera una ligera sobrepresión contra
  la entrada de radón. Visualización en directo del valor de radón (Bq/m³) y el umbral.
- 💧 **Control del punto de rocío.** Si ventilar aumentara la humedad ambiente, NeuraCell-X
  **apaga** los dispositivos; con buenas condiciones se vuelve a liberar. Visualización del
  punto de rocío interior/exterior. **La protección contra radón tiene siempre prioridad.**
- 🔄 **Restauración exacta.** Cuando todas las funciones de protección están inactivas, se restaura
  exactamente el modo que estaba activo antes en cada dispositivo.
- 🧪 **Autotest / anulación manual** directamente desde la aplicación con solo pulsar un botón.

> La lógica propiamente dicha se ejecuta en la [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (tema de estado `ambientika/neuracell/state`). La aplicación muestra el estado en directo y puede
> activar la protección contra radón o el bloqueo del punto de rocío mediante `ambientika/radon/alarm` y
> `ambientika/dewpoint/block`.

**Nuevos endpoints de la API:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

La API es accesible en `http://DEINE-IP:8080/api`.  
Documentación interactiva: **http://DEINE-IP:8080/docs**

| Endpoint | Método | Descripción |
|----------|---------|--------------|
| `/api/devices` | GET | Todos los dispositivos con estado |
| `/api/devices/{id}` | GET | Un solo dispositivo |
| `/api/devices/{id}/command` | POST | Enviar comando |
| `/api/health` | GET | Estado del sistema |
| `/ws` | WebSocket | Actualizaciones en directo |

### Ejemplo: cambiar de modo

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Ejemplo: ventilador al 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Estructura del proyecto

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

## 🔧 Instalación manual (sin Docker)

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

## 🌍 Enlaces

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licencia

Licencia MIT – © Ambientika / SUEDWIND
