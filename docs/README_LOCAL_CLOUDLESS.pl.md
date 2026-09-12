🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · **PL** · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – lokalny tryb pracy bez serwera

> **Umiejscowienie.** Zalecanym trybem pracy Local App jest mostek chmurowy
> (`docker-compose.yml`): sprawdzony w praktyce i przeznaczony do eksploatacji
> bieżącej. Opisany tutaj tryb lokalny zaprojektowano jako **poziom awaryjny**.
> Utrzymuje instalację w gotowości do obsługi, gdy serwer Ambientika jest
> nieosiągalny — podczas okien serwisowych, awarii sieci lub w obiektach, dla których
> nie przewidziano stałego łącza internetowego. Nie zastępuje trybu standardowego.

W tym trybie Ambientika Local App (FastAPI + PWA) prowadzi instalację **bez serwera
Ambientika i bez połączenia internetowego**. W porównaniu z trybem standardowym
zmienia się wyłącznie sposób połączenia z urządzeniami: zamiast mostka odpytującego
serwer działa **mostek lokalny**, który komunikuje się z centralami wentylacyjnymi
bezpośrednio w sieci domowej przez ich natywny protokół TCP (port 11000).

```
Tryb standardowy:  Urządzenie → serwer Ambientika → mostek chmurowy → MQTT → app
Tryb lokalny:      Urządzenie → mostek lokalny (TCP 11000) → MQTT → app  ← bez serwera
```

Pełny zakres funkcji zostaje zachowany:

- monitorowanie i sterowanie urządzeniami (tryb, wentylator, czujniki, punkt rosy)
- **realizacja harmonogramu tygodniowego**
- **NeuraCell-X**: ochrona radonowa (priorytet) i **sterowanie punktem rosy**, z
  dokładnym przywróceniem wcześniej aktywnego trybu

Backend Local App i PWA są używane **bez zmian** — mostek lokalny publikuje te same
tematy i to samo słownictwo pól co tryb standardowy (nazwy trybów
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int, `filterAlarm`
bool, dodatkowo `dewPoint`).

## Komponenty

Wymieniane jest wyłącznie połączenie z urządzeniami; aplikacja i logika sterowania
pozostają bez zmian.

```
docker-compose.local.yml          # stos bez odpytywania serwera
Dockerfile.bridge                 # obraz mostka lokalnego
ambientika_local_bridge.py        # mostek lokalny (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # konfiguracja lokalnego brokera
env.local.example.txt             # szablon konfiguracji (bez danych dostępowych serwera)
```

## Uruchomienie

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Skierowanie urządzeń na ten host (wymagane, jednorazowo)

Urządzenia łączą się z tym hostem, który został zapisany podczas provisioningu BLE:

1. **Ponowny provisioning BLE:** zapisz `H_<host-ip>:11000`, `S_<ssid>`,
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
| `SEND_SETUP` | `false` | jawnie włącza zapis topologii urządzenia przy połączeniu; najpierw sprawdź wszystkie wartości poniżej |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | wartości konfiguracji używane tylko przy `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | wykonawca harmonogramu |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | kontroler radonu i punktu rosy |
| `RADON_THRESHOLD` | `100` | próg automatycznego wyzwolenia w Bq/m³ |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatyczny punkt rosy + histereza °C |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publikacja Home Assistant discovery (niepotrzebne aplikacji) |

## Zapewnienie jakości

- ✅ Kodek transmisji bajt w bajt zgodny z die Protokollspezifikation (temperatura i RSSI dekodowane
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

## Parametryzacja

Przypisania trybów i wentylatora oraz progi ochrony radonowej i punktu rosy są
wstępnie ustawione na wartości bezpieczne dla zastosowania i można je dostosować do
projektu w `ambientika_local_bridge.py` (dwie tabele przypisań oraz pola `Config`):
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, progi biegów
wentylatora oraz `RADON_THRESHOLD` i `DEWPOINT_MARGIN`. Wartości graniczne właściwe
dla obiektu — w szczególności progi radonu wymagane przepisami — należy ustawić przy
uruchomieniu.

## Status wydania

Lokalny tryb pracy jest udostępniany jako **wydanie kontrolowane (tryb obserwacji)**:
walidacja w terenie na całym wdrożonym zasobie firmware nie została jeszcze
zakończona, a informacje zwrotne z instalacji są na bieżąco uwzględniane. Do
eksploatacji bieżącej zalecanym wariantem pozostaje mostek chmurowy; tryb lokalny
jest poziomem awaryjnym na wypadek nieosiągalności serwera. Uwagi prosimy zgłaszać
przez issues tego repozytorium.
