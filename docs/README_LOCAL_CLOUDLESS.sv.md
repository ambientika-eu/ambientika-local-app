🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · **SV** · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – lokalt driftläge utan server

> **Placering.** Det rekommenderade driftläget för Local App är molnbryggan
> (`docker-compose.yml`): beprövad i fält och avsedd för normal drift. Det lokala
> driftläge som beskrivs här är utformat som en **reservnivå**. Det håller anläggningen
> användbar när Ambientika-servern inte kan nås — vid underhållsfönster, nätstörningar
> eller i byggnader där permanent internetanslutning inte är avsedd. Det ersätter inte
> standardläget.

I detta läge driver Ambientika Local App (FastAPI + PWA) anläggningen **utan
Ambientika-server och utan internetanslutning**. Jämfört med standardläget ändras
endast enhetsanslutningen: i stället för bryggan som frågar servern används en **lokal
brygga** som kommunicerar med ventilationsaggregaten direkt i hemnätet via deras
nativa TCP-protokoll (port 11000).

```
Standardläge:  Aggregat → Ambientika-server → molnbrygga → MQTT → app
Lokalt läge:   Aggregat → lokal brygga (TCP 11000) → MQTT → app     ← utan server
```

Hela funktionsomfånget behålls:

- enhetsövervakning och styrning (läge, fläkt, sensorer, daggpunkt)
- **utförande av veckoschemat**
- **NeuraCell-X**: radonskydd (prioritet) och **daggpunktsstyrning**, med exakt
  återställning av det tidigare aktiva läget

Local App:s backend och PWA används **oförändrade** — den lokala bryggan publicerar
samma topics och samma fältvokabulär som standardläget (lägesnamn
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int, `filterAlarm`
bool, samt `dewPoint`).

## Komponenter

Endast enhetsanslutningen byts ut; app och styrlogik förblir desamma.

```
docker-compose.local.yml          # stack utan serverfrågor
Dockerfile.bridge                 # image för den lokala bryggan
ambientika_local_bridge.py        # lokal brygga (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # konfiguration för lokal broker
env.local.example.txt             # konfigurationsmall (utan serveruppgifter)
```

## Kör

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Rikta enheterna mot den här värden (obligatoriskt, engångs)

Enheterna ansluter till den värd som skrevs in vid BLE-provisioneringen:

1. **BLE-omprovisionering:** skriv `H_<host-ip>:11000`, `S_<ssid>`,
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
| `SEND_SETUP` | `false` | aktivera uttryckligen skrivning av enhetstopologin vid anslutning; kontrollera först alla värden nedan |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | konfigurationsvärden som endast används med `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | schemakörare |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon- och daggpunktsstyrning |
| `RADON_THRESHOLD` | `100` | Bq/m³ tröskel för automatisk utlösning |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatisk daggpunkt + °C-hysteres |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publicera Home Assistant-discovery (behövs inte av appen) |

## Kvalitetssäkring

- ✅ Wire-codec byte-för-byte mot die Protokollspezifikation (temperatur och RSSI avkodas
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

## Parametrering

Läges- och fläktmappningar samt tröskelvärdena för radon- och daggpunktsskydd är
förinställda med tillämpningssäkra standardvärden och kan anpassas per projekt i
`ambientika_local_bridge.py` (två mappningstabeller och `Config`-fälten):
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, trösklarna för
fläktsteg samt `RADON_THRESHOLD` och `DEWPOINT_MARGIN`. Objektsspecifika gränsvärden —
isärskilt myndighetsföreskrivna radontrösklar — ska sättas vid idrifttagning.

## Frisläppningsstatus

Det lokala driftläget tillhandahålls som en **kontrollerad leverans (observationsläge)**:
fältvalideringen över hela det levererade firmwarebeståndet är ännu inte avslutad, och
återkoppling från installationer arbetas löpande in i frisläppningen. För normal drift
är molnbryggan fortsatt den rekommenderade varianten; det lokala läget är reservnivån
för det fall servern inte kan nås. Återkoppling lämnas gärna via detta repositorys
issues.
