🌐 **DE** · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – lokaler Betriebsmodus ohne Server

> **Einordnung.** Der empfohlene Betriebsmodus der Local App ist die Cloud-Bridge
> (`docker-compose.yml`): im Feld bewährt und für den Regelbetrieb vorgesehen. Der
> hier beschriebene lokale Betriebsmodus ist als **Rückfallebene** ausgelegt. Er hält
> die Anlage bedienbar, wenn der Ambientika-Server nicht erreichbar ist — bei
> Wartungsfenstern, Netzstörungen oder in Objekten, für die eine dauerhafte
> Internetanbindung nicht vorgesehen ist. Er ersetzt den Standardmodus nicht.

In diesem Modus betreibt die Ambientika Local App (FastAPI + PWA) die Anlage **ohne
Ambientika-Server und ohne Internetverbindung**. Gegenüber dem Standardmodus ändert
sich ausschließlich die Geräteanbindung: An die Stelle der Bridge, die den Server
abfragt, tritt eine **lokale Bridge**, die die Lüftungsgeräte im Heimnetz direkt über
ihr natives TCP-Protokoll (Port 11000) anspricht.

```
Standardmodus:  Gerät → Ambientika-Server → Cloud-Bridge → MQTT → App
Lokaler Modus:  Gerät → lokale Bridge (TCP 11000) → MQTT → App      ← ohne Server
```

Der Funktionsumfang bleibt dabei vollständig erhalten:

- Geräteüberwachung und Steuerung (Modus, Lüfter, Sensoren, Taupunkt)
- **Ausführung des Wochenzeitplans**
- **NeuraCell-X**: Radon-Schutz (Priorität) und **Taupunktsteuerung**, mit exakter
  Wiederherstellung des zuvor aktiven Modus

Backend und PWA der Local App werden **unverändert** verwendet — die lokale Bridge
veröffentlicht dieselben Topics und dasselbe Feld-Vokabular wie im Standardmodus
(Modusnamen `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int,
`filterAlarm` bool, zusätzlich `dewPoint`).

## Komponenten

Ausgetauscht wird ausschließlich die Geräteanbindung; App und Steuerung bleiben gleich.

```
docker-compose.local.yml          # Stack ohne Server-Abfrage
Dockerfile.bridge                 # Image der lokalen Bridge
ambientika_local_bridge.py        # lokale Bridge (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # Konfiguration des lokalen Brokers
env.local.example.txt             # Konfigurationsvorlage (ohne Server-Zugangsdaten)
```

## Ausführen

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Geräte auf diesen Host ausrichten (erforderlich, einmalig)

Die Geräte verbinden sich mit dem Host, der beim BLE-Provisioning geschrieben wurde:

1. **BLE-Neu-Provisioning:** Schreiben Sie `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` in jedes Gerät.
2. **Statische Route / DNAT:** Leiten Sie `185.214.203.87/32` → auf diesen Host um und
   fügen Sie einen IP-Alias hinzu, damit der Host Pakete für die Cloud-IP annimmt.

Details in `CLOUD-INTEGRATION.md`.

## MQTT-Topics

| Topic | Richtung | Bedeutung |
|-------|-----|---------|
| `ambientika/<serial>/status` | raus | Gerätezustand (JSON, App-Vokabular + `dewPoint`) |
| `ambientika/<serial>/availability` | raus | `online` / `offline` |
| `ambientika/<serial>/set` | rein | `{mode, fanSpeed, ...}` Befehl |
| `ambientika/<serial>/schedule/set` | rein | vollständiger Wochenzeitplan (aus der App) |
| `ambientika/<serial>/schedule/<day>/set` | rein | Slots eines Tages |
| `ambientika/neuracell/state` | raus | NeuraCell-X Live-Status (JSON) |
| `ambientika/radon/alarm` | rein | `ON`/`OFF` — Radon-Schutz erzwingen / aufheben |
| `ambientika/radon/value` | rein | Radonwert (Bq/m³) — löst bei Schwelle automatisch aus |
| `ambientika/dewpoint/block` | rein | `ON`/`OFF` — Taupunkt-Sperre erzwingen / freigeben |
| `ambientika/weather` | rein | `{"temperature": t, "humidity": rh}` AUSSENluft |

## Wochenzeitplan

Flankengesteuert: Sobald ein Slot für den aktuellen Wochentag/die aktuelle Uhrzeit
aktiv wird, wendet die Bridge dessen `mode` (+ `fanSpeed`, oder behält die aktuelle
Geschwindigkeit, wenn der Slot keine hat) genau **einmal** an, sodass eine manuelle
Änderung innerhalb eines Slots nicht überschrieben wird. Der Zeitplan wird
ausgesetzt, solange ein NeuraCell-X-Schutz aktiv ist.

## NeuraCell-X (Radon + Taupunkt)

Priorität: **Radon > Taupunkt > normal**. Beim ersten Übergang in einen beliebigen
Schutz speichert die Bridge den aktuellen Modus/Lüfter jedes Geräts als
Ausgangswert; sobald alle Schutzfunktionen aufgehoben sind, führt sie eine **exakte
Wiederherstellung** durch.

- **Radon-Schutz** — löst aus, wenn `radon/alarm=ON` oder `radon/value ≥
  RADON_THRESHOLD`. Alle Geräte → `INTAKE` bei `LOW` (sanfter Frischluft-Überdruck).
  Normale `/set`-Befehle werden während der Aktivität unterdrückt.
- **Taupunktsteuerung** — löst aus, wenn `dewpoint/block=ON`, oder
  automatisch, wenn der **Außen-Taupunkt** auf oder über dem Innen-Taupunkt liegt
  (minus `DEWPOINT_MARGIN`), d. h. wenn Lüften Feuchtigkeit hinzufügen würde. Alle
  Geräte → `OFF`. Benötigt Außendaten auf `ambientika/weather`; ohne diese
  funktioniert nur die manuelle Übersteuerung. Der Innen-Taupunkt wird aus
  Temperatur + Feuchte jedes Geräts berechnet (Magnus-Formel).

## Konfiguration (env)

| Var | Standard | Bedeutung |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | Broker |
| `MQTT_PREFIX` | `ambientika` | Topic-Präfix (`ambientika` beibehalten, damit es zur App passt) |
| `LOCAL_TCP_PORT` | `11000` | Port, mit dem sich die Geräte verbinden |
| `SEND_SETUP` | `false` | Opt-in zum Schreiben der Gerätetopologie beim Verbinden; vorher alle folgenden Werte prüfen |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | Setup-Werte, die nur bei `SEND_SETUP=true` verwendet werden |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | Zeitplan-Ausführer |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | Radon+Taupunkt-Controller |
| `RADON_THRESHOLD` | `100` | Bq/m³ Schwelle für automatisches Auslösen |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | automatischer Taupunkt + °C-Hysterese |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | Home Assistant Discovery veröffentlichen (von der App nicht benötigt) |

## Qualitätssicherung

- ✅ Wire-Codec byte-für-byte gegen die Protokollspezifikation (Temperatur & RSSI
  **vorzeichenbehaftet** dekodiert).
- ✅ App-Vokabular-Round-Trip (Modusnamen, fanSpeed %, Taupunkt).
- ✅ Wochenzeitplan: Flankentrigger wendet Slots einmal an; sonst keine Aktion;
  Zeiten normalisiert auf `HH:MM`.
- ✅ NeuraCell-X: Radon-Priorität, Befehlsunterdrückung, automatischer + manueller
  Taupunkt mit **±Margin-Hysterese** und **exakter Wiederherstellung** des Modus
  vor dem Schutz (Ausgangswert vom letzten normalen Zielwert übernommen, nicht vom
  Geräte-Echo).
- ✅ Nebenläufigkeit gehärtet: Ein einzelnes Lock serialisiert
  Befehl/Zeitplan/NeuraCell, der Schutzzustand wird **vor** jedem
  Schutz-Schreibvorgang festgeschrieben, Geräteschleifen iterieren über Snapshots,
  Schreibvorgänge werden pro Gerät serialisiert.
- ✅ Robustheit: TCP-Framing synchronisiert sich nach einem verirrten Byte neu;
  fehlerhafte `weather`-Payloads werden abgelehnt; Reconnect erhält den
  Gerätezustand; Radon-/Wetter-Eingaben, die älter als `NC_INPUT_TTL` sind, werden
  als unbekannt behandelt; MQTT Last-Will + sauberes Herunterfahren.
- ✅ Regressions-Suite: **40 Unit-/Integrationstests** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Vollständig end-to-end über einen **echten MQTT-Broker** mit einem simulierten
  Gerät (`smoke_test.py`, 13/13): Status + Befehl + Zeitplan +
  Radon-Schutz/-Unterdrückung/-Wiederherstellung + Taupunkt + Framing-Resync +
  Herunterfahren.
- ✅ `docker compose config` gültig; keine Cloud-Zugangsdaten irgendwo im Stack.
- ✅ paho-mqtt 2.x Callback-API (VERSION2), 1.x-Fallback beibehalten.

## Parametrierung

Modus- und Lüfterzuordnungen sowie die Schwellenwerte für Radon- und Taupunktschutz
sind mit anwendungssicheren Standardwerten vorbelegt und projektspezifisch anpassbar
in `ambientika_local_bridge.py` (zwei Zuordnungstabellen und die `Config`-Felder):
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, die Schwellen der
Lüfterstufen sowie `RADON_THRESHOLD` und `DEWPOINT_MARGIN`. Objektbezogene Grenzwerte
— insbesondere behördlich vorgegebene Radonschwellen — sind bei der Inbetriebnahme zu
setzen.

## Freigabestatus

Der lokale Betriebsmodus wird als **kontrollierte Auslieferung (Beobachtungsmodus)**
bereitgestellt: Die Feldvalidierung über den gesamten ausgelieferten Firmwarebestand
ist noch nicht abgeschlossen, Rückmeldungen aus Installationen fließen laufend in die
Freigabe ein. Für den Regelbetrieb bleibt die Cloud-Bridge die empfohlene Variante;
der lokale Modus ist die Rückfallebene für den Fall, dass der Server nicht erreichbar
ist. Rückmeldungen bitte über die Issues dieses Repositories.
