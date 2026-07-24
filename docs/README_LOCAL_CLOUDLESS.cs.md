🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · **CS**

# Ambientika – 100% bezcloudový aplikační stack

Provozuje Ambientika Local App (FastAPI + PWA) **bez cloudu SUEDWIND a bez
internetu**. Jedinou změnou oproti výchozímu stacku je zdroj dat: MQTT bridge,
který se dotazuje cloudu, je nahrazen **lokální bridge**, jež komunikuje s
větracími jednotkami přímo přes jejich nativní raw-TCP protokol (port 11000).

Bridge nyní pokrývá kompletní sadu funkcí bez cloudu:

- monitorování a ovládání zařízení (režim, ventilátor, senzory, rosný bod)
- **provádění týdenního plánu** (Týdenní plán)
- **NeuraCell-X**: ochrana proti radonu (priorita) + **řízení podle rosného bodu
  (Řízení podle rosného bodu)**, s přesným obnovením předchozího režimu.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Backend local-app a PWA se používají **beze změny** — bridge publikuje stejná
témata a stejnou slovní zásobu polí, kterou aplikace očekává (přívětivé názvy režimů
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, a navíc `dewPoint`).

## Soubory, které přidat do kořenového adresáře repozitáře `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Spuštění

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Nasměrování jednotek na tohoto hostitele (nutné, jednorázově)

Jednotky se připojují k tomu hostiteli, který byl zapsán při provisioningu přes BLE:

1. **Opětovný provisioning přes BLE (preferováno):** do každé jednotky zapište `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>`.
2. **Statická trasa / DNAT:** přesměrujte `185.214.203.87/32` → na tohoto hostitele a přidejte
   IP alias, aby hostitel přijímal pakety pro cloudovou IP.

Podrobnosti v `CLOUD-INTEGRATION.md`.

## MQTT témata

| Téma | Směr | Význam |
|-------|-----|---------|
| `ambientika/<serial>/status` | ven | stav zařízení (JSON, slovník aplikace + `dewPoint`) |
| `ambientika/<serial>/availability` | ven | `online` / `offline` |
| `ambientika/<serial>/set` | dovnitř | příkaz `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | dovnitř | kompletní týdenní plán (z aplikace) |
| `ambientika/<serial>/schedule/<day>/set` | dovnitř | časové úseky jednoho dne |
| `ambientika/neuracell/state` | ven | živý stav NeuraCell-X (JSON) |
| `ambientika/radon/alarm` | dovnitř | `ON`/`OFF` — vynutí / zruší ochranu proti radonu |
| `ambientika/radon/value` | dovnitř | naměřená hodnota radonu (Bq/m³) — automaticky sepne při prahu |
| `ambientika/dewpoint/block` | dovnitř | `ON`/`OFF` — vynutí / uvolní blokování podle rosného bodu |
| `ambientika/weather` | dovnitř | `{"temperature": t, "humidity": rh}` VENKOVNÍ vzduch |

## Týdenní plán

Spouštění při změně (edge-triggered): jakmile se pro aktuální den v týdnu / čas stane
časový úsek aktivním, bridge použije jeho `mode` (+ `fanSpeed`, nebo ponechá aktuální
rychlost, pokud úsek žádnou nemá) přesně **jednou**, takže ruční změna uvnitř úseku není
přebíjena. Plán je pozastaven, dokud je aktivní ochrana NeuraCell-X.

## NeuraCell-X (radon + rosný bod)

Priorita: **radon > rosný bod > normální**. Při prvním přechodu do jakékoli
ochrany bridge uloží aktuální režim/ventilátor každé jednotky jako výchozí stav; jakmile
se všechny ochrany zruší, provede **přesné obnovení**.

- **Ochrana proti radonu** — sepne, když `radon/alarm=ON` nebo `radon/value ≥
  RADON_THRESHOLD`. Všechny jednotky → `INTAKE` na `LOW` (mírný přetlak čerstvého vzduchu).
  Běžné příkazy `/set` jsou během aktivity potlačeny.
- **Řízení podle rosného bodu (Řízení podle rosného bodu)** — sepne, když `dewpoint/block=ON`, nebo
  automaticky, když je **venkovní** rosný bod na úrovni vnitřního rosného bodu nebo nad ním
  (mínus `DEWPOINT_MARGIN`), tj. větrání by přidalo vlhkost. Všechny jednotky →
  `OFF`. Vyžaduje venkovní data na `ambientika/weather`; bez nich funguje pouze ruční
  přepsání. Vnitřní rosný bod se počítá z teploty a vlhkosti každé jednotky
  (Magnusův vzorec).

## Konfigurace (env)

| Proměnná | Výchozí | Význam |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | prefix tématu (ponechte `ambientika` kvůli shodě s aplikací) |
| `LOCAL_TCP_PORT` | `11000` | port, ke kterému se jednotky připojují |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | nastavení odeslané při připojení |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | vykonavatel plánu |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | řídicí jednotka radonu + rosného bodu |
| `RADON_THRESHOLD` | `100` | práh automatického sepnutí v Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatický rosný bod + hystereze v °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publikování discovery pro Home Assistant (aplikace jej nevyžaduje) |

## Stav ověření

- ✅ Bajtově přesný kodek přenosu podle `PROTOCOL.md` (teplota a RSSI dekódovány
  **jako signed**).
- ✅ Obousměrný převod slovníku aplikace (názvy režimů, fanSpeed %, rosný bod).
- ✅ Týdenní plán: spouštění při změně použije úseky jednou; jinak bez akce; časy
  normalizovány na `HH:MM`.
- ✅ NeuraCell-X: priorita radonu, potlačení příkazů, automatický i ruční rosný bod
  s **hysterezí ±margin**, a **přesné obnovení** režimu před ochranou
  (výchozí stav se bere z posledního běžného cíle, nikoli z odezvy zařízení).
- ✅ Odolnost vůči souběhu: jediný zámek serializuje příkaz/plán/NeuraCell,
  stav ochrany je potvrzen **před** jakýmkoli ochranným zápisem, smyčky zařízení
  iterují nad snímky, zápisy jsou serializovány po jednotlivých zařízeních.
- ✅ Robustnost: rámcování TCP se po zbloudilém bajtu znovu synchronizuje; poškozené
  payloady `weather` jsou odmítnuty; opětovné připojení zachová stav zařízení; vstupy
  radon/weather starší než `NC_INPUT_TTL` jsou považovány za neznámé; MQTT last-will + čisté ukončení.
- ✅ Regresní sada: **40 unit/integračních testů** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Kompletní end-to-end přes **skutečný MQTT broker** se simulovanou jednotkou
  (`smoke_test.py`, 13/13): stav + příkaz + plán + ochrana proti radonu/potlačení/
  obnovení + rosný bod + resynchronizace rámcování + ukončení.
- ✅ `docker compose config` je platné; nikde ve stacku nejsou žádné cloudové přihlašovací údaje.
- ✅ paho-mqtt 2.x callback API (VERSION2), zachován fallback pro 1.x.
- ⛔️ **Zatím netestováno na reálném hardwaru** — reverzně zpětně analyzovaný binární
  protokol + řízení relevantní pro bezpečnost. Před nasazením do produkce ověřte na jedné
  fyzické jednotce (zejména dekódování teploty/RSSI jako signed).

## Schválení před nasazením do produkce `>>> CONTROL <<<` / `>>> MAPPING <<<`

Mapování režim/ventilátor a prahy & cílové hodnoty radonu/rosného bodu jsou rozumné
výchozí hodnoty, nikoli certifikované. Nechte je zkontrolovat oproti specifikaci produktu a doladit v
`ambientika_local_bridge.py` (dvě mapovací tabulky + řídicí pole `Config`).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, prahy pro převod
% ventilátoru → stupeň, `RADON_THRESHOLD` a `DEWPOINT_MARGIN` jsou hodnoty, které je třeba potvrdit.
