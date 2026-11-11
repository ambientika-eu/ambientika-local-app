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

Varje återställning **bekräftas av enhetens nästa status**. Visar den fortfarande ett skyddsläge (AV / tilluft) — för att den var offline vid frigivningen eller anslutningen föll bort tyst — skickas återställningen igen (högst 3 gånger, minst 20 s mellan). Visar den ett annat läge har någon ändrat det under tiden och det gäller. Med `STATE_FILE` överlever det en **omstart av bryggan**: ett aktivt skydd förblir aktivt tills den utlösande indatan rapporterar igen, högst `NC_INPUT_TTL`.

- **Radonskydd** — utlöses när `radon/alarm=ON` eller `radon/value ≥
  RADON_THRESHOLD`. Alla enheter → `INTAKE` vid `LOW` (skonsamt friskluftsövertryck).
  Normala `/set`-kommandon undertrycks medan det är aktivt.
- **Daggpunktsstyrning** — utlöses när `dewpoint/block=ON`, eller
  automatiskt när daggpunkten **ute** ligger på/över daggpunkten inne
  (minus `DEWPOINT_MARGIN`), dvs. ventilation skulle tillföra fukt. Alla enheter →
  `OFF`. Behöver utomhusdata på `ambientika/weather`; utan det fungerar endast den
  manuella åsidosättningen. Daggpunkten inne beräknas från varje enhets temp+fukt
  (Magnus-formeln).

## Home Assistant: add-on-profil

Med `HA_DISCOVERY_PROFILE=addon` erbjuder den lokala bryggan enheterna i Home Assistant exakt som
add-onen **Ambientika MQTT Bridge**: samma discovery-topics och `unique_id`, samma state-topic
`ambientika/<serienr>/state` med samma nycklar och värden (`Smart`, `AwayHome`,
`ManualHeatRecovery`, `Intake` …, `Low` / `Medium` / `High`), samma kommando-topics
`ambientika/<serienr>/set/<attribut>` och knappen för filteråterställning. Home Assistant behåller
entitets-ID:na: automationer, skript och instrumentpaneler från add-onen fungerar oförändrat efter
övergången till lokal drift.

1. Stoppa add-onen — på en broker körs antingen add-onen eller den lokala bryggan.
2. Ställ `MQTT_BROKER` / `MQTT_PORT` / `MQTT_USER` / `MQTT_PASSWORD` på Home Assistants broker.
3. Ställ `HA_DISCOVERY_PROFILE=addon` och, för att behålla rumsnamnen, `ADDON_DEVICE_NAMES={"<SERIENR>": "<rum>"}`.
4. Dirigera om enheterna till denna värd (se ovan).

Kommandon som kommer till en enhet inom 0,8 s skickas till den som ett kommando. Skillnader mot
add-onen: en kopplad slav visar sitt eget driftläge (add-onen visar masterns); "Failed Polls"
räknar enhetens tappade anslutningar; NeuraCell-X använder den lokala bryggans ingångar
(`ambientika/radon/…`, `ambientika/dewpoint/block`, `ambientika/weather`) och publicerar sitt
tillstånd för Home Assistant på `ambientika/neuracell/addon_state`; diagnossensorn "Filter Reset
Status" erbjuds inte.

## Konfiguration (env)

| Var | Standard | Betydelse |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | mäklare |
| `MQTT_PREFIX` | `ambientika` | topic-prefix (behåll `ambientika` för att matcha appen) |
| `LOCAL_TCP_PORT` | `11000` | port som enheterna ansluter till |
| `SETUP_DEVICES` | — | inställning per enhet; **utan post skrivs ingen inställning** — roller och zoner från den officiella appen lämnas orörda. JSON: `{"<SERIAL>": {"role": 0, "zone": 0, "house": <HOUSE-ID>}}` — roll, zon (0–15) och hus är obligatoriska |
| `SETUP_ALLOW_ROLE_CHANGE` | `false` | en inställning som skulle ändra den rapporterade master/slav-rollen avvisas utan `true` |
| `SEND_SETUP` | `false` | gammal global inställning, endast för **en enhet**; ändrar en rapporterad roll endast med `SETUP_ALLOW_ROLE_CHANGE=true` |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | mål för den gamla globala inställningen (`SEND_SETUP=true`) |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | schemakörare |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon- och daggpunktsstyrning |
| `RADON_THRESHOLD` | `100` | Bq/m³ tröskel för automatisk utlösning |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatisk daggpunkt + °C-hysteres |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `STATE_FILE` | — | fil för basvärden / väntande återställningar (compose: `/data/local_bridge_state.json`); tom = endast minne |
| `PENDING_RESTORE_TTL` | `86400` | hur länge en enhet som var offline vid frigivning fortfarande återställs (s) |
| `HA_DISCOVERY` | `false` | publicera Home Assistant-discovery (behövs inte av appen) |
| `HA_DISCOVERY_PROFILE` | `local` | `addon`: entiteter, värden och kommandon från Home Assistant-add-onen (slår på discovery) |
| `ADDON_DEVICE_NAMES` | *(tom)* | enhetsnamn för add-on-profilen, JSON `{"<SERIENR>": "<namn>"}` |
| `DISCOVERY_PREFIX` | `homeassistant` | discovery-prefix för Home Assistant |
| `AVERAGE_WINDOW_MIN` | `30` | fönster för medelvärdesbildad temperatur / fukt (minuter) |
| `MQTT_USER` / `MQTT_PASSWORD` | — | inloggning till brokern |

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
- ✅ Säkerhet: inställning endast per enhet, aldrig en tyst ändring av master/slav-rollen; endast bekräftade koder; inga väderdata; ramar på 19 och 21 byte.
- ✅ Regressionssvit: **220 enhets-/integrationstester** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`, `test_addon_profile.py`).
- ✅ Fullständig end-to-end genom en **riktig MQTT-mäklare** med en simulerad enhet
  (`smoke_test.py`, 12/13 — check 8 "offline after shutdown" open): status + kommando + schema + radonskydd/-undertryckning/
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
