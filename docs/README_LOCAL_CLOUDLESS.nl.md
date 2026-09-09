🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · **NL** · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – lokale bedrijfsmodus zonder server

> **Positionering.** De aanbevolen bedrijfsmodus van de Local App is de cloudbridge
> (`docker-compose.yml`): beproefd in het veld en bedoeld voor de reguliere werking.
> De hier beschreven lokale bedrijfsmodus is opgezet als **terugvalniveau**. Hij houdt
> de installatie bedienbaar wanneer de Ambientika-server niet bereikbaar is — bij
> onderhoudsvensters, netwerkstoringen of in gebouwen waar een permanente
> internetverbinding niet is voorzien. Hij vervangt de standaardmodus niet.

In deze modus draait de Ambientika Local App (FastAPI + PWA) de installatie **zonder
Ambientika-server en zonder internetverbinding**. Ten opzichte van de standaardmodus
verandert uitsluitend de apparaatkoppeling: in plaats van de bridge die de server
bevraagt komt een **lokale bridge** die de ventilatie-units rechtstreeks in het
thuisnetwerk aanspreekt via hun native TCP-protocol (poort 11000).

```
Standaardmodus:  Unit → Ambientika-server → cloudbridge → MQTT → app
Lokale modus:    Unit → lokale bridge (TCP 11000) → MQTT → app      ← zonder server
```

De volledige functieomvang blijft behouden:

- apparaatbewaking en -besturing (modus, ventilator, sensoren, dauwpunt)
- **uitvoering van het weekprogramma**
- **NeuraCell-X**: radonbescherming (prioriteit) en **dauwpuntsturing**, met exact
  herstel van de eerder actieve modus

De backend van de Local App en de PWA worden **ongewijzigd** gebruikt — de lokale
bridge publiceert dezelfde topics en hetzelfde veldvocabulaire als de standaardmodus
(modusnamen `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int,
`filterAlarm` bool, plus `dewPoint`).

## Componenten

Alleen de apparaatkoppeling wordt vervangen; app en besturingslogica blijven gelijk.

```
docker-compose.local.yml          # stack zonder serverbevraging
Dockerfile.bridge                 # image van de lokale bridge
ambientika_local_bridge.py        # lokale bridge (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # configuratie van de lokale broker
env.local.example.txt             # configuratiesjabloon (zonder servergegevens)
```

## Uitvoeren

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Richt de toestellen op deze host (vereist, eenmalig)

De toestellen verbinden met de host die tijdens de BLE-provisioning is weggeschreven:

1. **BLE-herprovisioning:** schrijf `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` naar elk toestel.
2. **Statische route / DNAT:** stuur `185.214.203.87/32` → deze host door en voeg een
   IP-alias toe zodat de host pakketten voor de cloud-IP accepteert.

Details in `CLOUD-INTEGRATION.md`.

## MQTT-topics

| Topic | Dir | Betekenis |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | apparaatstatus (JSON, app-woordenschat + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | `{mode, fanSpeed, ...}` commando |
| `ambientika/<serial>/schedule/set` | in | volledig weekschema (vanuit de app) |
| `ambientika/<serial>/schedule/<day>/set` | in | tijdvakken van één dag |
| `ambientika/neuracell/state` | out | NeuraCell-X live-status (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — radonbescherming forceren / opheffen |
| `ambientika/radon/value` | in | radonmeetwaarde (Bq/m³) — activeert automatisch bij drempel |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — dauwpuntblokkering forceren / vrijgeven |
| `ambientika/weather` | in | `{"temperature": t, "humidity": rh}` BUITEN-lucht |

## Weekschema

Edge-getriggerd: wanneer een tijdvak actief wordt voor de huidige weekdag/tijd, past de
bridge zijn `mode` toe (+ `fanSpeed`, of behoudt de huidige snelheid als het tijdvak
er geen heeft) exact **eenmaal**, zodat een handmatige wijziging binnen een tijdvak niet
wordt tegengewerkt. Het schema wordt opgeschort zolang een NeuraCell-X-bescherming actief is.

## NeuraCell-X (radon + dauwpunt)

Prioriteit: **radon > dauwpunt > normaal**. Bij de eerste overgang naar een
bescherming slaat de bridge de huidige modus/ventilator van elk toestel op als basislijn; wanneer alle
beschermingen zijn opgeheven, voert hij een **exact herstel** uit.

- **Radonbescherming** — activeert wanneer `radon/alarm=ON` of `radon/value ≥
  RADON_THRESHOLD`. Alle toestellen → `INTAKE` op `LOW` (lichte verse-lucht-overdruk).
  Normale `/set`-commando's worden onderdrukt zolang actief.
- **Dauwpuntregeling (Taupunktsteuerung)** — activeert wanneer `dewpoint/block=ON`, of
  automatisch wanneer het **buiten**dauwpunt gelijk aan/hoger dan het binnendauwpunt is
  (minus `DEWPOINT_MARGIN`), d.w.z. ventileren zou vocht toevoegen. Alle toestellen →
  `OFF`. Vereist buitengegevens op `ambientika/weather`; zonder deze werkt alleen de handmatige
  override. Het binnendauwpunt wordt berekend uit temp+vocht van elk toestel
  (Magnus-formule).

## Configuratie (env)

| Var | Default | Betekenis |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | topic-prefix (houd `ambientika` aan om bij de app te passen) |
| `LOCAL_TCP_PORT` | `11000` | poort waarmee de toestellen verbinden |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | setup verstuurd bij verbinden |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | schema-uitvoerder |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon+dauwpunt-controller |
| `RADON_THRESHOLD` | `100` | Bq/m³ auto-activeringsdrempel |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | auto-dauwpunt + °C-hysterese |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | Home Assistant discovery publiceren (niet nodig voor de app) |

## Kwaliteitsborging

- ✅ Wire-codec byte-voor-byte tegen die Protokollspezifikation (temperatuur & RSSI gedecodeerd
  **signed**).
- ✅ App-woordenschat round-trip (modusnamen, fanSpeed %, dauwpunt).
- ✅ Weekschema: edge-trigger past tijdvakken eenmaal toe; anders no-op; tijden
  genormaliseerd naar `HH:MM`.
- ✅ NeuraCell-X: radonprioriteit, commando-onderdrukking, auto + handmatig dauwpunt
  met **±margin-hysterese**, en **exact herstel** van de pre-beschermingsmodus
  (basislijn genomen van het laatste normale doel, niet de device-echo).
- ✅ Concurrency gehard: één enkele lock serialiseert command/schedule/NeuraCell,
  de beschermingsstatus wordt vastgelegd **voordat** er beschermend wordt geschreven, device-loops
  itereren over snapshots, writes zijn per apparaat geserialiseerd.
- ✅ Robuustheid: TCP-framing hersynchroniseert na een verdwaalde byte; misvormde `weather`-
  payloads geweigerd; reconnect behoudt de apparaatstatus; radon/weather-invoer
  ouder dan `NC_INPUT_TTL` als onbekend behandeld; MQTT last-will + nette afsluiting.
- ✅ Regressiesuite: **40 unit-/integratietests** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Volledig end-to-end via een **echte MQTT-broker** met een gesimuleerd toestel
  (`smoke_test.py`, 13/13): status + command + schedule + radon protect/suppress/
  restore + dauwpunt + framing-resync + shutdown.
- ✅ `docker compose config` geldig; nergens cloud-inloggegevens in de stack.
- ✅ paho-mqtt 2.x callback-API (VERSION2), 1.x-fallback behouden.

## Parametrering

Modus- en ventilatortoewijzingen evenals de drempelwaarden voor radon- en
dauwpuntbescherming zijn voorzien van toepassingsveilige standaardwaarden en zijn per
project aanpasbaar in `ambientika_local_bridge.py` (twee toewijzingstabellen en de
`Config`-velden): `BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, de
drempels van de ventilatorstanden en `RADON_THRESHOLD` en `DEWPOINT_MARGIN`.
Gebouwspecifieke grenswaarden — in het bijzonder door de overheid voorgeschreven
radondrempels — moeten bij de inbedrijfstelling worden ingesteld.

## Vrijgavestatus

De lokale bedrijfsmodus wordt geleverd als **gecontroleerde uitlevering
(observatiemodus)**: de veldvalidatie over het volledige uitgeleverde firmwarebestand
is nog niet afgerond en terugkoppeling uit installaties wordt doorlopend in de
vrijgave verwerkt. Voor de reguliere werking blijft de cloudbridge de aanbevolen
variant; de lokale modus is het terugvalniveau voor het geval de server niet
bereikbaar is. Terugkoppeling graag via de issues van deze repository.
