🌐 [DE](../README.md) · [EN](README.en.md) · [IT](README.it.md) · [FR](README.fr.md) · [ES](README.es.md) · [NL](README.nl.md) · [PL](README.pl.md) · **PT** · [SV](README.sv.md) · [DA](README.da.md) · [CS](README.cs.md)

# Ambientika Local App 🏠

> **App local para dispositivos de ventilação Ambientika – controlo na rede doméstica, opcionalmente 100% sem cloud.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-ambientika--eu-green)](https://github.com/ambientika-eu)

---

## 🎯 O que é isto?

Uma aplicação web (PWA) totalmente **local** para o controlo de dispositivos de ventilação Ambientika –  
o controlo é executado inteiramente de forma local na rede doméstica, sem partilha de dados com terceiros. A ligação aos dispositivos existe em dois **modos de funcionamento** (ver abaixo) — incluindo uma variante **100% sem cloud** sem servidor na cloud e sem ligação permanente à internet.

A app é executada na rede doméstica num Raspberry Pi, NAS ou em qualquer servidor Linux  
e está acessível através do navegador (telemóvel e PC) – também como app instalável.

---

## 🔀 Modos de funcionamento

A app e o controlo são sempre executados de forma local na rede doméstica. Para a **ligação aos dispositivos** existem dois modos:

- **Padrão – Cloud-Bridge:** o `docker-compose.yml` inicia a `ambientika-mqtt-bridge`, que consulta a cloud da Ambientika e disponibiliza os dados localmente por MQTT. Requer um registo único na cloud (`AMBIENTIKA_EMAIL` / `AMBIENTIKA_PASSWORD`) e internet.
- **100% sem cloud – bridge local:** o `docker-compose.local.yml` comunica com os dispositivos através da bridge TCP local (`ambientika_local_bridge.py`, porta 11000) diretamente na LAN — **sem servidor na cloud e sem ligação permanente à internet**. Configuração: [`README_LOCAL_CLOUDLESS.md`](README_LOCAL_CLOUDLESS.pt.md) e [`CLOUD-INTEGRATION.md`](CLOUD-INTEGRATION.pt.md).

A função de proteção contra ponto de orvalho/humidade é, de qualquer forma, executada de modo autónomo no dispositivo e é independente de ambos.

## 🏗️ Arquitetura

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

> **Modo padrão (Cloud-Bridge).** Para o funcionamento 100% sem cloud, ver **Modos de funcionamento** acima, bem como `README_LOCAL_CLOUDLESS.md` e `CLOUD-INTEGRATION.md`.

### 1. Clonar o repositório

```bash
git clone https://github.com/ambientika-eu/ambientika-local-app.git
cd ambientika-local-app
```

### 2. Configuração

```bash
cp .env.example .env
```

Depois, editar o `.env`:

```env
AMBIENTIKA_EMAIL=deine@email.de
AMBIENTIKA_PASSWORD=deinPasswort
```

### 3. Iniciar

```bash
docker compose up -d
```

### 4. Abrir a app

Navegador: **http://DEINE-IP:8080**

> No telemóvel: adicionar ao ecrã inicial → como uma app nativa!

---

## 📱 Funcionalidades

| Funcionalidade | Estado |
|---------|--------|
| Todos os dispositivos num relance | ✅ |
| Mudar de modo (HRV / Noite / Boost / Eco / Desligado) | ✅ |
| Controlar a velocidade do ventilador | ✅ |
| Temperatura / Humidade / CO₂ em tempo real | ✅ |
| Indicação de alarme de filtro | ✅ |
| **NeuraCell-X® – Proteção contra rádon (patente pendente)** | ✅ |
| **NeuraCell-X® – Controlo do ponto de orvalho** | ✅ |
| PWA – instalável em telemóvel e PC | ✅ |
| Modo offline (Service Worker) | ✅ |
| Atualizações em direto por WebSocket | ✅ |
| 100% local – sem servidor na cloud | ✅ |
| Conforme ao RGPD | ✅ |

---

## 🛡️ NeuraCell-X® (patente pendente)

A app traz o **NeuraCell-X®** – a tecnologia de proteção da Ambientika com patente pendente –
diretamente para um separador próprio, totalmente local:

- ☢️ **Proteção contra rádon (prioridade máxima).** Em caso de alarme de rádon, o NeuraCell-X coloca *todos*
  os dispositivos em **modo de ar de insuflação (nível 1)** e gera uma ligeira sobrepressão contra
  a entrada de rádon. Indicação em direto do valor de rádon (Bq/m³) e do limiar.
- 💧 **Controlo do ponto de orvalho.** Se ventilar aumentasse a humidade ambiente, o NeuraCell-X
  **desliga** os dispositivos; em boas condições, é novamente libertado. Indicação do
  ponto de orvalho interior/exterior. **A proteção contra rádon tem sempre prioridade.**
- 🔄 **Restauro exato.** Quando todas as funções de proteção estão inativas, o modo
  anteriormente ativo de cada dispositivo é restaurado com exatidão.
- 🧪 **Autoteste / sobreposição manual** diretamente a partir da app, ao toque de um botão.

> A lógica propriamente dita é executada na [MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge)
> (tópico de estado `ambientika/neuracell/state`). A app mostra o estado em direto e pode
> acionar a proteção contra rádon ou o bloqueio por ponto de orvalho através de `ambientika/radon/alarm` e
> `ambientika/dewpoint/block`.

**Novos endpoints da API:** `GET /api/neuracell`, `POST /api/neuracell/radon` `{active}`,
`POST /api/neuracell/dewpoint` `{block}`.

---

## 🔌 REST API

A API está acessível em `http://DEINE-IP:8080/api`.  
Documentação interativa: **http://DEINE-IP:8080/docs**

| Endpoint | Método | Descrição |
|----------|---------|--------------|
| `/api/devices` | GET | Todos os dispositivos com estado |
| `/api/devices/{id}` | GET | Dispositivo individual |
| `/api/devices/{id}/command` | POST | Enviar comando |
| `/api/health` | GET | Estado do sistema |
| `/ws` | WebSocket | Atualizações em direto |

### Exemplo: mudar de modo

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"mode": "NIGHT"}'
```

### Exemplo: ventilador a 60%

```bash
curl -X POST http://localhost:8080/api/devices/DEV001/command \
  -H "Content-Type: application/json" \
  -d '{"fanSpeed": 60}'
```

---

## 📁 Estrutura do projeto

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

## 🔧 Instalação manual (sem Docker)

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

## 📄 Licença

MIT License – © Ambientika / SUEDWIND
