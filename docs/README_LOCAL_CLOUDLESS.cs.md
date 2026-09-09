🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · **CS**

# Ambientika Local App – lokální provozní režim bez serveru

> **Zařazení.** Doporučeným provozním režimem Local App je cloudový most
> (`docker-compose.yml`): ověřený v praxi a určený pro běžný provoz. Zde popsaný
> lokální režim je navržen jako **záložní úroveň**. Udržuje zařízení ovladatelné v
> případě, že server Ambientika není dostupný — při servisních oknech, výpadcích sítě
> nebo v objektech, kde se trvalé připojení k internetu nepředpokládá. Standardní režim
> nenahrazuje.

V tomto režimu Ambientika Local App (FastAPI + PWA) provozuje zařízení **bez serveru
Ambientika a bez připojení k internetu**. Oproti standardnímu režimu se mění pouze
připojení k jednotkám: místo mostu, který se dotazuje serveru, nastupuje **lokální
most**, který komunikuje s větracími jednotkami přímo v domácí síti prostřednictvím
jejich nativního protokolu TCP (port 11000).

```
Standardní režim:  Jednotka → server Ambientika → cloudový most → MQTT → app
Lokální režim:     Jednotka → lokální most (TCP 11000) → MQTT → app  ← bez serveru
```

Celý rozsah funkcí zůstává zachován:

- monitorování a řízení jednotek (režim, ventilátor, senzory, rosný bod)
- **provádění týdenního programu**
- **NeuraCell-X**: ochrana před radonem (prioritní) a **řízení rosného bodu**, s
  přesným obnovením předchozího aktivního režimu

Backend Local App a PWA se používají **beze změny** — lokální most publikuje stejná
témata a stejný slovník polí jako standardní režim (názvy režimů
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int, `filterAlarm`
bool, navíc `dewPoint`).

## Komponenty

Vyměňuje se výhradně připojení k jednotkám; aplikace a řídící logika zůstávají stejné.

```
docker-compose.local.yml          # stack bez dotazování serveru
Dockerfile.bridge                 # image lokálního mostu
ambientika_local_bridge.py        # lokální most (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # konfigurace lokálního brokeru
env.local.example.txt             # šablona konfigurace (bez přístupových údajů serveru)
```

## Spuštění

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Nasměrování jednotek na tohoto hostitele (nutné, jednorázově)

Jednotky se připojují k tomu hostiteli, který byl zapsán při provisioningu přes BLE:

1. **Opětovný provisioning přes BLE:** do každé jednotky zapište `H_<host-ip>:11000`, `S_<ssid>`,
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
| `RADON_THRESHOLD` | `100` | práh automatického sepnutí v Bq/m³ |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatický rosný bod + hystereze v °C |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publikování discovery pro Home Assistant (aplikace jej nevyžaduje) |

## Zajištění kvality

- ✅ Bajtově přesný kodek přenosu podle die Protokollspezifikation (teplota a RSSI dekódovány
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

## Parametrizace

Přiřazení režimů a ventilátoru i prahové hodnoty ochrany před radonem a rosným bodem
jsou přednastaveny bezpečnými výchozími hodnotami a lze je přizpůsobit podle projektu
v `ambientika_local_bridge.py` (dvě přiřazovací tabulky a pole `Config`):
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, prahy stupňů
ventilátoru a `RADON_THRESHOLD` a `DEWPOINT_MARGIN`. Limitní hodnoty specifické pro
objekt — zejména úředně stanovené prahy radonu — je třeba nastavit při uvedení do
provozu.

## Stav vydání

Lokální provozní režim je poskytován jako **řízené vydání (režim pozorování)**:
validace v provozu napříč celým dodaným firmwarovým portfoliem ještě není dokončena a
zpětná vazba z instalací je průběžně zapracovávána. Pro běžný provoz zůstává
doporučenou variantou cloudový most; lokální režim je záložní úrovní pro případ, že
server není dostupný. Zpětnou vazbu prosím zasílejte přes issues tohoto repozitáře.
