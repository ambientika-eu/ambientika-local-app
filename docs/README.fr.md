🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · **FR** · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · [PT](README.pt.md) · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **Application locale pour appareils de ventilation Ambientika – contrôle sur le réseau domestique, au choix 100% sans cloud.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 Qu'est-ce que c'est ?

Une application web (PWA) entièrement **locale** pour contrôler les appareils de ventilation Ambientika –  
le contrôle s'exécute entièrement en local sur le réseau domestique, sans transmettre de données à des tiers. La connexion des appareils propose deux **modes de fonctionnement** (voir ci-dessous), dont une variante **100% sans cloud** sans serveur cloud et sans connexion Internet permanente.

L'application s'exécute sur le réseau domestique, sur un Raspberry Pi, un NAS ou n'importe quel serveur Linux  
et est accessible depuis un navigateur (mobile et PC) – y compris en tant qu'application installable.

---

## 🔀 Modes de fonctionnement

L'application et le contrôle s'exécutent toujours en local sur le réseau domestique. Pour la **connexion des appareils**, il existe deux modes :

- **Standard – Cloud-Bridge :** `docker-compose.yml` démarre l'`ambientika-mqtt-bridge`, qui interroge le cloud Ambientika et met les données à disposition en local via MQTT. Nécessite une connexion unique au cloud (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) et Internet.
- **100% sans cloud – bridge local :** `docker-compose.local.yml` communique avec les appareils via le bridge TCP local (`ambientika_local_bridge.py`, port 11000) directement sur le LAN, **sans serveur cloud et sans connexion Internet permanente**. Configuration : [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.fr.md) et [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.fr.md).

La fonction point de rosée / protection contre l'humidité fonctionne de toute façon de manière autonome dans l'appareil et est indépendante des deux.

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

## ⚡ Démarrage rapide (Docker Compose)

> **Mode standard (Cloud-Bridge).** Pour un fonctionnement 100% sans cloud, consultez **Modes de fonctionnement** ci-dessus, ainsi que `README_LOCAL_CLOUDLESS.md` et `CLOUD-INTEGRATION.md`.

### 1. Cloner le dépôt

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configuration

```bash
cp .env.example .env
```

Ensuite, modifiez `.env` :

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Démarrer

```bash
docker compose up -d
```

### 4. Ouvrir l'application

Navigateur : **http://DEINE-IP:8080**

> Sur mobile : ajoutez à l'écran d'accueil → comme une application native !

---

## 📱 Fonctionnalités

| Fonctionnalité | Statut |
|---------|--------|
| Tous les appareils en un coup d'œil | ✅ |
| Changer de mode (HRV / Nuit / Boost / Eco / Arrêt) | ✅ |
| Contrôler la vitesse du ventilateur | ✅ |
| Température / humidité / CO₂ en temps réel | ✅ |
| Indication d'alarme de filtre | ✅ |
| **NeuraCell-X® – Protection radon (brevet en cours)** | ✅ |
| **NeuraCell-X® – Contrôle du point de rosée** | ✅ |
| PWA – installable sur mobile et PC | ✅ |
| Mode hors ligne (Service Worker) | ✅ |
| Mises à jour en direct par WebSocket | ✅ |
| 100% local – sans serveur cloud | ✅ |
| Conforme au RGPD | ✅ |

---

## 🛡️ NeuraCell-X® (brevet en cours)

L'application intègre **NeuraCell-X®** – la technologie de protection d'Ambientika avec brevet en cours –
directement dans son propre onglet, entièrement en local :

- ☢️ **Protection radon (priorité maximale).** En cas d'alarme radon, NeuraCell-X met *tous*
  les appareils en **mode air soufflé (niveau 1)** et crée une légère surpression contre
  les infiltrations de radon. Affichage en direct de la valeur de radon (Bq/m³) et du seuil.
- 💧 **Contrôle du point de rosée.** Si ventiler augmentait l'humidité ambiante, NeuraCell-X
  **éteint** les appareils ; lorsque les conditions sont bonnes, la ventilation est de nouveau libérée. Affichage du
  point de rosée intérieur/extérieur. **La protection radon est toujours prioritaire.**
- 🔄 **Restauration exacte.** Lorsque toutes les fonctions de protection sont inactives, le mode
  actif précédent est restauré exactement pour chaque appareil.
- 🧪 **Autotest / forçage manuel** directement depuis l'application d'une simple pression.

> La logique proprement dite s'exécute dans le [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (topic d'état `ambientika/neuracell/state`). L'application affiche l'état en direct et peut
> déclencher la protection radon ou le blocage du point de rosée via `ambientika/radon/alarm` et
> `ambientika/dewpoint/block`.

**Nouveaux endpoints de l'API :** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

L'API est accessible à l'adresse `http://DEINE-IP:8080/api`.  
Documentation interactive : **http://DEINE-IP:8080/docs**

| Endpoint | Méthode | Description |
|----------|---------|--------------|
| `/api/devices` | GET | Tous les appareils avec leur état |
| `/api/devices/{id}` | GET | Un seul appareil |
| `/api/devices/{id}/command` | POST | Envoyer une commande |
| `/api/health` | GET | État du système |
| `/ws` | WebSocket | Mises à jour en direct |

### Exemple : changer de mode

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Exemple : ventilateur à 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Structure du projet

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

## 🔧 Installation manuelle (sans Docker)

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

## 🌍 Liens

- 🌐 [ambientika.eu](https://www.ambientika.eu)
- 📦 [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
- 🏠 [HA Add-on](https://github.com/ambientika-eu/ambientika-ha-addon)

---

## 📄 Licence

Licence MIT – © Ambientika / SUEDWIND
