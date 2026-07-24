🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · **SV** · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – 100% molnfri app-stack

Kör Ambientika Local App (FastAPI + PWA) **utan SUEDWIND-moln och utan
internet**. Den enda skillnaden mot upstream-stacken är datakällan: den
molnpollande MQTT-bryggan ersätts av en **lokal brygga** som kommunicerar med
ventilationsenheterna direkt via deras inbyggda raw-TCP-protokoll (port 11000).

Bryggan täcker nu hela funktionsuppsättningen molnfritt:

- enhetsövervakning + styrning (läge, fläkt, sensorer, daggpunkt)
- **körning av veckoschema** (Veckoschema)
- **NeuraCell-X**: radonskydd (prioritet) + **daggpunktsstyrning**, med
  exakt återställning av det föregående läget.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Local-app-backenden och PWA:n används **omodifierade** — bryggan publicerar
samma topics och samma fältvokabulär som appen förväntar sig (läsvänliga lägesnamn
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, plus `dewPoint`).

## Filer att lägga till i roten av `ambientika-local-app`-repot

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Kör

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Rikta enheterna mot den här värden (obligatoriskt, engångs)

Enheterna ansluter till den värd som skrevs in vid BLE-provisioneringen:

1. **BLE-omprovisionering (rekommenderas):** skriv `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` till varje enhet.
2. **Statisk rutt / DNAT:** omdirigera `185.214.203.87/32` → den här värden och lägg
   till ett IP-alias så att värden accepterar paket för moln-IP:n.

Detaljer i `CLOUD-INTEGRATION.md`.

## MQTT-topics

| Topic | Riktn. | Betydelse |
|-------|-----|---------|
| `ambientika/<serial>/status` | ut | enhetstillstånd (JSON, app-vokabulär + `dewPoint`) |
| `ambientika/<serial>/availability` | ut | `online` / `offline` |
| `ambientika/<serial>/set` | in | `{mode, fanSpeed, ...}`-kommando |
| `ambientika/<serial>/schedule/set` | in | fullständigt veckoschema (från appen) |
| `ambientika/<serial>/schedule/<day>/set` | in | tidsintervall för en dag |
| `ambientika/neuracell/state` | ut | NeuraCell-X livestatus (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — tvinga / rensa radonskydd |
| `ambientika/radon/value` | in | radonavläsning (Bq/m³) — utlöses automatiskt vid tröskel |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — tvinga / frige daggpunktsspärr |
| `ambientika/weather` | in | `{"temperature": t, "humidity": rh}` UTOMHUSLUFT |

## Veckoschema

Flankutlöst: när ett tidsintervall blir aktivt för aktuell veckodag/tid tillämpar
bryggan dess `mode` (+ `fanSpeed`, eller behåller aktuell hastighet om intervallet
saknar sådan) exakt **en gång**, så att en manuell ändring inom ett intervall inte
motarbetas. Schemat pausas medan ett NeuraCell-X-skydd är aktivt.

## NeuraCell-X (radon + daggpunkt)

Prioritet: **radon > daggpunkt > normal**. Vid den första övergången till något
skydd sparar bryggan varje enhets aktuella läge/fläkt som en baslinje; när alla
skydd upphör utför den en **exakt återställning**.

- **Radonskydd** — utlöses när `radon/alarm=ON` eller `radon/value ≥
  RADON_THRESHOLD`. Alla enheter → `INTAKE` vid `LOW` (skonsamt friskluftsövertryck).
  Normala `/set`-kommandon undertrycks medan det är aktivt.
- **Daggpunktsstyrning** — utlöses när `dewpoint/block=ON`, eller
  automatiskt när daggpunkten **ute** ligger på/över daggpunkten inne
  (minus `DEWPOINT_MARGIN`), dvs. ventilation skulle tillföra fukt. Alla enheter →
  `OFF`. Behöver utomhusdata på `ambientika/weather`; utan det fungerar endast den
  manuella åsidosättningen. Daggpunkten inne beräknas från varje enhets temp+fukt
  (Magnus-formeln).

## Konfiguration (env)

| Var | Standard | Betydelse |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | mäklare |
| `MQTT_PREFIX` | `ambientika` | topic-prefix (behåll `ambientika` för att matcha appen) |
| `LOCAL_TCP_PORT` | `11000` | port som enheterna ansluter till |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | konfiguration som skickas vid anslutning |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | schemakörare |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon- och daggpunktsstyrning |
| `RADON_THRESHOLD` | `100` | Bq/m³ tröskel för automatisk utlösning `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatisk daggpunkt + °C-hysteres `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publicera Home Assistant-discovery (behövs inte av appen) |

## Verifieringsstatus

- ✅ Wire-codec byte-för-byte mot `PROTOCOL.md` (temperatur och RSSI avkodas
  **med tecken**).
- ✅ App-vokabulär-round-trip (lägesnamn, fanSpeed %, daggpunkt).
- ✅ Veckoschema: flankutlösare tillämpar tidsintervall en gång; annars ingen åtgärd; tider
  normaliseras till `HH:MM`.
- ✅ NeuraCell-X: radonprioritet, kommandoundertryckning, automatisk + manuell
  daggpunkt med **±marginalhysteres** och **exakt återställning** av läget före
  skyddet (baslinjen tas från det senaste normala målet, inte enhetens eko).
- ✅ Härdad samtidighet: ett enda lås serialiserar kommando/schema/NeuraCell,
  skyddstillståndet skrivs **innan** någon skyddande skrivning, enhetsloopar
  itererar över snapshots, skrivningar serialiseras per enhet.
- ✅ Robusthet: TCP-inramning omsynkroniseras efter en vilsen byte; felaktiga
  `weather`-payloads avvisas; återanslutning bevarar enhetstillståndet;
  radon-/väderindata äldre än `NC_INPUT_TTL` behandlas som okända; MQTT last-will +
  ren avstängning.
- ✅ Regressionssvit: **40 enhets-/integrationstester** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Fullständig end-to-end genom en **riktig MQTT-mäklare** med en simulerad enhet
  (`smoke_test.py`, 13/13): status + kommando + schema + radonskydd/-undertryckning/
  -återställning + daggpunkt + inramningsomsynk + avstängning.
- ✅ `docker compose config` giltig; inga molnuppgifter någonstans i stacken.
- ✅ paho-mqtt 2.x callback-API (VERSION2), 1.x-fallback bibehållen.
- ⛔️ **Ännu inte testat på riktig hårdvara** — reverse-engineerad binär +
  säkerhetsrelevant styrning. Validera på en fysisk enhet före produktion (särskilt
  den teckenförsedda temperatur-/RSSI-avkodningen).

## Godkännande före produktion `>>> CONTROL <<<` / `>>> MAPPING <<<`

Läges-/fläktmappningarna och radon-/daggpunktströsklarna & målvärdena är rimliga
standardvärden, inte certifierade. Låt granska dem mot produktspecifikationen och
justera dem i `ambientika_local_bridge.py` (två mappningstabeller + `Config`-styrfälten).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, trösklarna för fläkt-%→nivå,
`RADON_THRESHOLD` och `DEWPOINT_MARGIN` är värdena att bekräfta.
