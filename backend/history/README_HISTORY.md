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

## Was schon 1:1 mit dem heutigen System funktioniert
`serial`, `device_name`, `temperature`, `humidity`, `mode`(+num), `fanSpeed`(%+Stufe),
`airQuality`(VOC + 5-Stufen-Kategorie), `filterAlarm`→Ampel(grün/rot), `rssi`
(Funkgüte), `online`. Alles aus dem realen Bridge-Status-Payload.

## Was noch von eurer Seite kommt (heute NULL, bricht nichts)
Die Bridge liefert im Status-Payload **noch nicht**: `zone`, `role` (Master/Slave),
`humidity_threshold`, `night_mode` (eigenes Feld), `mode_effective` (Zonen-/Master-Modus).
`recorder.py` übernimmt diese Felder **automatisch**, sobald die Bridge sie mitschickt
(Keys: `zone`, `role`, `humidityThreshold`, `nightMode`, `modeEffective`/`zoneMode`/`masterMode`).

## Gegen App/Firmware final zu bestätigen (Defaults sind gesetzt)
- **VOC→Luftqualitätsstufe**: Schwellen 300/600/1000/1500 (`mappings.AIR_QUALITY_VOC_BANDS`).
- **Lüfter %→Stufe**: 0/33/66/100 (`mappings.FAN_STAGE_BANDS`) — Rohprozent bleibt ohnehin erhalten.
- **Filter „gelb"**: heute nicht im Payload (nur bool) → nur grün/rot, bis die Bridge einen echten Filterstatus liefert.
- **Modus-Nummern**: an die realen Bridge-Strings gebunden (HRV/NIGHT/BOOST/ECO/SMART/OFF), nicht an die 12 Handbuch-Namen. Weitere native Modi in `mappings.MODE_NUM` ergänzen.

## Grafana (Resch-Pilot)
Grafana kann direkt auf die SQLite (Plugin `frser-sqlite-datasource`) oder — der
saubere Weg — über MQTT → Home Assistant, das die Langzeitstatistik selbst führt.
