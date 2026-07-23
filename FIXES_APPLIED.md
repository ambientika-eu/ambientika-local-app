# Fixes applied — `ambientika_local_bridge.py`

Alle 18 im Fehlerbericht dokumentierten Defekte sind behoben. Verifikation:
statische Analyse sauber (ruff, pyflakes, mypy für die kritischen Punkte,
py_compile), **40 Unit-/Integrationstests grün** (die Bug-Tests wurden zu
Regressionstests umgeschrieben, die jetzt das KORREKTE Verhalten prüfen) und ein
**live End-to-End-Smoke-Test mit echtem MQTT-Broker: 13/13** (zweimal
reproduziert, nicht flaky).

## Behobene Punkte

HIGH
- **H1 Schutz-Commit-Reihenfolge + Re-Entrancy:** `self.protection` wird jetzt
  VOR den Schutz-Schreibvorgängen gesetzt; ein `asyncio.Lock` serialisiert
  Befehl/Zeitplan/NeuraCell. Kein Unterdrückungsfenster, kein Doppel-Apply.
- **H2 Dict-Mutation während Iteration:** alle Geräte-Schleifen laufen über
  `list(...)`-Snapshots; ein Verbindungsabbruch mid-apply crasht nicht mehr.
- **H3 Taupunkt-Flattern:** echte ±margin-Hysterese (blockieren bei
  `outdoor ≥ indoor+margin`, freigeben erst bei `outdoor < indoor−margin`).
- **H4 Frame-Desync:** unbekanntes führendes Byte wird verworfen und
  resynchronisiert, statt die Verbindung dauerhaft zu blockieren.

MEDIUM
- **M1 Weather-Payload:** Nicht-Objekt-JSON wird zu `{}` verworfen (kein
  AttributeError mehr).
- **M2 Baseline-Race:** Restore-Baseline stammt aus dem letzten NORMAL-Ziel
  (`normal_codes`), nicht aus dem Geräte-Echo → korrekte Wiederherstellung auch
  bei schnellem Re-Trip.
- **M3 Reconnect:** Writer-Wechsel erhält den Gerätezustand (nur `setup_sent`
  wird zurückgesetzt, Setup wird einmalig neu gesendet).
- **M4 Vorzeichen:** Temperatur und RSSI werden signed dekodiert (−5 °C, −56 dBm)
  → korrekte Taupunkt-Eingangswerte. *(An echter Hardware final bestätigen.)*
- **M5 Publish-Sturm:** `neuracell/state` wird nur bei tatsächlicher Änderung
  (mit `retain`) veröffentlicht.

LOW
- **L1** `HOUSE_ID` wird auf u32 geclampt (kein OverflowError).
- **L2** NIGHT-Stufe ist bewusst einseitig; `fanLevel`-String bleibt verlustfrei.
- **L3** `_apply_command`/Scheduler senden nie ein Frame mit unaufgelöstem Code.
- **L4** Zeiten werden in `normalize_week` auf `HH:MM` genullt.
- **L5** Luftqualität: Rohwert 0 → „UNKNOWN_SENSOR" (nicht mehr „VERY_GOOD"),
  oberes Ende geclampt.
- **L6** paho-Client nutzt die 2.x-Callback-API (VERSION2), 1.x-Fallback bleibt.
- **L7** MQTT-LWT + sauberes Herunterfahren (`bridge/availability` = offline).
- **L8** Zeitplan-Updates laufen über den Loop-Thread; Callback-Fehler werden
  geloggt statt verschluckt.
- **L9** Radon-/Wetter-Werte älter als `NC_INPUT_TTL` (Default 900 s) gelten als
  unbekannt → kein stilles Deaktivieren/Latchen des Schutzes.

Zusätzlich: per-Gerät `send_lock` gegen überlappende `drain()`-Aufrufe.

## Smoke-Test-Abdeckung (live, echter Broker + simuliertes Gerät über TCP)
Setup-Push, Status-Publish (inkl. korrektem signed RSSI −56 dBm), MQTT-Mode-
Befehl → korrektes 13-Byte-Frame am Gerät, Radon→INTAKE/LOW, Befehlsunter-
drückung im Schutz, Radon-Aus→exakter Restore auf BOOST/HIGH, manueller
Taupunkt-Block→OFF und Freigabe→Restore, Framing-Resync nach Störbyte, sauberes
Offline beim Shutdown.

## Vor Produktivbetrieb weiterhin offen
Nur noch der Hardware-Test an einem echten Gerät (die reverse-engineerten
Byte-Offsets, insbesondere das signed-Decoding aus M4). Danach der „Internet
ziehen"-Beweis.
