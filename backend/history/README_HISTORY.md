# Lokale Messwert-Historie — `backend/history/`

Drop-in-Paket für die **Ambientika Local App** (FastAPI-Backend). Speichert die
Gerätemesswerte lokal in **SQLite**, exportiert **CSV/JSON** über die API und
publiziert denselben Wertesatz per **MQTT** inkl. **Home-Assistant-Discovery**
mit `state_class: measurement` (HA führt die Langzeitstatistik selbst).
**Keine Cloud** — alles läuft im Backend, das ohnehin lokal steht.

Nur Python-Standardbibliothek für den Kern (sqlite3). MQTT nutzt den
vorhandenen `paho-mqtt`-Client des Backends.

## Dateien
- `mappings.py` — verbindliche Text→Zahl-Mappings (Modus, Luftqualität, Lüfterstufe, Filterstatus, Rolle). **Der eigentliche Aufwand, an einer Stelle.**
- `store.py` — SQLite-Schema, idempotenter Upsert `UNIQUE(serial, ts_utc)`, Retention-Purge, Config.
- `recorder.py` — Bridge-Status-Payload → normalisierter Datensatz (tolerant gegenüber fehlenden/zukünftigen Feldern).
- `exporter.py` — CSV/JSON.
- `discovery.py` — MQTT-State-Topic + HA-Discovery-Configs.
- `sampler.py` — periodische Aufzeichnung (asyncio-Task) + MQTT-Publish + täglicher Purge.
- `routes.py` — FastAPI-Router (`/api/history`, Export, Config, Sample-Now).
- `selftest.py` — End-to-End-Test ohne Broker/Hardware (`python -m history.selftest`).

## Integration in `backend/main.py`

```python
# 1) Imports (oben)
from history.sampler import HistorySampler
from history.routes import make_history_router

sampler: HistorySampler | None = None

# 2) In der lifespan – nach dem MQTT-Setup:
@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, sampler
    loop = asyncio.get_running_loop()
    # ... bestehendes mqtt_client-Setup ...
    sampler = HistorySampler(
        get_devices=lambda: devices,     # das In-Memory-Dict des Backends
        mqtt_client=mqtt_client,
        mqtt_prefix=MQTT_PREFIX,
    )
    app.include_router(make_history_router(sampler))
    sampler.start()
    yield
    await sampler.stop()
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
```

Das war's — ab dann wird alle 5 Minuten (konfigurierbar) je Gerät ein Datensatz
geschrieben und per MQTT/HA veröffentlicht.

## Konfiguration (ENV)
| Variable | Default | Bedeutung |
|---|---|---|
| `HISTORY_DB` | `history.db` | Pfad der SQLite-Datei (persistentes Volume wählen) |
| `HISTORY_INTERVAL` | `300` | Aufzeichnungsintervall in Sekunden |
| `HISTORY_RETENTION_DAYS` | `730` | Aufbewahrung (2 Jahre) |
| `HISTORY_MQTT` | `true` | numerischen Wertesatz + Discovery per MQTT publizieren |

## API
- `GET /api/history?serial=&start=&end=&limit=` — Zeilen abfragen
- `GET /api/history/export.csv` / `export.json` — Download inkl. Text+Num
- `GET /api/history/config` — Intervall/Aufbewahrung/Zeilenzahl
- `POST /api/history/sample-now` — sofort eine Runde aufzeichnen

## Realer Bridge-State-Contract (Anker der Mappings)
Die Bridge publiziert unter `<prefix>/<serial>/state` (JSON, retained) exakt diese
Felder — daran ist das Paket ausgerichtet:
`operating_mode`, `fan_speed` (Low/Medium/High), `humidity_level` (Dry/Normal/Moist),
`light_sensor_level`, `temperature`, `humidity`, `air_quality` (**String**, z. B. „good"),
`humidity_alarm`, `filters_status` (green/yellow/red), `night_alarm`, `device_role`
(Master/Slave), `last_operating_mode`, `zone_index`. Die Seriennummer steht im **Topic**,
`online` kommt aus dem separaten `availability`-Topic.

Damit werden **alle** ursprünglichen Wunschfelder abgedeckt: Rolle, Zone,
Betriebsmodus (aktuell **und** zuletzt gesetzt: `mode_reported` + `mode_last`),
Feuchteschwelle (`humidity_level`), Nachtmodus (abgeleitet aus `operating_mode == Night`)
und die Filter-Ampel (grün/**gelb**/rot). Jeder Textwert bekommt zusätzlich sein `*_num`.

Betriebsmodus-Nummern = **native `OperatingMode`-Enumwerte** aus `ambientika_py`
(Smart=0, Auto=1, ManualHeatRecovery=2, Night=3, AwayHome=4, Surveillance=5,
TimedExpulsion=6, Expulsion=7, Intake=8, MasterSlaveFlow=9, SlaveMasterFlow=10, Off=11) —
genau **ein** Nummernkreis, von der Bibliothek definiert.

## Nicht verfügbar (bewusst weggelassen)
`rssi`/Funkgüte, Außen-Temperatur/-Feuchte, VOC-Rohzahl und `isDark` liefert die
Bibliothek/Bridge **nicht** — daher nicht im Schema (kein toter HA-Sensor). Liefert die
Firmware `air_quality` ausnahmsweise als **Zahl** (ppm/CO₂), wird sie als `air_quality_voc`
gespeichert und über die VOC-Bänder klassifiziert (Dual-Pfad in `air_quality_normalize`).

## Gegen die reale Firmware final zu bestätigen (Defaults sind gesetzt, `mappings.FLAGS`)
- **Luftqualität-String → Stufe**: `AIR_QUALITY_TEXT_NUM` (mehrsprachig, tolerant) gegen die
  echten Gerätestrings kalibrieren; ersatzweise VOC-Bänder 300/600/1000/1500.
- **Lüfter**: real 3-stufig (Low/Medium/High → 1/2/3); keine separate Nachtdrehzahl als Fan-Wert.
- **`device_role`**: als String erwartet (Master/Slave); bei Zahlencode `ROLE_NUM` ergänzen.

## Grafana (Resch-Pilot)
Grafana kann direkt auf die SQLite (Plugin `frser-sqlite-datasource`) oder — der
saubere Weg — über MQTT → Home Assistant, das die Langzeitstatistik selbst führt.
