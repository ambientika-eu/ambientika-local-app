"""
Verbindliche Text <-> Zahl-Mappings fuer die lokale Messwert-Historie.
====================================================================

Grundregel (aus der Spezifikation):
  Jeder kategoriale Textwert bleibt erhalten UND bekommt zusaetzlich ein
  numerisches Feld. Die Mappings sind hier EINMAL verbindlich festgelegt und
  dokumentiert. Nichts Bestehendes bricht, weil der Textwert zusaetzlich
  gespeichert wird.

Abgleich mit der Realitaet (Stand: ambientika-mqtt-bridge Status-Payload):
  Der Bridge-Status liefert:
    mode        -> String (HRV | NIGHT | BOOST | ECO | SMART | OFF ...)
    fanSpeed    -> int 0..100 (Prozent, KEINE Stufe)
    airQuality  -> int (VOC-Rohwert, z.B. 850; Smart-Schwelle 600), KEINE 5 Stufen
    filterAlarm -> bool (nur an/aus, KEIN gruen/gelb/rot)
    rssi        -> int dBm (Funkguete IST vorhanden)
    serial      -> String (Schluessel IST vorhanden)

  Deshalb: die numerischen Rohwerte (Prozent, VOC, dBm) werden 1:1 gespeichert,
  UND es werden zusaetzlich die App-naehen Kategorien abgeleitet
  (Luftqualitaet 0..4, Luefterstufe, Filter-Ampel). Die Ableitungsschwellen
  sind konfigurierbar und unten als Default hinterlegt -> gegen die App final
  bestaetigen (siehe FLAGS am Dateiende).

Richtung der Ordinalskalen (bewusst festgelegt):
  * Luftqualitaet  : hoeher = bessere Luft   (0 schlecht .. 4 sehr gut)
  * Filterstatus   : hoeher = dringlicher    (0 gruen, 1 gelb, 2 rot)
"""

from typing import Optional, Dict, List, Tuple

# ---------------------------------------------------------------------------
# 3.1  Luftqualitaet  (5 Stufen, Text + Zahl)
# ---------------------------------------------------------------------------
# Reihenfolge fix: 0 = schlechteste Luft, 4 = beste Luft.
AIR_QUALITY_LABELS: Dict[int, str] = {
    0: "schlecht",
    1: "maessig",
    2: "befriedigend",
    3: "gut",
    4: "sehr gut",
}
AIR_QUALITY_NUM: Dict[str, int] = {v: k for k, v in AIR_QUALITY_LABELS.items()}

# Ableitung aus dem VOC-Rohwert (airQuality im Bridge-Payload).
# Hoeherer VOC = schlechtere Luft. Liste (obere_Grenze_exklusiv, num).
# DEFAULT - gegen die App bestaetigen (FLAG A).
AIR_QUALITY_VOC_BANDS: List[Tuple[float, int]] = [
    (300, 4),    # < 300        -> sehr gut
    (600, 3),    # 300 .. 599   -> gut
    (1000, 2),   # 600 .. 999   -> befriedigend
    (1500, 1),   # 1000 .. 1499 -> maessig
    (float("inf"), 0),  # >= 1500 -> schlecht
]


def air_quality_from_voc(voc: Optional[float]) -> Tuple[Optional[str], Optional[int]]:
    """VOC-Rohwert -> (Text, Zahl) der 5-stufigen Luftqualitaet."""
    if voc is None:
        return None, None
    for upper, num in AIR_QUALITY_VOC_BANDS:
        if voc < upper:
            return AIR_QUALITY_LABELS[num], num
    return AIR_QUALITY_LABELS[0], 0


# ---------------------------------------------------------------------------
# 3.2  Filterstatus  (Ampel, Text + Zahl)
# ---------------------------------------------------------------------------
FILTER_STATUS_LABELS: Dict[int, str] = {0: "gruen", 1: "gelb", 2: "rot"}
FILTER_STATUS_NUM: Dict[str, int] = {
    "gruen": 0, "grün": 0, "green": 0,
    "gelb": 1, "yellow": 1,
    "rot": 2, "red": 2,
}


def filter_status_from_alarm(
    filter_alarm: Optional[bool],
    filter_status_text: Optional[str] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Bevorzugt einen echten Ampel-Text (falls die Bridge ihn spaeter liefert),
    sonst Ableitung aus dem heutigen Boolean:
        filterAlarm == False -> gruen (0)
        filterAlarm == True  -> rot   (2)
    'gelb' (1) ist im heutigen Payload NICHT enthalten (FLAG B).
    """
    if filter_status_text:
        key = filter_status_text.strip().lower()
        num = FILTER_STATUS_NUM.get(key)
        if num is not None:
            return FILTER_STATUS_LABELS[num], num
    if filter_alarm is None:
        return None, None
    return (FILTER_STATUS_LABELS[2], 2) if filter_alarm else (FILTER_STATUS_LABELS[0], 0)


# ---------------------------------------------------------------------------
# 3.3  Luefterstufe  (Text + Zahl, inkl. Nachtgeschwindigkeit)
# ---------------------------------------------------------------------------
# Primaerwert bleibt der Prozentwert (fanSpeed 0..100) - der IST bereits eine
# Zahl inkl. Nachtgeschwindigkeit. Zusaetzlich eine grobe Stufe fuer die
# App-Darstellung. Bands: (obere_Grenze_inklusiv, num, label).
# DEFAULT - gegen die App bestaetigen (FLAG C).
FAN_STAGE_BANDS: List[Tuple[int, int, str]] = [
    (0, 0, "Aus"),
    (33, 1, "Stufe 1"),
    (66, 2, "Stufe 2"),
    (100, 3, "Stufe 3"),
]


def fan_stage_from_percent(pct: Optional[int]) -> Tuple[Optional[str], Optional[int]]:
    """fanSpeed-Prozent -> (Text, Zahl) einer groben Luefterstufe."""
    if pct is None:
        return None, None
    for upper, num, label in FAN_STAGE_BANDS:
        if pct <= upper:
            return label, num
    return FAN_STAGE_BANDS[-1][2], FAN_STAGE_BANDS[-1][1]


# ---------------------------------------------------------------------------
# 3.4  Betriebsmodus  (durchnummerierte Aufzaehlung, Text + Zahl)
# ---------------------------------------------------------------------------
# WICHTIG: Anker sind die real von der Bridge/Firmware gemeldeten Modus-Strings
# (HRV | NIGHT | BOOST | ECO | SMART | OFF), NICHT die 12 deutschen Namen aus
# dem App-Handbuch. Damit gibt es genau einen Nummernkreis. Die Zuordnung zu den
# Handbuch-Begriffen steht als Kommentar dabei. Neue/unbekannte Modi brechen
# nichts: Text wird gespeichert, num bleibt None (siehe unknown-Handling).
MODE_NUM: Dict[str, int] = {
    "OFF": 0,      # AUS
    "SMART": 1,    # SMART
    "HRV": 2,      # (manuelle) Waermerueckgewinnung
    "NIGHT": 3,    # NACHT
    "ECO": 4,      # Eco / minimale Waermerueckgewinnung
    "BOOST": 5,    # Boost / zeitgesteuerter Ablauf bei max. Geschwindigkeit
    # --- Platz fuer weitere native Modi, sobald die Bridge sie meldet: ---
    # "AWAY": 6,           # AUSSER HAUS
    # "SURVEILLANCE": 7,   # UEBERWACHUNG
    # "EXHAUST": 8,        # ABLUFT
    # "INTAKE": 9,         # ZULUFT
    # "MASTER_SLAVE": 10,  # MASTER-SLAVE-DURCHFLUSS
    # "SLAVE_MASTER": 11,  # SLAVE-MASTER-DURCHFLUSS
}
MODE_LABELS: Dict[int, str] = {v: k for k, v in MODE_NUM.items()}


def mode_to_num(mode: Optional[str]) -> Optional[int]:
    """Modus-String -> Zahl. Unbekannter Modus -> None (Text bleibt erhalten)."""
    if mode is None:
        return None
    return MODE_NUM.get(mode.strip().upper())


# ---------------------------------------------------------------------------
# 3.5  Rolle  (Master/Slave, Text + Zahl)
# ---------------------------------------------------------------------------
ROLE_NUM: Dict[str, int] = {"slave": 0, "master": 1}


def role_to_num(role: Optional[str]) -> Optional[int]:
    if role is None:
        return None
    return ROLE_NUM.get(role.strip().lower())


def bool_to_int(value) -> Optional[int]:
    """Boolean/Truthy -> 0/1, None bleibt None."""
    if value is None:
        return None
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "on", "yes") else 0
    return 1 if bool(value) else 0


# ---------------------------------------------------------------------------
# Offene Punkte, die gegen App/Firmware final bestaetigt werden muessen.
# ---------------------------------------------------------------------------
FLAGS = {
    "A_air_quality_voc_bands":
        "VOC-Schwellen fuer die 5 Luftqualitaetsstufen gegen die App-Anzeige "
        "kalibrieren (aktuell Default 300/600/1000/1500).",
    "B_filter_yellow":
        "Bridge liefert heute nur filterAlarm (bool) -> nur gruen/rot. 'gelb' "
        "erst moeglich, wenn die Bridge einen echten Filterstatus/Reststunden "
        "publiziert.",
    "C_fan_stage_bands":
        "Prozent->Stufe-Grenzen (0/33/66/100) gegen die App bestaetigen; "
        "Rohprozent bleibt ohnehin als fan_speed_pct erhalten.",
    "D_mode_enum":
        "Modus-Nummerierung ist an die realen Bridge-Strings gebunden "
        "(HRV/NIGHT/BOOST/ECO/SMART/OFF), nicht an die 12 Handbuch-Namen. "
        "Weitere native Modi bei Bedarf in MODE_NUM ergaenzen.",
}
