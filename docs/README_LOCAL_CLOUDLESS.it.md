🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · **IT** · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – modalità operativa locale senza server

> **Inquadramento.** La modalità operativa consigliata della Local App è il bridge
> cloud (`docker-compose.yml`): collaudata sul campo e prevista per l'esercizio
> ordinario. La modalità locale qui descritta è concepita come **livello di riserva**.
> Mantiene l'impianto utilizzabile quando il server Ambientika non è raggiungibile —
> durante finestre di manutenzione, disservizi di rete o in edifici per i quali non è
> prevista una connessione internet permanente. Non sostituisce la modalità standard.

In questa modalità la Ambientika Local App (FastAPI + PWA) gestisce l'impianto **senza
server Ambientika e senza connessione internet**. Rispetto alla modalità standard
cambia esclusivamente il collegamento ai dispositivi: al posto del bridge che
interroga il server subentra un **bridge locale** che comunica con le unità di
ventilazione direttamente nella rete domestica tramite il loro protocollo TCP nativo
(porta 11000).

```
Modalità standard:  Unità → server Ambientika → bridge cloud → MQTT → app
Modalità locale:    Unità → bridge locale (TCP 11000) → MQTT → app     ← senza server
```

L'intero set di funzioni rimane disponibile:

- monitoraggio e controllo dei dispositivi (modalità, ventola, sensori, punto di rugiada)
- **esecuzione del programma settimanale**
- **NeuraCell-X**: protezione dal radon (prioritaria) e **controllo del punto di
  rugiada**, con ripristino esatto della modalità attiva in precedenza

Il backend della Local App e la PWA vengono utilizzati **senza modifiche** — il bridge
locale pubblica gli stessi topic e lo stesso vocabolario di campi della modalità
standard (nomi di modalità `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %,
`airQuality` int, `filterAlarm` bool, più `dewPoint`).

## Componenti

Viene sostituito soltanto il collegamento ai dispositivi; app e logica di controllo
restano invariate.

```
docker-compose.local.yml          # stack senza interrogazione del server
Dockerfile.bridge                 # immagine del bridge locale
ambientika_local_bridge.py        # bridge locale (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # configurazione del broker locale
env.local.example.txt             # modello di configurazione (senza credenziali server)
```

## Esecuzione

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Puntare le unità verso questo host (obbligatorio, una tantum)

Le unità si connettono all'host che è stato scritto durante il provisioning BLE:

1. **Ri-provisioning BLE:** scrivere `H_<host-ip>:11000`, `S_<ssid>`,
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
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | configurazione inviata alla connessione |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | esecutore del programma |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | controller radon + punto di rugiada |
| `RADON_THRESHOLD` | `100` | soglia di attivazione automatica Bq/m³ |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | punto di rugiada automatico + isteresi °C |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | pubblica il discovery di Home Assistant (non necessario per l'app) |

## Garanzia di qualità

- ✅ Codec di trasmissione byte per byte rispetto a die Protokollspezifikation (temperatura e RSSI decodificati
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

## Parametrizzazione

Le corrispondenze di modalità e ventola nonché le soglie per la protezione dal radon e
dal punto di rugiada sono preimpostate con valori sicuri per l'applicazione e sono
adattabili per progetto in `ambientika_local_bridge.py` (due tabelle di corrispondenza
e i campi `Config`): `BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`,
le soglie dei livelli di ventilazione e `RADON_THRESHOLD` e `DEWPOINT_MARGIN`. I
valori limite specifici dell'edificio — in particolare le soglie di radon prescritte
dalle autorità — vanno impostati in fase di messa in servizio.

## Stato di rilascio

La modalità operativa locale viene fornita come **rilascio controllato (modalità di
osservazione)**: la validazione sul campo sull'intero parco firmware distribuito non è
ancora conclusa e i riscontri dalle installazioni confluiscono costantemente nel
rilascio. Per l'esercizio ordinario il bridge cloud resta la variante consigliata; la
modalità locale è il livello di riserva per il caso in cui il server non sia
raggiungibile. Si prega di inviare i riscontri tramite le issue di questo repository.
