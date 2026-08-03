"""
Verbindliche Text <-> Zahl-Mappings fuer die lokale Messwert-Historie.
======================================================================

Grundregel (aus der Spezifikation):
  Jeder kategoriale Textwert bleibt erhalten UND bekommt zusaetzlich ein
  numerisches Feld. Die Mappings sind hier EINMAL verbindlich festgelegt und
  dokumentiert. Nichts Bestehendes bricht, weil der Textwert zusaetzlich
  gespeichert wird.

ANKER = der REALE MQTT-Contract der ambientika-mqtt-bridge.
--------------------------------------------------------------------------
Die Bridge publiziert unter  <prefix>/<serial>/state  (JSON, retained) exakt:

    operating_mode      -> str  (OperatingMode.name: Smart|Auto|ManualHeatRecovery|
                                  Night|AwayHome|Surveillance|TimedExpulsion|
                                  Expulsion|Intake|MasterSlaveFlow|SlaveMasterFlow|Off)
    fan_speed           -> str  (FanSpeed.name: Low|Medium|High)
    humidity_level      -> str  (HumidityLevel.name: Dry|Normal|Moist)
    light_sensor_level  -> str  (LightSensorLevel.name: NotAvailable|Off|Low|Medium)
    temperature         -> Zahl (Grad C)
    humidity            -> Zahl (% rF)
    air_quality         -> str  ODER Zahl  (siehe air_quality_normalize)
    humidity_alarm      -> bool
    filters_status      -> str  (green|yellow|red)
    night_alarm         -> bool
    device_role         -> str  (Master|Slave)
    last_operating_mode -> str  (OperatingMode.name)
    zone_index          -> int

  Die Enum-Namen und ihre ZAHLEN stammen 1:1 aus ambientika_py (IntEnum) -
  dadurch gibt es genau EINEN, von der Firmware/Bibliothek definierten
  Nummernkreis fuer den Betriebsmodus. Wir erfinden nichts.

Richtung der Ordinalskalen (bewusst festgelegt):
  * Luftqualitaet  : hoeher = bessere Luft   (0 schlecht .. 4 sehr gut)
  * Filterstatus   : hoeher = dringlicher    (0 gruen, 1 gelb, 2 rot)
  * Luefterstufe   : hoeher = schneller      (1 Low, 2 Medium, 3 High)
  * Feuchtestufe   : hoeher = feuchter/traeger (0 Dry, 1 Normal, 2 Moist)
"""

from typing import Optional, Dict, List, Tuple, Any


def _norm(s: Any) -> Optional[str]:
    """Normalisiert einen Kategorie-String: lower, ohne Rand-/Trennzeichen."""
    if s is None:
        return None
    return str(s).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _as_number(value: Any) -> Optional[float]:
    """Gibt eine Zahl zurueck, falls value numerisch (auch als String) ist, sonst None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 1  Betriebsmodus  (Anker: OperatingMode-IntEnum aus ambientika_py)
# ---------------------------------------------------------------------------
# Zahl = der native Enum-Wert. Namen exakt wie von der Bridge gemeldet (.name).
OPERATING_MODE_NUM: Dict[str, int] = {
    "smart": 0,
    "auto": 1,
    "manualheatrecovery": 2,
    "night": 3,
    "awayhome": 4,
    "surveillance": 5,
    "timedexpulsion": 6,
    "expulsion": 7,
    "intake": 8,
    "masterslaveflow": 9,
    "slavemasterflow": 10,
    "off": 11,
}
# Legacy-/App-Aliasse (alte PWA-Tokens) auf denselben Nummernkreis, damit auch
# aeltere Payloads/Frontend-Kommandos sauber normalisiert werden.
_MODE_ALIASES: Dict[str, int] = {
    "manualhrv": 2, "hrv": 2, "manual": 2,
    "away": 4,
    "monitoring": 5, "surveil": 5,
    "timedexhaust": 6, "texhaust": 6,
    "exhaust": 7, "expel": 7,
    "supply": 8,
    "msflow": 9, "smflow": 10,
    "eco": 0, "boost": 7,   # grobe Alt-Zuordnung; nur fuer Legacy-Daten
}


def mode_to_num(mode: Optional[str]) -> Optional[int]:
    """Modus-Name -> Zahl (nativer OperatingMode-Wert). Unbekannt -> None."""
    key = _norm(mode)
    if key is None:
        return None
    if key in OPERATING_MODE_NUM:
        return OPERATING_MODE_NUM[key]
    return _MODE_ALIASES.get(key)


# ---------------------------------------------------------------------------
# 2  Luefterstufe  (Anker: FanSpeed-Enum Low/Medium/High)
# ---------------------------------------------------------------------------
# Hoeher = schneller. 0 bleibt frei fuer "aus/unbekannt".
FAN_SPEED_NUM: Dict[str, int] = {"low": 1, "medium": 2, "high": 3}
FAN_SPEED_LABELS: Dict[int, str] = {1: "Low", 2: "Medium", 3: "High"}


def fan_speed_to_num(fan: Any) -> Optional[int]:
    """
    Luefter -> Stufe 1..3. Nimmt den Bridge-Namen (Low/Medium/High) ODER
    - fuer Legacy-Daten - einen Prozentwert 0..100 und bucketet ihn.
    """
    key = _norm(fan)
    if key is not None and key in FAN_SPEED_NUM:
        return FAN_SPEED_NUM[key]
    num = _as_number(fan)
    if num is None:
        return None
    if num <= 0:
        return None          # 0 % -> keine Stufe (aus)
    if num <= 33:
        return 1
    if num <= 66:
        return 2
    return 3


def fan_num_to_name(num: Optional[int]) -> Optional[str]:
    """Stufe 1..3 -> FanSpeed-Name (fuer Kommandos an die Bridge)."""
    if num is None:
        return None
    return FAN_SPEED_LABELS.get(int(num))


# ---------------------------------------------------------------------------
# 3  Luftqualitaet  (5 Stufen, Text + Zahl)  - AKZEPTIERT STRING ODER PPM
# ---------------------------------------------------------------------------
# 0 = schlechteste Luft, 4 = beste Luft.
AIR_QUALITY_LABELS: Dict[int, str] = {
    0: "sehr schlecht",
    1: "schlecht",
    2: "mittel",
    3: "gut",
    4: "sehr gut",
}

# 3a) numerischer Weg: VOC/CO2-aehnlicher Rohwert -> Stufe.
#     Hoeherer Wert = schlechtere Luft. (obere_Grenze_exklusiv, num)
#     DEFAULT - gegen die reale Geraeteanzeige kalibrieren (FLAG A).
AIR_QUALITY_VOC_BANDS: List[Tuple[float, int]] = [
    (300, 4),           # < 300        -> sehr gut
    (600, 3),           # 300 .. 599   -> gut
    (1000, 2),          # 600 .. 999   -> befriedigend
    (1500, 1),          # 1000 .. 1499 -> maessig
    (float("inf"), 0),  # >= 1500      -> schlecht
]

# 3b) kategorialer Weg: Geraete-/API-String -> Stufe. Mehrsprachig + tolerant.
#     DEFAULT - gegen die realen Strings der Firmware bestaetigen (FLAG A).
# Kalibriert auf die REALEN 5 Geraetestrings (VeryGood/Good/Medium/Bad/VeryBad),
# entspricht der App-Skala sehr gut..sehr schlecht. Mehrsprachige Synonyme dabei.
AIR_QUALITY_TEXT_NUM: Dict[str, int] = {
    # 4 - sehr gut  (Geraet: "VeryGood")
    "verygood": 4, "sehrgut": 4, "excellent": 4, "ottima": 4, "ottimo": 4,
    "eccellente": 4, "perfect": 4, "best": 4,
    # 3 - gut  (Geraet: "Good")
    "good": 3, "gut": 3, "buona": 3, "buono": 3, "healthy": 3, "fine": 3, "ok": 3,
    # 2 - mittel  (Geraet: "Medium")
    "medium": 2, "mittel": 2, "moderate": 2, "fair": 2, "media": 2, "discreta": 2,
    "acceptable": 2, "average": 2, "normal": 2, "befriedigend": 2,
    # 1 - schlecht  (Geraet: "Bad")
    "bad": 1, "schlecht": 1, "poor": 1, "cattiva": 1, "cattivo": 1, "scarsa": 1,
    "scarso": 1, "mediocre": 1, "unhealthy": 1, "maessig": 1,
    # 0 - sehr schlecht  (Geraet: "VeryBad")
    "verybad": 0, "sehrschlecht": 0, "verypoor": 0, "hazardous": 0, "critical": 0,
    "worst": 0, "severe": 0, "cattivissima": 0,
}


def air_quality_normalize(value: Any) -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Luftqualitaet robust normalisieren.

    Rueckgabe: (voc, text, num)
      voc  : der numerische Rohwert (ppm-aehnlich), falls das Geraet eine Zahl
             liefert - sonst None.
      text : 5-stufige Kategorie (deutsch) wenn klassifizierbar, sonst der
             rohe Geraete-String (unveraendert, damit nichts verloren geht).
      num  : 0..4 (hoeher = besser) oder None bei unbekanntem String.
    """
    if value is None:
        return None, None, None

    # (a) Zahl -> als VOC/CO2 behandeln
    num_val = _as_number(value)
    if num_val is not None:
        for upper, num in AIR_QUALITY_VOC_BANDS:
            if num_val < upper:
                return int(num_val), AIR_QUALITY_LABELS[num], num
        return int(num_val), AIR_QUALITY_LABELS[0], 0

    # (b) String-Kategorie
    key = _norm(value)
    if key in AIR_QUALITY_TEXT_NUM:
        num = AIR_QUALITY_TEXT_NUM[key]
        return None, AIR_QUALITY_LABELS[num], num
    # unbekannt: Rohtext behalten, num offen lassen (bricht nichts)
    return None, str(value).strip(), None


# ---------------------------------------------------------------------------
# 4  Filterstatus  (Ampel, Text + Zahl)
# ---------------------------------------------------------------------------
FILTER_STATUS_LABELS: Dict[int, str] = {0: "gruen", 1: "gelb", 2: "rot"}
# Reale Bridge-/Geraetewerte fuer den Filter sind Good/Medium/Bad (nicht
# green/yellow/red) - beides wird hier auf die Ampel 0/1/2 abgebildet.
FILTER_STATUS_NUM: Dict[str, int] = {
    "green": 0, "gruen": 0, "grun": 0, "verde": 0, "ok": 0, "good": 0, "clean": 0,
    "yellow": 1, "gelb": 1, "giallo": 1, "amber": 1, "warn": 1, "warning": 1,
    "soon": 1, "medium": 1, "moderate": 1,
    "red": 2, "rot": 2, "rosso": 2, "alarm": 2, "replace": 2, "critical": 2,
    "dirty": 2, "bad": 2,
}


def filter_status_normalize(
    value: Any,
    filter_alarm: Optional[bool] = None,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Filter-Ampel aus dem realen filters_status-String (green|yellow|red).
    Fallback (Legacy): ein boolescher filterAlarm -> gruen/rot.
    """
    key = _norm(value)
    if key is not None and key in FILTER_STATUS_NUM:
        num = FILTER_STATUS_NUM[key]
        return FILTER_STATUS_LABELS[num], num
    if isinstance(value, bool):  # falls jemand einen Bool im Statusfeld liefert
        filter_alarm = value
        value = None
    if value not in (None, ""):
        return str(value).strip(), None  # unbekannt: Rohtext behalten
    if filter_alarm is None:
        return None, None
    return (FILTER_STATUS_LABELS[2], 2) if filter_alarm else (FILTER_STATUS_LABELS[0], 0)


def filter_num_is_alarm(num: Optional[int]) -> Optional[bool]:
    """Kompat-Bool fuer die alte PWA: rot (2) = Alarm. gruen/gelb = kein Alarm."""
    if num is None:
        return None
    return num >= 2


# ---------------------------------------------------------------------------
# 5  Feuchtestufe  (Anker: HumidityLevel-Enum Dry/Normal/Moist)
# ---------------------------------------------------------------------------
# Das ist die eingestellte Feuchte-SCHWELLE (Empfindlichkeit) des Geraets.
HUMIDITY_LEVEL_NUM: Dict[str, int] = {"dry": 0, "normal": 1, "moist": 2}


def humidity_level_to_num(level: Optional[str]) -> Optional[int]:
    key = _norm(level)
    if key is None:
        return None
    return HUMIDITY_LEVEL_NUM.get(key)


# ---------------------------------------------------------------------------
# 6  Lichtsensor-Stufe  (Anker: LightSensorLevel-Enum)
# ---------------------------------------------------------------------------
LIGHT_SENSOR_NUM: Dict[str, int] = {"notavailable": 0, "off": 1, "low": 2, "medium": 3}


def light_sensor_to_num(level: Optional[str]) -> Optional[int]:
    key = _norm(level)
    if key is None:
        return None
    return LIGHT_SENSOR_NUM.get(key)


# ---------------------------------------------------------------------------
# 7  Rolle  (Master/Slave, Text + Zahl)
# ---------------------------------------------------------------------------
ROLE_NUM: Dict[str, int] = {
    "slave": 0, "secondary": 0, "sekundaer": 0,
    "master": 1, "primary": 1, "haupt": 1,
}


def role_to_num(role: Optional[str]) -> Optional[int]:
    key = _norm(role)
    if key is None:
        return None
    return ROLE_NUM.get(key)


# ---------------------------------------------------------------------------
# 8  Bool -> 0/1
# ---------------------------------------------------------------------------
def bool_to_int(value: Any) -> Optional[int]:
    """Boolean/Truthy -> 0/1, None bleibt None."""
    if value is None:
        return None
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "on", "yes", "online") else 0
    return 1 if bool(value) else 0


# ---------------------------------------------------------------------------
# Offene Punkte, die gegen die reale Firmware final bestaetigt werden muessen.
# ---------------------------------------------------------------------------
FLAGS = {
    "A_air_quality":
        "air_quality kommt real als STRING (5 Stufen: VeryGood/Good/Medium/Bad/"
        "VeryBad) - selten als Zahl. AIR_QUALITY_TEXT_NUM ist auf genau diese "
        "Geraetestrings kalibriert; die VOC-Baender greifen nur, falls doch eine "
        "Zahl geliefert wird.",
    "E_filter_strings":
        "filters_status kommt real als Good/Medium/Bad (nicht green/yellow/red); "
        "FILTER_STATUS_NUM bildet beide Vokabulare auf 0/1/2 ab.",
    "B_role_numeric":
        "device_role wird als String (Master/Slave) erwartet. Falls die Firmware "
        "einen Zahlencode liefert, Zuordnung in ROLE_NUM ergaenzen/bestaetigen.",
    "C_fan_stage":
        "FanSpeed ist real 3-stufig (Low/Medium/High -> 1/2/3). Es gibt KEINE "
        "separate Nachtdrehzahl als Fan-Wert; 'Nacht' ist ein Betriebsmodus.",
    "D_mode_enum":
        "Betriebsmodus-Nummern = native OperatingMode-Enumwerte (Smart=0 .. Off=11) "
        "aus ambientika_py. Bei neuen Firmware-Modi OPERATING_MODE_NUM ergaenzen.",
}
