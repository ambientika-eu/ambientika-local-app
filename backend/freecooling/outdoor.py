"""
Außenwert-Quellen des Free-Cooling-Wächters.
============================================

Der SMART-Modus der Geräte vergleicht den Innenwert mit dem Außenwert einer
Wetterstation in der Umgebung. Genau dieser Vergleich kippt, wenn die Werte
nah beieinander liegen. Der Wächter arbeitet deshalb bevorzugt mit einem
Sensor am Haus und nutzt eine Wetterstation nur als Rückfallebene.

Zwei Arten von Quellen, in dieser Reihenfolge:

  sensor   ein Fühler im Heimnetz — per MQTT auf <prefix>/weather
           ({"temperature": t, "humidity": rh}, wie bei der lokalen Bridge) oder
           <prefix>/dewpoint/outdoor_temp (nackte Zahl, wie bei der NeuraCell-X
           Taupunktberechnung der Cloud-Bridge), oder per HTTP
           POST /api/freecooling/outdoor.
  weather  OpenWeatherMap, vom Backend abgefragt (OWM_API_KEY + OWM_LOCATION)
           oder von der PWA gemeldet (sie fragt OWM ohnehin alle 15 min ab).

Ein Wert gilt nur, solange er jünger als die Gültigkeitsdauer ist (Standard
60 min). Fehlt ein gültiger Wert, startet kein Free Cooling, ein laufendes
Free Cooling wird beendet.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("ambientika-freecooling")

KINDS = ("sensor", "weather")           # Reihenfolge = Priorität


@dataclass
class OutdoorReading:
    temperature: float
    humidity: Optional[float]
    kind: str                           # sensor | weather
    source: str                         # Klartext, z.B. "mqtt:ambientika/weather"
    ts: datetime                        # Empfangszeit (UTC)

    def age_s(self, now: datetime) -> int:
        return max(0, int((now - self.ts).total_seconds()))

    def label(self) -> str:
        return f"{self.kind}:{self.source}"

    def as_dict(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        return {
            "temperature": self.temperature,
            "humidity": self.humidity,
            "kind": self.kind,
            "source": self.source,
            "ts": self.ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_s": self.age_s(now),
        }


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if f != f:                          # NaN
        return None
    return f


def parse_payload(raw: str) -> Tuple[Optional[float], Optional[float]]:
    """MQTT-Nutzlast -> (temperature, humidity).

    Akzeptiert ein JSON-Objekt mit temperature/humidity (auch temp/hum,
    temperature_c) oder eine nackte Zahl (nur Temperatur).
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    try:
        data = json.loads(s)
    except ValueError:
        return _num(s), None
    if isinstance(data, dict):
        t = _num(data.get("temperature", data.get("temp", data.get("temperature_c"))))
        h = _num(data.get("humidity", data.get("hum", data.get("rh"))))
        return t, h
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return float(data), None
    return None, None


def plausible(t: Optional[float]) -> bool:
    """Grobe Plausibilität: ein Außensensor liefert zwischen -50 und +60 °C."""
    return t is not None and -50.0 <= t <= 60.0


class OutdoorStore:
    """Hält je Quellenart den letzten Wert und liefert den gültigen Wert."""

    def __init__(self, ttl_s: int = 3600):
        self.ttl_s = int(ttl_s)
        self._readings: Dict[str, OutdoorReading] = {}

    def update(self, kind: str, temperature: Any, humidity: Any = None,
               source: str = "", ts: Optional[datetime] = None) -> Optional[OutdoorReading]:
        if kind not in KINDS:
            log.warning("freecooling: unbekannte Außenwert-Art %r", kind)
            return None
        t = _num(temperature)
        if not plausible(t):
            log.warning("freecooling: Außenwert %r (%s) verworfen", temperature, source)
            return None
        r = OutdoorReading(temperature=t, humidity=_num(humidity), kind=kind,
                           source=source or kind, ts=ts or datetime.now(timezone.utc))
        self._readings[kind] = r
        return r

    def set_humidity(self, kind: str, humidity: Any) -> bool:
        """Feuchte nachtragen (eigenes Topic), ohne den Zeitstempel zu erneuern."""
        r = self._readings.get(kind)
        h = _num(humidity)
        if r is None or h is None:
            return False
        r.humidity = h
        return True

    def current(self, now: Optional[datetime] = None,
                ttl_s: Optional[int] = None) -> Optional[OutdoorReading]:
        """Der gültige Außenwert: Sensor vor Wetterstation, nur wenn frisch."""
        now = now or datetime.now(timezone.utc)
        ttl = self.ttl_s if ttl_s is None else int(ttl_s)
        for kind in KINDS:
            r = self._readings.get(kind)
            if r is not None and r.age_s(now) <= ttl:
                return r
        return None

    def latest(self) -> Optional[OutdoorReading]:
        """Der jüngste Wert, auch wenn er schon abgelaufen ist (Anzeige)."""
        rs = list(self._readings.values())
        return max(rs, key=lambda r: r.ts) if rs else None

    def as_dict(self, now: Optional[datetime] = None, ttl_s: Optional[int] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        cur = self.current(now, ttl_s)
        return {
            "current": cur.as_dict(now) if cur else None,
            "valid": cur is not None,
            "ttl_s": self.ttl_s if ttl_s is None else int(ttl_s),
            "readings": {k: r.as_dict(now) for k, r in self._readings.items()},
        }


# ---------------------------------------------------------------------------
# OpenWeatherMap (Rückfallebene) — synchron, für asyncio.to_thread
# ---------------------------------------------------------------------------
OWM_BASE = "https://api.openweathermap.org"


def _get_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "ambientika-local-app"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (feste Host-URL)
        return json.loads(resp.read().decode("utf-8"))


def resolve_location(api_key: str, location: str, fetch=_get_json) -> Tuple[float, float, str]:
    """'lat,lon' oder Ortsname -> (lat, lon, Anzeigename)."""
    loc = (location or "").strip()
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) == 2:
        lat, lon = _num(parts[0]), _num(parts[1])
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon, loc
    url = (f"{OWM_BASE}/geo/1.0/direct?q={urllib.parse.quote(loc)}&limit=1"
           f"&appid={urllib.parse.quote(api_key)}")
    data = fetch(url)
    if not isinstance(data, list) or not data:
        raise ValueError(f"Ort nicht gefunden: {loc}")
    hit = data[0]
    name = ", ".join(x for x in (hit.get("name"), hit.get("country")) if x)
    return float(hit["lat"]), float(hit["lon"]), name or loc


def fetch_openweathermap(api_key: str, lat: float, lon: float,
                         fetch=_get_json) -> Tuple[float, Optional[float], str]:
    """Aktuelles Wetter -> (temperature °C, humidity %, Stationsname)."""
    url = (f"{OWM_BASE}/data/2.5/weather?lat={lat}&lon={lon}&units=metric"
           f"&appid={urllib.parse.quote(api_key)}")
    data = fetch(url)
    main = data.get("main") if isinstance(data, dict) else None
    if not isinstance(main, dict) or _num(main.get("temp")) is None:
        raise ValueError(f"Unerwartete OWM-Antwort: {str(data)[:120]}")
    name = data.get("name") or f"{lat},{lon}"
    return float(main["temp"]), _num(main.get("humidity")), str(name)
