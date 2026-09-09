🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · **DA** · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – lokal driftstilstand uden server

> **Placering.** Den anbefalede driftstilstand for Local App er cloud-bridgen
> (`docker-compose.yml`): afprøvet i marken og beregnet til normal drift. Den lokale
> driftstilstand, der beskrives her, er udformet som et **reserveniveau**. Den holder
> anlægget betjeningsklart, når Ambientika-serveren ikke kan nås — ved
> vedligeholdelsesvinduer, netværksforstyrrelser eller i bygninger, hvor en permanent
> internetforbindelse ikke er forudsat. Den erstatter ikke standardtilstanden.

I denne tilstand driver Ambientika Local App (FastAPI + PWA) anlægget **uden
Ambientika-server og uden internetforbindelse**. I forhold til standardtilstanden
ændres udelukkende enhedsforbindelsen: i stedet for bridgen, der spørger serveren,
indtræder en **lokal bridge**, som taler med ventilationsaggregaterne direkte i
hjemmenetværket via deres native TCP-protokol (port 11000).

```
Standardtilstand:  Aggregat → Ambientika-server → cloud-bridge → MQTT → app
Lokal tilstand:    Aggregat → lokal bridge (TCP 11000) → MQTT → app   ← uden server
```

Hele funktionsomfanget bevares:

- enheds overvågning og styring (tilstand, ventilator, sensorer, dugpunkt)
- **udførelse af ugeprogrammet**
- **NeuraCell-X**: radonbeskyttelse (prioritet) og **dugpunktsstyring**, med nøjagtig
  genskabelse af den tidligere aktive tilstand

Local Apps backend og PWA anvendes **uændret** — den lokale bridge publicerer de samme
topics og det samme feltvokabular som standardtilstanden (tilstandsnavne
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int, `filterAlarm`
bool, samt `dewPoint`).

## Komponenter

Kun enhedsforbindelsen udskiftes; app og styringslogik forbliver de samme.

```
docker-compose.local.yml          # stack uden serverforespørgsler
Dockerfile.bridge                 # image til den lokale bridge
ambientika_local_bridge.py        # lokal bridge (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # konfiguration af den lokale broker
env.local.example.txt             # konfigurationsskabelon (uden serveradgangsdata)
```

## Kør

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Ret enhederne mod denne host (påkrævet, engangs)

Enhederne forbinder til den host, der blev skrevet under BLE-provisioning:

1. **BLE-genprovisionering:** skriv `H_<host-ip>:11000`, `S_<ssid>`,
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
| `RADON_THRESHOLD` | `100` | Bq/m³ tærskel for automatisk udløsning |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatisk dugpunkt + °C-hysterese |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publicér Home Assistant discovery (ikke nødvendig for appen) |

## Kvalitetssikring

- ✅ Wire-codec byte-for-byte mod die Protokollspezifikation (temperatur & RSSI afkodet
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

## Parametrering

Tilstands- og ventilatortilknytninger samt grænseværdierne for radon- og
dugpunktsbeskyttelse er forudindstillet med anvendelsessikre standardværdier og kan
tilpasses projektspecifikt i `ambientika_local_bridge.py` (to tilknytningstabeller og
`Config`-felterne): `BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`,
grænserne for ventilatortrin samt `RADON_THRESHOLD` og `DEWPOINT_MARGIN`.
Bygningsspecifikke grænseværdier — især myndighedsfastsatte radongrænser — skal
sættes ved idriftsættelsen.

## Frigivelsesstatus

Den lokale driftstilstand stilles til rådighed som en **kontrolleret levering
(observationstilstand)**: feltvalideringen på tværs af hele den leverede
firmwarebestand er endnu ikke afsluttet, og tilbagemeldinger fra installationer
indarbejdes løbende i frigivelsen. Til normal drift forbliver cloud-bridgen den
anbefalede variant; den lokale tilstand er reserveniveauet, hvis serveren ikke kan
nås. Tilbagemeldinger modtages gerne via dette repositorys issues.
