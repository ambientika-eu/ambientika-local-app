# Changelog

Alle nennenswerten Änderungen an diesem Repository. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

### Behoben

- **Lokale Bridge (Betrieb ohne Server) — Setup:** Beim Verbinden wurde jedem Gerät dasselbe
  Setup geschrieben (Master, Zone 0, Haus 1). Bei Anlagen mit mehreren Geräten hätte das die
  Master/Slave-Zuordnung und die Querlüftung überschrieben. Setup ist jetzt standardmäßig aus,
  nur pro Seriennummer über `SETUP_DEVICES`, und eine vom Gerät gemeldete Rolle wird nie ohne
  `SETUP_ALLOW_ROLE_CHANGE=true` geändert. Das alte globale Setup gibt es nur noch mit
  ausdrücklichem `SEND_SETUP=true`. Rolle, Zone (0–15) und Haus sind in `SETUP_DEVICES` Pflicht.
- **Lokale Bridge — Setup-Format:** Das Setup-Paket hatte 16 Byte mit einem Füllbyte vor der
  Haus-ID; die Geräte lesen die Haus-ID ab Byte 11 und speicherten sie um ein Byte verschoben
  (Haus 1 wurde zu 256). Jetzt 15 Byte im Format des Servers. Geräte, die im lokalen Modus mit
  der alten Version liefen, haben Haus-ID 256 gespeichert; bei Bedarf einmal mit der
  offiziellen App neu einrichten.
- **Lokale Bridge — Keep-alive:** Das 8-Byte-Paket `04 00 <MAC>`, das Master regelmäßig senden,
  wird erkannt und ohne Wirkung verbraucht (zählt auch als Nachweis für 19-Byte-Geräte).
- **Lokale Bridge — 19-Byte-Status:** Geräte mit Funk-Firmware 0.0.11 und älter senden 19 statt
  21 Byte; die Bridge las immer 21 und verschob damit alle folgenden Rahmen. Die Länge kommt
  jetzt aus dem Firmware-Rahmen bzw. dem Datenstrom; ohne eindeutigen Nachweis bleibt es bei
  21 Byte (unverändert für heutige Geräte, Differenztest alt/neu mit gültigen Rahmen: 3000 Szenarien,
  0 Abweichungen;
  einzige Ausnahme: ein Rahmen mit RSSI 0 und Lichtcode 1/3 wird erst mit dem nächsten Rahmen
  veröffentlicht, weil erst dann sicher ist, dass er 21 Byte lang ist).
- **Lokale Bridge — nur bestätigte Codes:** Modus/Lüfter/Feuchte/Licht wurden ungeprüft
  gesendet. Jetzt gehen nur Werte aus der Protokolltabelle an das Gerät, alles andere wird
  verweigert und protokolliert (auch Zeitplan und `RADON_PROTECT_*`). Vom Gerät gemeldete
  Feuchte-/Lichtcodes außerhalb der Tabelle werden durch bestätigte Standardwerte ersetzt statt
  zurückgeschrieben; nicht lesbare Befehle und Zeitplan-Einträge werden abgewiesen statt eine
  Ausnahme auszulösen. Schutzschaltungen erreichen ein Gerät erst nach seinem ersten Status, damit
  sein echter Zustand bekannt ist und nach dem Schutz korrekt wiederhergestellt wird.
- **Lokale Bridge — Wetterdaten und unbekannte Rahmen:** Außenwetterdaten für den SMART-Modus
  werden bewusst nicht gesendet (Format vom Hersteller nicht bestätigt). Unbekannte Rahmentypen
  werden protokolliert statt still verworfen; ein 0x01/0x03-Byte mitten in einem unbekannten
  Rahmen wird nicht mehr als Status eines fremden Geräts gelesen (Kopfbyte 0x00, beim
  Resynchronisieren gleiche MAC). Schutzschaltungen (Radon/Taupunkt/Wiederherstellung) setzen
  bei unerwartetem Echo bestätigte Standardwerte statt den Schutz zu verweigern; als
  Normalzustand wird nur ein bestätigter Zustand gemerkt (74 neue Tests in
  `test_safety_fixes.py`, vier unabhängige Gegenprüfungen).
- **PWA aus einem frischen Klon:** `docker compose up` lieferte unter `http://IP:8080` nur die
  API, weil `./frontend/dist` im Repo nicht existiert und als leeres Verzeichnis eingehängt
  wurde. Das Backend liefert die PWA jetzt aus `frontend/` aus (ein gebautes `frontend/dist/`
  hat weiterhin Vorrang), beide Compose-Dateien hängen `./frontend` ein; README-Hinweis
  korrigiert (`index.html` als Datei öffnen funktioniert nicht, die App spricht die API
  unter derselben Adresse an).
- **Free-Cooling-Wächter:** Nach der Bestätigung des Starts ist die Schonfrist erledigt —
  ein Eingriff des Nutzers mit der nächsten Abfrage wird sofort erkannt, nicht erst nach
  Ablauf der 180 s ab dem Startbefehl (2 neue Tests).
- **PWA:** Fehlermeldungen beim Speichern der Wächter-Einstellungen zeigen den Text der API
  (`detail`) statt des rohen JSON. Service Worker Cache `ambientika-local-v5`, Backend 2.1.1.

### Hinzugefügt

- **Free-Cooling-Wächter** (`backend/freecooling/`, Tab „❄️ Free Cooling“ in der PWA):
  ersetzt die Free-Cooling-Entscheidung des SMART-Modus durch eine lokale Regel mit
  Mindestabstand (Start ≥ 3,0 K, Ende < 1,0 K), Haltezeit (15 min), Gültigkeit des
  Außenwerts (60 min), Totband 0,3 K am unteren Außenwert und sofortigem Ende, sobald die
  Außenluft nicht kühler ist als innen. Außenwert bevorzugt von einem Sensor am Haus
  (`ambientika/weather`, `ambientika/dewpoint/outdoor_temp`, `POST /api/freecooling/outdoor`;
  retained Nachrichten werden ignoriert), OpenWeatherMap nur als Rückfallebene
  (`OWM_API_KEY`, `OWM_LOCATION`). Kühlt im Luftstrom Master→Slave (wie die SMART-Firmware;
  Slave→Master wählbar; Tag Stufe 2 / Nacht Stufe 1) und stellt exakt zurück; ersetzt SMART
  auf den gesteuerten Geräten durch AUTO; erkennt Eingriffe des Nutzers; NeuraCell-X hat
  Vorrang. Starts werden bestätigt, verspätete Befehle übernommen, Rückschaltungen bis zur
  Bestätigung wiederholt; Schonfristen nach Schutzphasen und Bridge-Unterbrechungen; alte
  Gerätewerte sperren Entscheidungen. Jede Umschaltung wird in SQLite protokolliert
  (Innen-/Außenwert, Quelle, Alter, Modus, Grund) und ist als CSV exportierbar.
  Konfiguration und Laufzeitzustand überleben einen Neustart. REST: `/api/freecooling`,
  `/config`, `/log`, `/log.csv`, `/outdoor`, `/evaluate`; WebSocket-Ereignis `freecooling`.
  64 Tests in `test_freecooling.py`.
- `GET /api/devices/{id}/smart` zeigt den Außenwert des Wächters, wenn das Gerät keinen liefert.
- Backend-Version 2.1.0; Service Worker Cache `ambientika-local-v4`.

### Geändert

- Tab-Leiste der PWA scrollt auf schmalen Bildschirmen horizontal statt die Seite zu verbreitern.

- Dokumentation des lokalen Betriebsmodus überarbeitet: klare Einordnung als
  Rückfallebene gegenüber dem Standardmodus (Cloud-Bridge), einheitliche Struktur
  in allen elf Sprachfassungen.
- Verweis auf ein separates Repository für den serverlosen Betrieb entfernt; der
  lokale Betriebsmodus ist Bestandteil dieses Repositories.

## [1.1.0] – Lokaler Betriebsmodus, Härtung

Härtungsrunde der lokalen Bridge (`ambientika_local_bridge.py`) vor der
kontrollierten Auslieferung. Verifikation: statische Analyse ohne Befund
(ruff, pyflakes, mypy für die kritischen Pfade, py_compile), **40 Unit- und
Integrationstests grün** sowie ein **End-to-End-Smoke-Test gegen einen echten
MQTT-Broker: 13/13**, zweimal reproduziert.

### Nebenläufigkeit und Zustandsführung

- Schutzzustand wird vor den zugehörigen Schreibvorgängen festgeschrieben; ein
  `asyncio.Lock` serialisiert Befehl, Zeitplan und NeuraCell-X. Kein
  Unterdrückungsfenster, keine doppelte Anwendung.
- Alle Geräteschleifen laufen über Snapshots (`list(...)`); ein Verbindungsabbruch
  während einer laufenden Anwendung ist unkritisch.
- Wiederherstellungs-Ausgangswert stammt aus dem letzten regulären Zielwert
  (`normal_codes`), nicht aus dem Geräte-Echo — korrekte Wiederherstellung auch bei
  schnell aufeinanderfolgenden Auslösungen.
- Schreibvorgänge werden je Gerät über ein eigenes `send_lock` serialisiert.

### Regelverhalten

- Taupunktsteuerung mit echter ±Margin-Hysterese (Sperre bei
  `außen ≥ innen + margin`, Freigabe erst bei `außen < innen − margin`) — kein
  Flattern an der Schwelle.
- Radon- und Wetterwerte, die älter als `NC_INPUT_TTL` sind (Standard 900 s), gelten
  als unbekannt; der Schutz wird dadurch weder stillschweigend deaktiviert noch
  dauerhaft gehalten.
- `neuracell/state` wird nur bei tatsächlicher Änderung veröffentlicht (mit
  `retain`).
- Zeitplan-Slots werden auf `HH:MM` normalisiert; Aktualisierungen laufen über den
  Loop-Thread, Callback-Fehler werden protokolliert.

### Protokoll und Robustheit

- Temperatur und RSSI werden vorzeichenbehaftet dekodiert (z. B. −5 °C, −56 dBm) —
  korrekte Eingangswerte für die Taupunktberechnung.
- TCP-Framing synchronisiert sich nach einem unerwarteten Byte neu, statt die
  Verbindung dauerhaft zu blockieren.
- Fehlerhafte `weather`-Payloads werden verworfen.
- Reconnect erhält den Gerätezustand; lediglich `setup_sent` wird zurückgesetzt und
  das Setup einmalig erneut gesendet.
- `HOUSE_ID` wird auf u32 begrenzt.
- Luftqualität: Rohwert 0 wird als `UNKNOWN_SENSOR` gemeldet, oberes Ende begrenzt.
- Es wird nie ein Frame mit unaufgelöstem Code gesendet.
- MQTT Last-Will und sauberes Herunterfahren (`bridge/availability` = `offline`).
- paho-mqtt 2.x Callback-API (VERSION2) mit beibehaltenem 1.x-Fallback.

### Abdeckung des Smoke-Tests

Echter Broker, simuliertes Gerät über TCP: Setup-Push, Status-Publish (inkl.
vorzeichenbehaftetem RSSI), MQTT-Moduswechsel → korrektes 13-Byte-Frame am Gerät,
Radon → INTAKE/LOW, Befehlsunterdrückung im Schutz, Radon aus → exakte
Wiederherstellung auf BOOST/HIGH, manuelle Taupunktsperre → OFF und Freigabe →
Wiederherstellung, Framing-Resynchronisation nach Störbyte, sauberes Offline beim
Herunterfahren.

### Offen

Feldvalidierung über den gesamten ausgelieferten Firmwarebestand; der lokale
Betriebsmodus wird bis dahin als kontrollierte Auslieferung (Beobachtungsmodus)
bereitgestellt.
