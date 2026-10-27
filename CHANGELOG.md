# Changelog

Alle nennenswerten Änderungen an diesem Repository. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

## [1.2.0] – 2026-10-27 – Erweiterungen lokaler Betrieb, Free-Cooling-Wächter

### Erweitert (lokaler Betrieb ohne Server, `ambientika_local_bridge.py`)

- **Bestätigte Wiederherstellung nach Radon-/Taupunktschutz:** Nach Ende des Schutzes wird jedes
  Gerät in seinen vorherigen Modus zurückgesetzt; die Bridge prüft das an der nächsten
  Statusmeldung und wiederholt bei Bedarf (max. 3, ≥ 20 s Abstand) — auch für Geräte, die bei der
  Freigabe offline sind, und über mehrere Schutzphasen. Eine zwischenzeitliche Änderung am Gerät
  oder ein Befehl mit Modus hat Vorrang. Offene Wiederherstellungen verfallen nach
  `PENDING_RESTORE_TTL` (24 h).
- **Zustand übersteht einen Neustart:** Mit `STATE_FILE` (Compose: Volume `bridge-state`) bleibt
  ein aktiver Schutz nach einem Neustart der Bridge aktiv, bis der auslösende Eingang sich wieder
  meldet (Radonwert/manuelles Topic höchstens `NC_INPUT_TTL`); der Taupunkt-Latch wird
  gespeichert.
- **Setup pro Gerät:** Rolle, Zone und Haus werden nur noch für Geräte geschrieben, die in
  `SETUP_DEVICES` eingetragen sind (Rolle, Zone 0–15 und Haus Pflicht); eine vom Gerät gemeldete
  Master/Slave-Rolle wird nur mit `SETUP_ALLOW_ROLE_CHANGE=true` geändert. Das globale Setup
  steht weiter mit `SEND_SETUP=true` zur Verfügung. Setup-Paket im 15-Byte-Format des Servers.
  Geräte, die im lokalen Betrieb eingerichtet wurden, bei einem Wechsel zur offiziellen App dort
  einmal neu einrichten.
- **Statusformate:** Statusmeldungen mit 19 Byte (Funk-Firmware 0.0.11 und älter) und 21 Byte
  werden unterstützt; die Länge kommt aus dem Firmware-Rahmen bzw. dem Datenstrom. Das
  Keep-alive-Paket `04 00 <MAC>` der Master wird erkannt.
- **Nur Werte aus der Protokolltabelle:** Modus, Lüfter, Feuchte und Licht werden vor dem Senden
  gegen die Protokolltabelle geprüft (auch Zeitplan und `RADON_PROTECT_*`); nicht lesbare
  Befehle und Zeitplan-Einträge werden protokolliert und abgewiesen.
- **Robuster Datenstrom:** Unbekannte Rahmentypen werden protokolliert; Rahmen werden zusätzlich
  am Kopfbyte und an der MAC des Geräts erkannt. Außenwetterdaten werden im lokalen Betrieb
  nicht an die Geräte gesendet.
- **Tests:** 116 Tests in `test_safety_fixes.py` (insgesamt 156 für die Bridge).

### Verbessert

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
