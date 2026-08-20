🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · **IT** · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – stack applicativo 100% senza cloud

Esegue la Ambientika Local App (FastAPI + PWA) **senza cloud SUEDWIND e senza
internet**. L'unica differenza rispetto allo stack originale è la sorgente dei dati: il
bridge MQTT che interroga il cloud viene sostituito da un **bridge locale** che comunica
con le unità di ventilazione direttamente tramite il loro protocollo raw-TCP nativo (porta 11000).

Il bridge ora copre l'intero set di funzionalità in modo cloud-free:

- monitoraggio e controllo dei dispositivi (modalità, ventola, sensori, punto di rugiada)
- **esecuzione del programma settimanale** (Wochenzeitplan)
- **NeuraCell-X**: protezione dal radon (priorità) + **controllo del punto di rugiada
  (Taupunktsteuerung)**, con ripristino esatto della modalità precedente.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Il backend della local-app e la PWA vengono utilizzati **senza modifiche** — il bridge pubblica gli
stessi topic e lo stesso vocabolario di campi che l'app si aspetta (nomi di modalità intuitivi
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, più `dewPoint`).

## File da aggiungere alla radice del repo `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Esecuzione

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Puntare le unità verso questo host (obbligatorio, una tantum)

Le unità si connettono all'host che è stato scritto durante il provisioning BLE:

1. **Ri-provisioning BLE (preferito):** scrivere `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` su ciascuna unità.
2. **Route statica / DNAT:** reindirizzare `185.214.203.87/32` → questo host e aggiungere un
   alias IP affinché l'host accetti i pacchetti destinati all'IP del cloud.

Dettagli in `CLOUD-INTEGRATION.md`.

## Topic MQTT

| Topic | Dir | Significato |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | stato del dispositivo (JSON, vocabolario dell'app + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | comando `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | in | programma settimanale completo (dall'app) |
| `ambientika/<serial>/schedule/<day>/set` | in | fasce orarie di un giorno |
| `ambientika/neuracell/state` | out | stato live di NeuraCell-X (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — forza / azzera la protezione dal radon |
| `ambientika/radon/value` | in | lettura del radon (Bq/m³) — si attiva automaticamente alla soglia |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — forza / rilascia il blocco per punto di rugiada |
| `ambientika/weather` | in | aria ESTERNA `{"temperature": t, "humidity": rh}` |

## Programma settimanale

A trigger sul fronte: quando una fascia diventa attiva per il giorno/ora corrente, il
bridge applica la sua `mode` (+ `fanSpeed`, oppure mantiene la velocità corrente se la fascia
non ne specifica una) esattamente **una volta**, in modo da non contrastare una modifica manuale
effettuata all'interno di una fascia. Il programma viene sospeso mentre è attiva una protezione NeuraCell-X.

## NeuraCell-X (radon + punto di rugiada)

Priorità: **radon > punto di rugiada > normale**. Alla prima transizione verso una qualsiasi
protezione il bridge salva la modalità/ventola corrente di ciascuna unità come baseline; quando tutte
le protezioni si azzerano esegue un **ripristino esatto**.

- **Protezione dal radon** — si attiva quando `radon/alarm=ON` oppure `radon/value ≥
  RADON_THRESHOLD`. Tutte le unità → `INTAKE` a `LOW` (leggera sovrapressione di aria fresca).
  I normali comandi `/set` vengono soppressi mentre è attiva.
- **Controllo del punto di rugiada (Taupunktsteuerung)** — si attiva quando `dewpoint/block=ON`, oppure
  automaticamente quando il punto di rugiada **esterno** è pari o superiore a quello interno
  (meno `DEWPOINT_MARGIN`), ovvero quando ventilare aggiungerebbe umidità. Tutte le unità →
  `OFF`. Richiede dati esterni su `ambientika/weather`; senza di essi funziona solo la
  forzatura manuale. Il punto di rugiada interno viene calcolato dalla temperatura+umidità di ciascuna unità
  (formula di Magnus).

## Configurazione (env)

| Var | Default | Significato |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | prefisso dei topic (mantenere `ambientika` per corrispondere all'app) |
| `LOCAL_TCP_PORT` | `11000` | porta a cui si connettono le unità |
| `SEND_SETUP` | `false` | abilita esplicitamente la scrittura della topologia alla connessione; verificare prima tutti i valori seguenti |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | valori di configurazione usati solo con `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | esecutore del programma |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | controller radon + punto di rugiada |
| `RADON_THRESHOLD` | `100` | soglia di attivazione automatica Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | punto di rugiada automatico + isteresi °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | pubblica il discovery di Home Assistant (non necessario per l'app) |

## Stato della verifica

- ✅ Codec di trasmissione byte per byte rispetto a `PROTOCOL.md` (temperatura e RSSI decodificati
  con **segno**).
- ✅ Round-trip del vocabolario dell'app (nomi di modalità, fanSpeed %, punto di rugiada).
- ✅ Programma settimanale: il trigger sul fronte applica le fasce una sola volta; altrimenti no-op; orari
  normalizzati a `HH:MM`.
- ✅ NeuraCell-X: priorità radon, soppressione dei comandi, punto di rugiada automatico + manuale
  con **isteresi ±margine**, e **ripristino esatto** della modalità precedente alla protezione
  (baseline presa dall'ultimo target normale, non dall'eco del dispositivo).
- ✅ Concorrenza rafforzata: un unico lock serializza comando/programma/NeuraCell,
  lo stato di protezione viene committato **prima** di qualsiasi scrittura protettiva, i loop sui dispositivi
  iterano su snapshot, le scritture sono serializzate per dispositivo.
- ✅ Robustezza: il framing TCP si risincronizza dopo un byte spurio; i payload `weather`
  malformati vengono rifiutati; la riconnessione preserva lo stato del dispositivo; gli input radon/weather
  più vecchi di `NC_INPUT_TTL` vengono trattati come sconosciuti; last-will MQTT + arresto pulito.
- ✅ Suite di regressione: **40 test unitari/di integrazione** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Test end-to-end completo attraverso un **broker MQTT reale** con un'unità simulata
  (`smoke_test.py`, 13/13): status + comando + programma + protezione radon/soppressione/
  ripristino + punto di rugiada + risincronizzazione del framing + arresto.
- ✅ `docker compose config` valido; nessuna credenziale cloud in alcun punto dello stack.
- ✅ API di callback paho-mqtt 2.x (VERSION2), fallback 1.x mantenuto.
- ⛔️ **Non ancora testato su hardware reale** — binario decodificato tramite reverse engineering + controllo
  rilevante per la sicurezza. Validare su un'unità fisica prima della produzione (in
  particolare la decodifica con segno di temperatura/RSSI).

## Approvazione prima della produzione `>>> CONTROL <<<` / `>>> MAPPING <<<`

Le mappature modalità/ventola e le soglie e i target radon/punto di rugiada sono valori
predefiniti ragionevoli, non certificati. Farli revisionare rispetto alle specifiche di prodotto e regolarli in
`ambientika_local_bridge.py` (due tabelle di mappatura + i campi di controllo `Config`).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, le soglie ventola %→livello,
`RADON_THRESHOLD` e `DEWPOINT_MARGIN` sono i valori da confermare.
