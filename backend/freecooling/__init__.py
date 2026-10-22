"""
Free-Cooling-Wächter der Ambientika Local App.
==============================================

Ersetzt die Free-Cooling-Entscheidung des SMART-Modus durch eine lokale Regel
mit Mindestabstand, Hysterese, Haltezeit und einem echten Außenwert vom Haus:

    Start:  T_innen > 24 °C  UND  T_außen > 20 °C  UND  (T_innen − T_außen) >= 3,0 K
    Ende:   (T_innen − T_außen) < 1,0 K  ODER  T_innen <= 23,5 °C  ODER  T_außen <= 20 °C
            ODER kein aktueller Außenwert (älter als 60 min)

Die Master laufen im Ruhezustand in AUTO (Wärmerückgewinnung). Der Wächter
schaltet sie zum Kühlen in den Luftstrom Master-Slave und danach exakt zurück.
Jede Umschaltung wird protokolliert (SQLite), damit die Arbeitsweise belegbar ist.

Module:
  controller.py  Regellogik, Geräte-Zustand, Befehle (reine Python-Logik, testbar)
  outdoor.py     Außenwert-Quellen: Sensor im Heimnetz (MQTT/HTTP), OpenWeatherMap
  store.py       SQLite: Konfiguration, Laufzeitzustand, Umschalt-Protokoll
  routes.py      FastAPI-Router /api/freecooling
"""

from .controller import FreeCoolingConfig, FreeCoolingController  # noqa: F401
from .outdoor import OutdoorStore, OutdoorReading                  # noqa: F401
from .store import FreeCoolingStore                                 # noqa: F401
