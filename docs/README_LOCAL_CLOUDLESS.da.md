🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · **DA** · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – 100% cloudfri app-stack

Kører Ambientika Local App (FastAPI + PWA) **uden SUEDWIND-cloud og uden
internet**. Den eneste ændring i forhold til upstream-stacken er datakilden: den
cloud-forespørgende MQTT-bridge er erstattet af en **lokal bridge**, som
kommunikerer med ventilationsenhederne direkte via deres native raw-TCP-protokol
(port 11000).

Bridgen dækker nu hele funktionssættet cloudfrit:

- enhedsovervågning + styring (tilstand, ventilator, sensorer, dugpunkt)
- **udførelse af ugeskema** (Ugeskema)
- **NeuraCell-X**: radonbeskyttelse (prioritet) + **dugpunktsstyring**, med
  nøjagtig gendannelse af den forrige tilstand.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Backend'en i local-app og PWA'en anvendes **uændret** — bridgen publicerer de
samme topics og det samme feltvokabular, som appen forventer (letforståelige
tilstandsnavne `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality`
int, `filterAlarm` bool, plus `dewPoint`).

## Filer, der skal føjes til roden af `ambientika-local-app`-repoet

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Kør

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Ret enhederne mod denne host (påkrævet, engangs)

Enhederne forbinder til den host, der blev skrevet under BLE-provisioning:

1. **BLE-genprovisionering (foretrukket):** skriv `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` til hver enhed.
2. **Statisk rute / DNAT:** omdiriger `185.214.203.87/32` → denne host og tilføj et
   IP-alias, så host'en accepterer pakker til cloud-IP'en.

Detaljer i `CLOUD-INTEGRATION.md`.

## MQTT-topics

| Topic | Retn. | Betydning |
|-------|-----|---------|
| `ambientika/<serial>/status` | ud | enhedstilstand (JSON, app-vokabular + `dewPoint`) |
| `ambientika/<serial>/availability` | ud | `online` / `offline` |
| `ambientika/<serial>/set` | ind | `{mode, fanSpeed, ...}` kommando |
| `ambientika/<serial>/schedule/set` | ind | fuldt ugeskema (fra appen) |
| `ambientika/<serial>/schedule/<day>/set` | ind | tidsrum for én dag |
| `ambientika/neuracell/state` | ud | NeuraCell-X live-status (JSON) |
| `ambientika/radon/alarm` | ind | `ON`/`OFF` — gennemtving / ophæv radonbeskyttelse |
| `ambientika/radon/value` | ind | radonaflæsning (Bq/m³) — udløser automatisk ved tærskel |
| `ambientika/dewpoint/block` | ind | `ON`/`OFF` — gennemtving / frigiv dugpunktsspærring |
| `ambientika/weather` | ind | `{"temperature": t, "humidity": rh}` UDENDØRS luft |

## Ugeskema

Flankeudløst: Når et tidsrum bliver aktivt for den aktuelle ugedag/tid, anvender
bridgen dets `mode` (+ `fanSpeed`, eller beholder den aktuelle hastighed, hvis
tidsrummet ikke har nogen) præcis **én gang**, så en manuel ændring inden i et
tidsrum ikke modarbejdes. Ugeskemaet suspenderes, mens en NeuraCell-X-beskyttelse
er aktiv.

## NeuraCell-X (radon + dugpunkt)

Prioritet: **radon > dugpunkt > normal**. Ved den første overgang til en hvilken
som helst beskyttelse gemmer bridgen hver enheds aktuelle tilstand/ventilator som
udgangspunkt; når alle beskyttelser ophæves, udfører den en **nøjagtig
gendannelse**.

- **Radonbeskyttelse** — udløses, når `radon/alarm=ON` eller `radon/value ≥
  RADON_THRESHOLD`. Alle enheder → `INTAKE` ved `LOW` (blidt friskluft-overtryk).
  Normale `/set`-kommandoer undertrykkes, mens den er aktiv.
- **Dugpunktsstyring** — udløses, når `dewpoint/block=ON`, eller
  automatisk, når det **udendørs** dugpunkt er på/over det indendørs dugpunkt
  (minus `DEWPOINT_MARGIN`), dvs. når ventilation ville tilføje fugt. Alle enheder →
  `OFF`. Kræver udendørsdata på `ambientika/weather`; uden dem virker kun den
  manuelle tilsidesættelse. Det indendørs dugpunkt beregnes ud fra hver enheds
  temperatur+fugt (Magnus-formlen).

## Konfiguration (env)

| Var | Standard | Betydning |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | topic-præfiks (behold `ambientika` for at matche appen) |
| `LOCAL_TCP_PORT` | `11000` | port, som enhederne forbinder til |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | opsætning sendt ved tilslutning |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | skema-udfører |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon+dugpunkt-controller |
| `RADON_THRESHOLD` | `100` | Bq/m³ tærskel for automatisk udløsning `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatisk dugpunkt + °C-hysterese `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publicér Home Assistant discovery (ikke nødvendig for appen) |

## Verifikationsstatus

- ✅ Wire-codec byte-for-byte mod `PROTOCOL.md` (temperatur & RSSI afkodet
  **fortegnsbehæftet**).
- ✅ App-vokabular-round-trip (tilstandsnavne, fanSpeed %, dugpunkt).
- ✅ Ugeskema: flankeudløser anvender tidsrum én gang; ellers ingen handling; tider
  normaliseret til `HH:MM`.
- ✅ NeuraCell-X: radonprioritet, kommandoundertrykkelse, automatisk + manuel
  dugpunkt med **±margin-hysterese** og **nøjagtig gendannelse** af tilstanden før
  beskyttelsen (udgangspunkt taget fra det seneste normale mål, ikke enhedens ekko).
- ✅ Samtidighed hærdet: en enkelt lås serialiserer kommando/skema/NeuraCell,
  beskyttelsestilstanden fastlægges **før** enhver beskyttende skrivning,
  enhedsløkker itererer over snapshots, skrivninger serialiseres pr. enhed.
- ✅ Robusthed: TCP-framing resynkroniserer efter en vildfaren byte; fejlformede
  `weather`-payloads afvises; genforbindelse bevarer enhedstilstanden;
  radon-/vejrinput ældre end `NC_INPUT_TTL` behandles som ukendt; MQTT last-will +
  ren nedlukning.
- ✅ Regressionssuite: **40 unit-/integrationstest** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Fuld end-to-end gennem en **rigtig MQTT-broker** med en simuleret enhed
  (`smoke_test.py`, 13/13): status + kommando + skema + radonbeskyttelse/-undertrykkelse/
  -gendannelse + dugpunkt + framing-resync + nedlukning.
- ✅ `docker compose config` gyldig; ingen cloud-legitimationsoplysninger nogen steder i stacken.
- ✅ paho-mqtt 2.x callback-API (VERSION2), 1.x-fallback bevaret.
- ⛔️ **Endnu ikke testet på rigtig hardware** — reverse-engineeret binærprotokol +
  sikkerhedsrelevant styring. Validér på én fysisk enhed før produktion (især den
  fortegnsbehæftede temperatur-/RSSI-afkodning).

## Frigivelse før produktion `>>> CONTROL <<<` / `>>> MAPPING <<<`

Tilstands-/ventilator-mappings samt radon-/dugpunkt-tærskler & -mål er fornuftige
standardværdier, ikke certificerede. Få dem gennemgået mod produktspecifikationen og
afstemt i `ambientika_local_bridge.py` (to mapping-tabeller + `Config`-styrefelterne).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, ventilator-%→trin-
tærsklerne, `RADON_THRESHOLD` og `DEWPOINT_MARGIN` er de værdier, der skal bekræftes.
