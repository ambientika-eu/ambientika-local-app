🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · **PL** · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – stos aplikacji w 100% bez chmury

Uruchamia Ambientika Local App (FastAPI + PWA) **bez chmury SUEDWIND i bez
internetu**. Jedyna zmiana względem oryginalnego stosu to źródło danych: odpytujący
chmurę MQTT Bridge zostaje zastąpiony przez **lokalny Bridge**, który komunikuje się z
urządzeniami wentylacyjnymi bezpośrednio przez ich natywny surowy protokół TCP (port 11000).

Bridge obejmuje teraz pełen zestaw funkcji bez chmury:

- monitorowanie i sterowanie urządzeniami (tryb, wentylator, czujniki, punkt rosy)
- **wykonywanie harmonogramu tygodniowego** (Wochenzeitplan)
- **NeuraCell-X**: ochrona przed radonem (priorytet) + **sterowanie punktem rosy
  (Taupunktsteuerung)**, z dokładnym przywróceniem poprzedniego trybu.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Backend local-app i PWA są używane **bez modyfikacji** — Bridge publikuje te
same tematy i to samo słownictwo pól, których oczekuje aplikacja (przyjazne nazwy trybów
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` typu int,
`filterAlarm` typu bool oraz `dewPoint`).

## Pliki do dodania w katalogu głównym repozytorium `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Uruchomienie

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Skierowanie urządzeń na ten host (wymagane, jednorazowo)

Urządzenia łączą się z tym hostem, który został zapisany podczas provisioningu BLE:

1. **Ponowny provisioning BLE (zalecane):** zapisz `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` w każdym urządzeniu.
2. **Trasa statyczna / DNAT:** przekieruj `185.214.203.87/32` → na ten host i dodaj
   alias IP, aby host akceptował pakiety kierowane na adres IP chmury.

Szczegóły w `CLOUD-INTEGRATION.md`.

## Tematy MQTT

| Temat | Kier. | Znaczenie |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | stan urządzenia (JSON, słownictwo aplikacji + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | polecenie `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | in | pełny harmonogram tygodniowy (z aplikacji) |
| `ambientika/<serial>/schedule/<day>/set` | in | przedziały czasowe jednego dnia |
| `ambientika/neuracell/state` | out | status NeuraCell-X na żywo (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — wymuś / wyczyść ochronę przed radonem |
| `ambientika/radon/value` | in | odczyt radonu (Bq/m³) — automatyczne wyzwolenie przy progu |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — wymuś / zwolnij blokadę punktu rosy |
| `ambientika/weather` | in | `{"temperature": t, "humidity": rh}` powietrze ZEWNĘTRZNE |

## Harmonogram tygodniowy

Wyzwalanie zboczem: gdy przedział czasowy staje się aktywny dla bieżącego dnia tygodnia/godziny,
Bridge stosuje jego `mode` (+ `fanSpeed` lub zachowuje bieżącą prędkość, jeśli przedział
jej nie określa) dokładnie **raz**, dzięki czemu ręczna zmiana w obrębie przedziału nie jest
nadpisywana. Harmonogram jest wstrzymywany, gdy aktywna jest ochrona NeuraCell-X.

## NeuraCell-X (radon + punkt rosy)

Priorytet: **radon > punkt rosy > normalny**. Przy pierwszym przejściu w dowolną
ochronę Bridge zapisuje bieżący tryb/wentylator każdego urządzenia jako stan bazowy; gdy wszystkie
ochrony ustąpią, wykonuje **dokładne przywrócenie**.

- **Ochrona przed radonem** — wyzwalana, gdy `radon/alarm=ON` lub `radon/value ≥
  RADON_THRESHOLD`. Wszystkie urządzenia → `INTAKE` na `LOW` (łagodne nadciśnienie świeżego powietrza).
  Normalne polecenia `/set` są blokowane, gdy jest aktywna.
- **Sterowanie punktem rosy (Taupunktsteuerung)** — wyzwalane, gdy `dewpoint/block=ON`, lub
  automatycznie, gdy **zewnętrzny** punkt rosy jest równy lub wyższy od wewnętrznego punktu rosy
  (minus `DEWPOINT_MARGIN`), tzn. wentylacja dodałaby wilgoci. Wszystkie urządzenia →
  `OFF`. Wymaga danych zewnętrznych w `ambientika/weather`; bez nich działa tylko ręczne
  nadpisanie. Wewnętrzny punkt rosy jest obliczany z temperatury i wilgotności każdego urządzenia
  (wzór Magnusa).

## Konfiguracja (env)

| Zmienna | Domyślnie | Znaczenie |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | prefiks tematów (zachowaj `ambientika`, aby pasował do aplikacji) |
| `LOCAL_TCP_PORT` | `11000` | port, z którym łączą się urządzenia |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | konfiguracja wysyłana przy połączeniu |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | wykonawca harmonogramu |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | kontroler radonu i punktu rosy |
| `RADON_THRESHOLD` | `100` | próg automatycznego wyzwolenia w Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatyczny punkt rosy + histereza °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publikacja Home Assistant discovery (niepotrzebne aplikacji) |

## Status weryfikacji

- ✅ Kodek transmisji bajt w bajt zgodny z `PROTOCOL.md` (temperatura i RSSI dekodowane
  jako **ze znakiem**).
- ✅ Pełny obieg słownictwa aplikacji (nazwy trybów, fanSpeed %, punkt rosy).
- ✅ Harmonogram tygodniowy: wyzwalanie zboczem stosuje przedziały raz; w pozostałych przypadkach brak działania; godziny
  normalizowane do `HH:MM`.
- ✅ NeuraCell-X: priorytet radonu, blokowanie poleceń, automatyczny i ręczny punkt rosy
  z **histerezą ±margines** oraz **dokładne przywrócenie** trybu sprzed ochrony
  (stan bazowy pobierany z ostatniego normalnego celu, a nie z echa urządzenia).
- ✅ Wzmocniona współbieżność: pojedynczy zamek szereguje polecenia/harmonogram/NeuraCell,
  stan ochrony jest zatwierdzany **przed** jakimkolwiek zapisem ochronnym, pętle urządzeń
  iterują po migawkach, zapisy są szeregowane per urządzenie.
- ✅ Odporność: ramkowanie TCP resynchronizuje się po błędnym bajcie; nieprawidłowe ładunki
  `weather` są odrzucane; ponowne połączenie zachowuje stan urządzenia; dane radonu/pogody
  starsze niż `NC_INPUT_TTL` traktowane jako nieznane; MQTT last-will + czyste zamknięcie.
- ✅ Zestaw testów regresji: **40 testów jednostkowych/integracyjnych** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Pełny test end-to-end przez **prawdziwy broker MQTT** z symulowanym urządzeniem
  (`smoke_test.py`, 13/13): status + polecenie + harmonogram + ochrona radonowa/blokada/
  przywrócenie + punkt rosy + resynchronizacja ramek + zamknięcie.
- ✅ `docker compose config` poprawne; brak jakichkolwiek poświadczeń chmury w stosie.
- ✅ API zwrotne paho-mqtt 2.x (VERSION2), zachowany fallback dla 1.x.
- ⛔️ **Jeszcze nieprzetestowane na prawdziwym sprzęcie** — protokół binarny odtworzony metodą
  inżynierii wstecznej + sterowanie istotne dla bezpieczeństwa. Zweryfikuj na jednym fizycznym
  urządzeniu przed wdrożeniem produkcyjnym (w szczególności dekodowanie temperatury/RSSI ze znakiem).

## Zatwierdzenie przed produkcją `>>> CONTROL <<<` / `>>> MAPPING <<<`

Mapowania trybów/wentylatora oraz progi i wartości docelowe radonu/punktu rosy to rozsądne
ustawienia domyślne, lecz niecertyfikowane. Zweryfikuj je względem specyfikacji produktu i dostrój w
`ambientika_local_bridge.py` (dwie tabele mapowań + pola sterujące `Config`).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, progi %→poziom wentylatora,
`RADON_THRESHOLD` oraz `DEWPOINT_MARGIN` to wartości do potwierdzenia.
