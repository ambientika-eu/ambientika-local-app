# Integration in ambientika-local-app — exakte Änderungen

Kleiner, additiver Changeset. Neue Dateien + drei winzige Edits an bestehenden
Dateien. **Keine neue Abhängigkeit** (`sqlite3` ist Standardbibliothek,
`fastapi`/`paho-mqtt` sind schon in `requirements.txt`).

## Neue Dateien
- `backend/history/` — das komplette Paket (Kern + Router + Selbsttest).
- `frontend/history.html` — eigenständige Historie-/Export-Oberfläche.

## 1) `backend/main.py` — drei Einfügungen

**(a) Nach `app.add_middleware(CORSMiddleware, ...)` einfügen** (registriert die
Routen VOR dem `app.mount("/", StaticFiles ...)` am Dateiende):

```python
# --- Lokale Messwert-Historie (SQLite + MQTT/HA, keine Cloud) ---
from history.sampler import HistorySampler
from history.routes import make_history_router

history_sampler = HistorySampler(
    get_devices=lambda: devices,      # bestehendes In-Memory-Dict
    mqtt_client=mqtt_client,          # bestehender paho-Client
    mqtt_prefix=MQTT_PREFIX,
)
app.include_router(make_history_router(history_sampler))
```

**(b) In `lifespan(...)`, direkt VOR `yield`** (nach dem MQTT-`try/except`):

```python
    history_sampler.start()
```

**(c) In `lifespan(...)`, direkt NACH `yield`** (vor `mqtt_client.loop_stop()`):

```python
    await history_sampler.stop()
```

## 2) `backend/Dockerfile` — eine Zeile

Der Dockerfile kopiert heute nur `main.py`. Ergänzen:

```dockerfile
COPY main.py .
COPY history/ ./history/          # <-- neu, sonst fehlt das Modul im Image
```

## 3) `docker-compose.yml` — Volume + Env für den `backend`-Service

Ohne persistentes Volume geht die Historie bei Container-Neubau verloren.

Im `backend`-Service unter `environment:` ergänzen:
```yaml
      HISTORY_DB: /data/history.db
      # optional (Defaults: 300 / 730 / true):
      # HISTORY_INTERVAL: 300
      # HISTORY_RETENTION_DAYS: 730
      # HISTORY_MQTT: "true"
```
Im `backend`-Service unter `volumes:` ergänzen:
```yaml
      - ambientika-history:/data
```
Und beim top-level `volumes:`-Block ergänzen:
```yaml
volumes:
  mosquitto-data:
  mosquitto-log:
  ambientika-history:               # <-- neu
```

## 4) Frontend anbinden (zwei Wege)

`frontend/history.html` funktioniert eigenständig, sobald das Backend das
Frontend ausliefert (erreichbar unter `/history.html`). Sie ruft nur die
`/api/history*`-Endpunkte auf.

- **Minimal:** einen Link/Button in `index.html` auf `history.html` setzen.
- **Als Tab:** im `<nav>` nach dem NeuraCell-Tab einen Eintrag ergänzen und einen
  `view-history`-Container mit einem `<iframe src="history.html">`. Da die App
  Tab-Labels per i18n füllt und `showTab()` genutzt wird, sollte dieser Schritt
  gegen die aktuelle `index.html` gemacht werden (nicht blind), damit i18n/Logik
  konsistent bleiben.

> Serving-Hinweis: `docker-compose.yml`/`main.py` liefern das Frontend aus
> `frontend/dist`. `history.html` muss also im selben ausgelieferten Verzeichnis
> landen wie `index.html` (dort, wo euer Build `dist` befüllt).

## Prüfen
```bash
cd backend && python -m history.selftest      # End-to-End ohne Broker/Hardware
# danach:
curl localhost:8080/api/history/config
curl "localhost:8080/api/history/export.csv" -o out.csv
```
