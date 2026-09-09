# Changelog

Alle nennenswerten Änderungen an diesem Repository. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

### Geändert

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
