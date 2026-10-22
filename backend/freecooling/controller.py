"""
Regellogik des Free-Cooling-Wächters.
=====================================

Reine Python-Logik ohne asyncio und ohne MQTT-Abhängigkeit: Der Controller
bekommt die Geräteliste, eine Sendefunktion, den Speicher und die Außenwerte
von außen und wird per ``tick(now)`` aufgerufen. Dadurch ist jede Entscheidung
mit festgesetzter Zeit testbar.

Regel je gesteuertem Gerät (Standardwerte, alle konfigurierbar):

  Start   T_innen > 24 °C  UND  T_außen > 20 °C  UND  (T_innen − T_außen) >= 3,0 K
  Ende    (T_innen − T_außen) < 1,0 K  ODER  T_innen <= 23,5 °C  ODER  T_außen <= 19,7 °C
  Sofort  Außenluft wärmer als innen, kein gültiger Außenwert (älter als 60 min),
          Wächter aus, Gerät nicht mehr in der Geräteliste
  Halten  nach jedem Umschalten mindestens 15 min im neuen Zustand

Zum Kühlen wird der Luftstrom Master→Slave gesetzt (tagsüber Stufe 2, nachts
Stufe 1), wie es auch die SMART-Firmware beim Free Cooling tut. Der Innensensor
sitzt unten im Gehäuse, nicht direkt im Luftstrom, und erfasst die Raumtemperatur;
den verbleibenden Einfluss des Luftstroms fangen der gemittelte Wert der Bridge
(temperature_avg) und die Hysterese am Ende ab. Slave→Master ist als cool_mode
wählbar (der Master saugt dann ab). Danach wird der Zustand vor dem Kühlen
exakt wiederhergestellt. War das SMART,
wird stattdessen AUTO gesetzt, weil der Wächter genau die Free-Cooling-
Entscheidung des SMART-Modus ersetzt. Solange der Wächter aktiv ist, hält er
die gesteuerten Geräte aus SMART heraus (abschaltbar).

Robustheit gegen die Realität der Bridge (Abfrage alle 30 s, Befehle können
verzögert ankommen, NeuraCell-X greift dazwischen):
  * Ein Start gilt erst als bestätigt, wenn das Gerät den Kühlmodus gemeldet hat.
    Bleibt die Bestätigung aus, ist das kein Eingriff des Nutzers, sondern ein
    nicht angekommener Befehl; kommt der Befehl später doch an, übernimmt der
    Wächter das Kühlen ("adopted") und beendet es nach seiner Regel.
  * Nach einer NeuraCell-X-Schutzphase und nach einer Unterbrechung der Bridge
    gilt eine Schonfrist, bevor ein abweichender Modus als Eingriff zählt.
  * Eine Rückschaltung wird wiederholt, bis das Gerät sie meldet; nach drei
    Fehlversuchen ist das als "restore_failed" sichtbar.
  * Gerätewerte, die älter als state_ttl_s sind, oder eine Bridge, die sich
    offline meldet, sperren jede Entscheidung.

Ein Eingriff des Nutzers (Modus am Gerät oder in der App geändert, nachdem der
Wächter bestätigt kühlt) wird erkannt: Der Wächter zieht sich zurück, stellt
nichts um und startet für eine Pause (Standard 60 min) nicht neu. Läuft eine
NeuraCell-X-Schutzfunktion (Radon, Taupunkt), sendet der Wächter nichts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .outdoor import OutdoorReading, OutdoorStore
from .store import FreeCoolingStore, utc_iso

log = logging.getLogger("ambientika-freecooling")

OPERATING_MODES = (
    "Smart", "Auto", "ManualHeatRecovery", "Night", "AwayHome", "Surveillance",
    "TimedExpulsion", "Expulsion", "Intake", "MasterSlaveFlow", "SlaveMasterFlow", "Off",
)
COOL_MODES = ("MasterSlaveFlow", "SlaveMasterFlow")
# Nur aus diesen Ruhezuständen heraus startet der Wächter. Aus, Außer Haus,
# Überwachung und manuell gewählte Luftstrom-Modi sind Entscheidungen des
# Nutzers und bleiben unangetastet; SMART wird (bei block_smart) erst ersetzt.
START_MODES = ("Auto", "ManualHeatRecovery", "Night")
FAN_SPEEDS = ("Low", "Medium", "High")
MIN_HOLD_S = 60            # Untergrenzen, die auch die API nicht unterschreiten darf
MIN_GRACE_S = 60           # zwei Abfragen der Bridge (30 s)
RESTORE_FAST_ATTEMPTS = 5  # danach nur noch alle RESTORE_SLOW_S
RESTORE_SLOW_S = 1800
RESTORE_FAILED_AFTER = 3   # ab so vielen Wiederholungen sichtbar als restore_failed
SMART_BLOCK_FAST = 3       # danach nur noch stündlich
SMART_BLOCK_SLOW_S = 3600
_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _envb(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _parse_hours(spec: str) -> Optional[Tuple[int, int]]:
    """'22:00-06:00' -> (1320, 360) Minuten; None wenn leer/ungültig."""
    m = _HOURS_RE.match(spec or "")
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 < 24 and 0 <= m1 < 60 and 0 <= h2 < 24 and 0 <= m2 < 60):
        return None
    return h1 * 60 + m1, h2 * 60 + m2


def _in_hours(span: Tuple[int, int], minute_of_day: int) -> bool:
    start, end = span
    if start == end:
        return False
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end        # über Mitternacht


def _is_unit(state: Any) -> bool:
    """Nur Einträge mit Gerätezustand; <prefix>/bridge/availability legt im
    devices-Dict des Backends einen Pseudo-Eintrag ohne Zustand an."""
    return isinstance(state, dict) and ("operating_mode" in state or "temperature" in state)


def _sendable_fan(fan: Any, default: str = "Medium") -> str:
    """Nur Low/Medium/High lassen sich senden; Night/Turbo sind Meldewerte."""
    s = str(fan or "").strip()
    if s in FAN_SPEEDS:
        return s
    low = s.lower()
    if low == "night":
        return "Low"
    if low == "turbo":
        return "High"
    return default


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
@dataclass
class FreeCoolingConfig:
    enabled: bool = False
    devices: List[str] = field(default_factory=list)   # Seriennummern; leer = alle Master
    t_in_min: float = 24.0
    t_out_min: float = 20.0
    t_out_hyst: float = 0.3          # Ende erst bei außen <= t_out_min − t_out_hyst (kein Pendeln um 20,0)
    delta_on: float = 3.0
    delta_off: float = 1.0
    min_hold_s: int = 900
    outdoor_ttl_s: int = 3600
    state_ttl_s: int = 600           # Gerätewerte älter als das gelten als nicht vorhanden
    cool_mode: str = "MasterSlaveFlow"
    fan_day: str = "Medium"
    fan_night: str = "Low"
    night_hours: str = ""            # "22:00-06:00" oder leer (dann nur Dämmerungssensor)
    rest_mode: str = "Auto"          # ersetzt SMART beim Zurückschalten
    block_smart: bool = True
    override_pause_s: int = 3600
    confirm_grace_s: int = 180       # Zeit, bis die Bridge einen gesendeten Modus meldet
    adopt_window_s: int = 1800       # so lange wird ein verspätet angekommener Start übernommen
    use_avg_temperature: bool = True

    @classmethod
    def from_env(cls) -> "FreeCoolingConfig":
        c = cls()
        c.enabled = _envb("FREECOOLING_ENABLED", c.enabled)
        devs = os.getenv("FREECOOLING_DEVICES", "")
        c.devices = [d.strip() for d in devs.split(",") if d.strip()]
        c.t_in_min = _envf("FREECOOLING_T_IN_MIN", c.t_in_min)
        c.t_out_min = _envf("FREECOOLING_T_OUT_MIN", c.t_out_min)
        c.t_out_hyst = _envf("FREECOOLING_T_OUT_HYST", c.t_out_hyst)
        c.delta_on = _envf("FREECOOLING_DELTA_ON", c.delta_on)
        c.delta_off = _envf("FREECOOLING_DELTA_OFF", c.delta_off)
        c.min_hold_s = _envi("FREECOOLING_MIN_HOLD_S", c.min_hold_s)
        c.outdoor_ttl_s = _envi("FREECOOLING_OUTDOOR_TTL_S", c.outdoor_ttl_s)
        c.state_ttl_s = _envi("FREECOOLING_STATE_TTL_S", c.state_ttl_s)
        c.cool_mode = os.getenv("FREECOOLING_COOL_MODE", c.cool_mode)
        c.fan_day = os.getenv("FREECOOLING_FAN_DAY", c.fan_day)
        c.fan_night = os.getenv("FREECOOLING_FAN_NIGHT", c.fan_night)
        c.night_hours = os.getenv("FREECOOLING_NIGHT_HOURS", c.night_hours)
        c.rest_mode = os.getenv("FREECOOLING_REST_MODE", c.rest_mode)
        c.block_smart = _envb("FREECOOLING_BLOCK_SMART", c.block_smart)
        c.override_pause_s = _envi("FREECOOLING_OVERRIDE_PAUSE_S", c.override_pause_s)
        c.confirm_grace_s = _envi("FREECOOLING_CONFIRM_GRACE_S", c.confirm_grace_s)
        c.adopt_window_s = _envi("FREECOOLING_ADOPT_WINDOW_S", c.adopt_window_s)
        c.use_avg_temperature = _envb("FREECOOLING_USE_AVG_TEMPERATURE", c.use_avg_temperature)
        return c.validated()

    @classmethod
    def from_dict(cls, data: Dict[str, Any], base: Optional["FreeCoolingConfig"] = None) -> "FreeCoolingConfig":
        """Teil-Update: Felder aus data über base (oder Standard) legen und prüfen."""
        c = FreeCoolingConfig(**asdict(base)) if base is not None else cls()
        known = {f.name for f in fields(cls)}
        for k, v in (data or {}).items():
            if k not in known or v is None:
                continue
            setattr(c, k, v)
        return c.validated()

    def validated(self) -> "FreeCoolingConfig":
        errors: List[str] = []
        self.enabled = bool(self.enabled)
        self.block_smart = bool(self.block_smart)
        self.use_avg_temperature = bool(self.use_avg_temperature)
        if isinstance(self.devices, str):
            self.devices = [d.strip() for d in self.devices.split(",") if d.strip()]
        self.devices = [str(d).strip() for d in (self.devices or []) if str(d).strip()]
        for name in ("t_in_min", "t_out_min", "t_out_hyst", "delta_on", "delta_off"):
            v = _num(getattr(self, name))
            if v is None:
                errors.append(f"{name}: Zahl erwartet")
            else:
                setattr(self, name, float(v))
        for name in ("min_hold_s", "outdoor_ttl_s", "state_ttl_s", "override_pause_s",
                     "confirm_grace_s", "adopt_window_s"):
            v = _num(getattr(self, name))
            if v is None or v < 0:
                errors.append(f"{name}: Sekunden >= 0 erwartet")
            else:
                setattr(self, name, int(v))
        if not errors:
            if self.delta_on <= 0:
                errors.append("delta_on muss größer als 0 sein")
            if self.delta_off < 0 or self.delta_off >= self.delta_on:
                errors.append("delta_off muss zwischen 0 und delta_on liegen")
            if self.t_out_hyst < 0 or self.t_out_hyst > 5:
                errors.append("t_out_hyst: 0 bis 5 K")
            if self.outdoor_ttl_s < 60:
                errors.append("outdoor_ttl_s: mindestens 60 s")
            if self.state_ttl_s < 60:
                errors.append("state_ttl_s: mindestens 60 s")
            if self.min_hold_s < MIN_HOLD_S:
                errors.append(f"min_hold_s: mindestens {MIN_HOLD_S} s")
            if self.confirm_grace_s < MIN_GRACE_S:
                errors.append(f"confirm_grace_s: mindestens {MIN_GRACE_S} s")
        self.cool_mode = str(self.cool_mode).strip()
        if self.cool_mode not in COOL_MODES:
            errors.append(f"cool_mode: einer von {', '.join(COOL_MODES)}")
        self.rest_mode = str(self.rest_mode).strip()
        if self.rest_mode not in START_MODES:
            errors.append(f"rest_mode: einer von {', '.join(START_MODES)}")
        for name in ("fan_day", "fan_night"):
            v = str(getattr(self, name)).strip().capitalize()
            if v not in FAN_SPEEDS:
                errors.append(f"{name}: Low, Medium oder High")
            setattr(self, name, v)
        self.night_hours = str(self.night_hours or "").strip()
        if self.night_hours and _parse_hours(self.night_hours) is None:
            errors.append("night_hours: Format HH:MM-HH:MM, z.B. 22:00-06:00")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Laufzeitzustand je Gerät
# ---------------------------------------------------------------------------
@dataclass
class DeviceState:
    serial: str
    cooling: bool = False
    confirmed: bool = False                   # Gerät hat den Kühlmodus seit dem Start gemeldet
    since: Optional[datetime] = None          # letztes Umschalten durch den Wächter (Haltezeit)
    grace_until: Optional[datetime] = None    # bis dahin zählt ein abweichender Modus nicht als Eingriff
    baseline_mode: Optional[str] = None       # Zustand vor dem Kühlen
    baseline_fan: Optional[str] = None
    night: Optional[bool] = None              # Nachtstufe aktiv (während des Kühlens)
    override_until: Optional[datetime] = None
    last_cmd_mode: Optional[str] = None       # zuletzt gesendeter Modus (für verspätete Befehle)
    last_cmd_fan: Optional[str] = None
    last_cmd_ts: Optional[datetime] = None
    restore_mode: Optional[str] = None        # nach dem Ende: erwarteter Modus, bis bestätigt
    restore_fan: Optional[str] = None
    restore_attempts: int = 0
    restore_next: Optional[datetime] = None
    smart_blocked_at: Optional[datetime] = None
    smart_block_count: int = 0
    stale: bool = False                       # Gerätewerte zuletzt zu alt / Bridge offline
    last_event: Optional[str] = None
    last_reason: Optional[str] = None
    last_event_ts: Optional[datetime] = None

    _DT = ("since", "grace_until", "override_until", "last_cmd_ts", "restore_next",
           "smart_blocked_at", "last_event_ts")

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in self._DT:
            v = getattr(self, k)
            d[k] = utc_iso(v) if v else None
        return d

    @classmethod
    def from_json(cls, serial: str, data: Dict[str, Any], now: Optional[datetime] = None) -> "DeviceState":
        s = cls(serial=serial)
        for f in fields(cls):
            if f.name == "serial" or f.name not in data:
                continue
            v = data[f.name]
            if f.name in cls._DT:
                v = _parse_iso(v)
                # Uhrensprung (z.B. Raspberry Pi ohne RTC): Zeiten aus der Zukunft
                # würden Haltezeit/Pause auf Stunden bis Jahre strecken.
                if v is not None and now is not None and v > now:
                    v = now
            setattr(s, f.name, v)
        s.cooling = bool(s.cooling)
        s.confirmed = bool(s.confirmed)
        s.stale = bool(s.stale)
        s.restore_attempts = int(s.restore_attempts or 0)
        s.smart_block_count = int(s.smart_block_count or 0)
        return s


def _parse_iso(v: Any) -> Optional[datetime]:
    if not v or not isinstance(v, str):
        return None
    try:
        dt = datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class FreeCoolingController:
    """Trifft je Tick die Entscheidung für jedes gesteuerte Gerät.

    get_devices()      -> {serial: state} (das devices-Dict des Backends)
    publish(serial, {"operating_mode": .., "fan_speed": ..}) -> sendet an die Bridge;
                          wirft eine Exception, wenn nichts gesendet werden konnte
    get_protection()   -> True, solange NeuraCell-X (Radon/Taupunkt) eingreift
    on_change(status)  -> Hook für den WebSocket-Push
    now_fn()           -> aktuelle Zeit, UTC-bewusst (für Tests austauschbar)
    local_tz           -> Zeitzone für night_hours (Standard: Systemzone)
    """

    def __init__(self, get_devices: Callable[[], Dict[str, Dict[str, Any]]],
                 publish: Callable[[str, Dict[str, Any]], None],
                 store: FreeCoolingStore,
                 outdoor: Optional[OutdoorStore] = None,
                 config: Optional[FreeCoolingConfig] = None,
                 get_protection: Optional[Callable[[], bool]] = None,
                 on_change: Optional[Callable[[Dict[str, Any]], None]] = None,
                 now_fn: Optional[Callable[[], datetime]] = None,
                 local_tz=None):
        self.get_devices = get_devices
        self.publish = publish
        self.store = store
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.local_tz = local_tz
        self.get_protection = get_protection or (lambda: False)
        self.on_change = on_change
        # Konfiguration: gespeicherte Werte gewinnen über Umgebungsvariablen,
        # damit eine Änderung aus der Oberfläche einen Neustart überlebt. Eine
        # ungültige Vorgabe darf das Backend nicht am Start hindern.
        if config is not None:
            base = config
        else:
            try:
                base = FreeCoolingConfig.from_env()
            except ValueError as exc:
                log.warning("freecooling: FREECOOLING_*-Umgebung ungültig (%s) – Standardwerte", exc)
                base = FreeCoolingConfig()
        saved = store.load_config()
        if saved:
            try:
                base = FreeCoolingConfig.from_dict(saved, base=base)
            except ValueError as exc:
                log.warning("freecooling: gespeicherte Konfiguration ungültig (%s) – Standard", exc)
        self.config = base
        self.outdoor = outdoor or OutdoorStore(ttl_s=self.config.outdoor_ttl_s)
        self.outdoor.ttl_s = self.config.outdoor_ttl_s
        now = self._now()
        self._dev: Dict[str, DeviceState] = {
            serial: DeviceState.from_json(serial, data, now)
            for serial, data in store.load_states().items()
        }
        self._prev_protected = False
        self._last_purge_day: Optional[str] = None
        self._last_push: Optional[datetime] = None
        self._last_push_key: Optional[Tuple] = None

    # ---- Konfiguration ------------------------------------------------------
    def update_config(self, updates: Dict[str, Any]) -> FreeCoolingConfig:
        new = FreeCoolingConfig.from_dict(updates, base=self.config)
        changed = {k: v for k, v in new.as_dict().items() if self.config.as_dict().get(k) != v}
        self.config = new
        self.outdoor.ttl_s = new.outdoor_ttl_s
        self.store.save_config(new.as_dict())
        if changed:
            self.store.log({"ts_utc": utc_iso(self._now()), "serial": "-", "event": "config",
                            "reason": ", ".join(f"{k}={v}" for k, v in sorted(changed.items()))})
        # Sofort anwenden (z.B. Wächter aus -> laufendes Kühlen beenden). Ein
        # Fehler dabei darf die gespeicherte Änderung nicht als gescheitert melden.
        try:
            self.tick()
        except Exception as exc:                     # noqa: BLE001
            log.exception("freecooling: Bewertung nach Konfigurationsänderung: %s", exc)
        return self.config

    # ---- Außenwert ----------------------------------------------------------
    def on_outdoor(self, kind: str, temperature: Any, humidity: Any = None,
                   source: str = "") -> Optional[OutdoorReading]:
        r = self.outdoor.update(kind, temperature, humidity, source, ts=self._now())
        if r is None:
            return None
        events = self.tick()
        if not events and self.on_change:            # tick() pusht nur bei Ereignissen
            # Nicht jede Sensormeldung (alle paar Sekunden) an alle Clients schicken.
            now = self._now()
            key = (r.kind, r.source, round(r.temperature, 1))
            if (key != self._last_push_key or self._last_push is None
                    or (now - self._last_push).total_seconds() >= 60):
                self._last_push, self._last_push_key = now, key
                try:
                    self.on_change(self.status(now))
                except Exception as exc:             # noqa: BLE001
                    log.debug("freecooling on_change: %s", exc)
        return r

    # ---- Hilfsfunktionen ----------------------------------------------------
    def _now(self) -> datetime:
        now = self.now_fn()
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now

    def _controlled(self, serial: str, state: Dict[str, Any]) -> bool:
        if self.config.devices:
            return serial in self.config.devices
        role = str(state.get("device_role") or state.get("role") or "").strip().lower()
        return role == "master"

    def _indoor_temp(self, state: Dict[str, Any]) -> Optional[float]:
        if self.config.use_avg_temperature:
            v = _num(state.get("temperature_avg"))
            if v is not None:
                return v
        return _num(state.get("temperature"))

    def _state_fresh(self, state: Dict[str, Any], now: datetime) -> bool:
        """Gerät online und zuletzt innerhalb state_ttl_s gemeldet."""
        if state.get("online") is False:
            return False
        seen = _num(state.get("lastSeen"))
        if seen is None:
            return True
        return (now.timestamp() - seen) <= self.config.state_ttl_s

    def _is_night(self, state: Dict[str, Any], now: datetime) -> bool:
        if state.get("night_alarm") is True:
            return True
        span = _parse_hours(self.config.night_hours)
        if span is None:
            return False
        local = now.astimezone(self.local_tz) if self.local_tz else now.astimezone()
        return _in_hours(span, local.hour * 60 + local.minute)

    def _state_for(self, serial: str) -> DeviceState:
        st = self._dev.get(serial)
        if st is None:
            st = DeviceState(serial=serial)
            self._dev[serial] = st
        return st

    def _persist(self, st: DeviceState) -> None:
        self.store.save_state(st.serial, st.to_json())

    def _send(self, st: DeviceState, mode: str, fan: Optional[str], now: datetime) -> bool:
        """Befehl an die Bridge; False, wenn er nachweislich nicht abgesetzt wurde."""
        payload: Dict[str, Any] = {"operating_mode": mode}
        if fan:
            payload["fan_speed"] = fan
        try:
            self.publish(st.serial, payload)
        except Exception as exc:                     # nie den Tick sterben lassen
            log.warning("freecooling: senden an %s fehlgeschlagen: %s", st.serial, exc)
            return False
        st.last_cmd_mode, st.last_cmd_fan, st.last_cmd_ts = mode, fan, now
        return True

    def _log(self, st: DeviceState, state: Dict[str, Any], event: str, reason: str,
             now: datetime, t_in: Optional[float], out: Optional[OutdoorReading],
             mode_from: Optional[str], fan_from: Optional[str],
             mode_to: Optional[str], fan_to: Optional[str], night: Optional[bool]) -> Dict[str, Any]:
        rec = {
            "ts_utc": utc_iso(now), "serial": st.serial,
            "device_name": state.get("name") or st.serial,
            "event": event, "reason": reason,
            "t_in": t_in,
            "t_out": out.temperature if out else None,
            "t_out_source": out.label() if out else None,
            "t_out_age_s": out.age_s(now) if out else None,
            "delta": round(t_in - out.temperature, 2) if (t_in is not None and out) else None,
            "mode_from": mode_from, "fan_from": fan_from,
            "mode_to": mode_to, "fan_to": fan_to,
            "night": None if night is None else int(bool(night)),
        }
        self.store.log(rec)
        st.last_event, st.last_reason, st.last_event_ts = event, reason, now
        log.info("freecooling %s %s: %s (innen %s, außen %s)", st.serial, event, reason,
                 t_in, out.temperature if out else None)
        return rec

    # ---- die Entscheidung ---------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or self._now()
        events: List[Dict[str, Any]] = []
        self._maybe_purge(now)
        cfg = self.config
        out = self.outdoor.current(now, cfg.outdoor_ttl_s)
        protected = bool(self.get_protection())
        devices = dict(self.get_devices())
        bridge = devices.get("bridge")
        bridge_online = not (isinstance(bridge, dict) and bridge.get("online") is False)
        if self._prev_protected and not protected:
            # Schutz aufgehoben: die Bridge stellt die Modi erst mit der nächsten
            # Abfrage wieder her — bis dahin ist ein abweichender Modus kein Eingriff.
            for st in self._dev.values():
                st.grace_until = now + timedelta(seconds=cfg.confirm_grace_s)
        self._prev_protected = protected
        for serial, state in devices.items():
            if not _is_unit(state):
                continue
            ev = self._evaluate(serial, state, now, out, protected, bridge_online)
            if ev:
                events.append(ev)
        # Gespeicherte Zustände von Geräten, die (noch) nicht gemeldet sind, bleiben;
        # sobald das Gerät wieder meldet, läuft die Bewertung weiter.
        if events and self.on_change:
            try:
                self.on_change(self.status(now))
            except Exception as exc:                 # noqa: BLE001
                log.debug("freecooling on_change: %s", exc)
        return events

    def _evaluate(self, serial: str, state: Dict[str, Any], now: datetime,
                  out: Optional[OutdoorReading], protected: bool,
                  bridge_online: bool) -> Optional[Dict[str, Any]]:
        cfg = self.config
        st = self._state_for(serial)
        controlled = cfg.enabled and self._controlled(serial, state)
        mode = state.get("operating_mode")
        fan = state.get("fan_speed")
        t_in = self._indoor_temp(state)
        fresh = bridge_online and self._state_fresh(state, now)
        in_grace = st.grace_until is not None and now < st.grace_until

        # 0) Keine frischen Gerätewerte oder Schutz aktiv: nichts entscheiden.
        if not fresh:
            if not st.stale:
                st.stale = True
                self._persist(st)
            return None
        if st.stale:
            # Gerät/Bridge wieder da: erst eine Abfrage abwarten, bevor ein
            # abweichender Modus als Eingriff zählt.
            st.stale = False
            st.grace_until = now + timedelta(seconds=cfg.confirm_grace_s)
            in_grace = True
            self._persist(st)
        if protected:
            return None

        # 1) Rückschaltung nach einem Ende bestätigen oder wiederholen — in jedem
        #    Zustand, auch wenn der Wächter inzwischen aus ist (Punkt 4 der Prüfung).
        if st.restore_mode is not None and mode is not None:
            if mode != cfg.cool_mode:
                st.restore_mode = st.restore_fan = st.restore_next = None
                st.restore_attempts = 0
                self._persist(st)
            elif st.restore_next is not None and now >= st.restore_next:
                st.restore_attempts += 1
                wait = cfg.confirm_grace_s if st.restore_attempts < RESTORE_FAST_ATTEMPTS else RESTORE_SLOW_S
                st.restore_next = now + timedelta(seconds=wait)
                self._send(st, st.restore_mode, st.restore_fan, now)
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="end",
                                reason=f"Rückkehr wiederholt ({st.restore_attempts}.) – Gerät meldet weiter {mode}",
                                mode_from=mode, fan_from=fan, mode_to=st.restore_mode,
                                fan_to=st.restore_fan, night=None)
                self._persist(st)
                return rec

        # 2) Wächter aus / Gerät nicht (mehr) gesteuert: laufendes Kühlen beenden,
        #    aber einen bereits sichtbaren Eingriff des Nutzers nicht übersteuern.
        if not controlled:
            if st.cooling:
                if st.confirmed and mode is not None and mode != cfg.cool_mode:
                    return self._release(st, state, now, t_in, out, mode, fan,
                                         "Wächter aus – Gerät steht bereits auf " + str(mode))
                return self._end(st, state, now, t_in, out, mode, fan,
                                 "disabled" if not cfg.enabled else "excluded", force=True)
            return None

        # 3) Zustand "kühlt".
        if st.cooling:
            if mode == cfg.cool_mode:
                if not st.confirmed or st.grace_until is not None:
                    # Das Gerät meldet den Kühlmodus: Start bestätigt. Die Schonfrist
                    # ist damit erledigt — ein abweichender Modus ab der nächsten
                    # Abfrage ist ein Eingriff des Nutzers und wartet nicht erst die
                    # restliche Schonfrist (bis zu confirm_grace_s) ab.
                    st.confirmed = True
                    st.grace_until = None
                    self._persist(st)
            elif in_grace:
                return None                              # Abfrage der Bridge abwarten
            elif not st.confirmed:
                # Der Startbefehl wurde nie bestätigt: nicht angekommen (oder vorher
                # umgestellt). Kein Eingriff, keine Pause — Haltezeit, dann neu.
                st.cooling = False
                st.since = now
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="unconfirmed",
                                reason=f"Startbefehl nicht bestätigt – Gerät meldet {mode}; "
                                       f"kommt er noch an, wird das Kühlen übernommen",
                                mode_from=cfg.cool_mode, fan_from=None, mode_to=mode, fan_to=fan,
                                night=None)
                self._persist(st)
                return rec
            else:
                return self._release(st, state, now, t_in, out, mode, fan,
                                     f"Gerät meldet {mode} statt {cfg.cool_mode} – Eingriff des Nutzers, "
                                     f"Pause {cfg.override_pause_s // 60} min", pause=True)
            if mode != cfg.cool_mode:
                return None
            # Endebedingungen (Ende sofort, wenn der Außenwert fehlt oder die
            # Außenluft wärmer ist als innen — das ist genau der beanstandete Fall).
            held = st.since is not None and (now - st.since).total_seconds() < cfg.min_hold_s
            if out is None:
                return self._end(st, state, now, t_in, None, mode, fan, "kein gültiger Außenwert", force=True)
            if t_in is None:
                return self._end(st, state, now, None, out, mode, fan, "kein Innenwert", force=True)
            delta = t_in - out.temperature
            if delta <= 0:
                return self._end(st, state, now, t_in, out, mode, fan,
                                 f"außen {out.temperature:.1f} °C nicht kühler als innen {t_in:.1f} °C",
                                 force=True)
            reason = None
            if delta < cfg.delta_off:
                reason = f"Abstand {delta:.1f} K < {cfg.delta_off:g} K"
            elif t_in <= cfg.t_in_min - 0.5:
                reason = f"innen {t_in:.1f} °C <= {cfg.t_in_min - 0.5:g} °C"
            elif out.temperature <= cfg.t_out_min - cfg.t_out_hyst:
                reason = f"außen {out.temperature:.1f} °C <= {cfg.t_out_min - cfg.t_out_hyst:g} °C"
            if reason and not held:
                return self._end(st, state, now, t_in, out, mode, fan, reason)
            # Tag/Nacht-Stufe nachführen (kein Moduswechsel, nur die Stufe), flankengesteuert.
            night = self._is_night(state, now)
            if st.night is None or night != st.night:
                st.night = night
                want = cfg.fan_night if night else cfg.fan_day
                if fan != want:
                    self._send(st, cfg.cool_mode, want, now)
                    rec = self._log(st, state, now=now, t_in=t_in, out=out, event="fan",
                                    reason="Nachtstufe" if night else "Tagstufe",
                                    mode_from=cfg.cool_mode, fan_from=fan, mode_to=cfg.cool_mode,
                                    fan_to=want, night=night)
                    self._persist(st)
                    return rec
                self._persist(st)
            return None

        # 4) Zustand "bereit".
        # 4a) Ein verspätet angekommener Startbefehl: Kühlen übernehmen statt
        #     das Gerät als "manuell" zu ignorieren.
        if (mode == cfg.cool_mode and st.last_cmd_mode == cfg.cool_mode and st.last_cmd_ts is not None
                and (now - st.last_cmd_ts).total_seconds() <= cfg.adopt_window_s
                and st.restore_mode is None):
            st.cooling, st.confirmed, st.since = True, True, now
            st.night = self._is_night(state, now)
            st.override_until = None
            if st.baseline_mode is None:
                st.baseline_mode = cfg.rest_mode
                st.baseline_fan = _sendable_fan(st.last_cmd_fan)
            rec = self._log(st, state, now=now, t_in=t_in, out=out, event="adopted",
                            reason="Startbefehl verspätet angekommen – Kühlen übernommen",
                            mode_from=mode, fan_from=fan, mode_to=cfg.cool_mode, fan_to=fan,
                            night=st.night)
            self._persist(st)
            return rec
        # 4b) SMART durch den Ruhemodus ersetzen — nicht während der Pause nach einem
        #     Eingriff, und mit abnehmender Häufigkeit, falls das Gerät nicht folgt.
        if cfg.block_smart and mode == "Smart":
            if st.override_until is not None and now < st.override_until:
                return None
            wait = cfg.confirm_grace_s if st.smart_block_count < SMART_BLOCK_FAST else SMART_BLOCK_SLOW_S
            if st.smart_blocked_at is None or (now - st.smart_blocked_at).total_seconds() >= wait:
                st.smart_blocked_at = now
                st.smart_block_count += 1
                st.since = now                                   # Haltezeit vor einem Start
                sent_fan = _sendable_fan(fan)
                self._send(st, cfg.rest_mode, sent_fan, now)
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="smart_blocked",
                                reason=f"SMART durch Wächter ersetzt -> {cfg.rest_mode}",
                                mode_from="Smart", fan_from=fan, mode_to=cfg.rest_mode,
                                fan_to=sent_fan, night=None)
                self._persist(st)
                return rec
            return None
        if st.smart_block_count and mode != "Smart":
            st.smart_block_count = 0
            self._persist(st)
        if mode not in START_MODES:                              # Aus, Außer Haus, manuell …
            return None
        if st.override_until is not None and now < st.override_until:
            return None
        if st.since is not None and (now - st.since).total_seconds() < cfg.min_hold_s:
            return None
        if st.restore_mode is not None:                          # Rückkehr noch nicht bestätigt
            return None
        if out is None or t_in is None:
            return None
        delta = t_in - out.temperature
        if not (t_in > cfg.t_in_min and out.temperature > cfg.t_out_min and delta >= cfg.delta_on):
            return None
        # Start: Zustand vorher merken, Befehl senden, Bestätigung abwarten.
        night = self._is_night(state, now)
        want = cfg.fan_night if night else cfg.fan_day
        baseline_mode = mode if (mode and mode != "Smart") else cfg.rest_mode
        baseline_fan = _sendable_fan(fan)
        if not self._send(st, cfg.cool_mode, want, now):
            st.since = now                                       # nicht jeden Tick erneut versuchen
            rec = self._log(st, state, now=now, t_in=t_in, out=out, event="send_failed",
                            reason="Startbefehl konnte nicht gesendet werden (MQTT getrennt?)",
                            mode_from=mode, fan_from=fan, mode_to=cfg.cool_mode, fan_to=want,
                            night=night)
            self._persist(st)
            return rec
        st.baseline_mode, st.baseline_fan = baseline_mode, baseline_fan
        st.cooling, st.confirmed, st.since, st.night = True, False, now, night
        st.grace_until = now + timedelta(seconds=cfg.confirm_grace_s)
        st.override_until = None
        rec = self._log(st, state, now=now, t_in=t_in, out=out, event="start",
                        reason=f"innen {t_in:.1f} °C, außen {out.temperature:.1f} °C, "
                               f"Abstand {delta:.1f} K >= {cfg.delta_on:g} K",
                        mode_from=mode, fan_from=fan, mode_to=cfg.cool_mode, fan_to=want,
                        night=night)
        self._persist(st)
        return rec

    def _release(self, st: DeviceState, state: Dict[str, Any], now: datetime,
                 t_in: Optional[float], out: Optional[OutdoorReading],
                 mode: Optional[str], fan: Optional[str], reason: str,
                 pause: bool = False) -> Dict[str, Any]:
        """Der Nutzer hat selbst umgestellt: Wächter zieht sich zurück, sendet nichts."""
        cfg = self.config
        st.cooling, st.confirmed, st.since, st.night = False, False, now, None
        st.baseline_mode = st.baseline_fan = None
        st.last_cmd_mode = None                              # kein späteres "adopted"
        if pause:
            st.override_until = now + timedelta(seconds=cfg.override_pause_s)
        rec = self._log(st, state, now=now, t_in=t_in, out=out, event="override", reason=reason,
                        mode_from=cfg.cool_mode, fan_from=None, mode_to=mode, fan_to=fan, night=None)
        self._persist(st)
        return rec

    def _end(self, st: DeviceState, state: Dict[str, Any], now: datetime,
             t_in: Optional[float], out: Optional[OutdoorReading],
             mode: Optional[str], fan: Optional[str], reason: str,
             force: bool = False) -> Dict[str, Any]:
        cfg = self.config
        back_mode = st.baseline_mode or cfg.rest_mode
        if back_mode not in START_MODES:
            back_mode = cfg.rest_mode
        back_fan = _sendable_fan(st.baseline_fan, default="Medium")
        sent = self._send(st, back_mode, back_fan, now)
        st.cooling, st.confirmed, st.since, st.night = False, False, now, None
        st.baseline_mode = st.baseline_fan = None
        st.last_cmd_mode = None if not sent else st.last_cmd_mode
        # Rückkehr gilt erst als erledigt, wenn das Gerät sie meldet; sonst wiederholen.
        st.restore_mode, st.restore_fan, st.restore_attempts = back_mode, back_fan, 0
        st.restore_next = now + timedelta(seconds=cfg.confirm_grace_s if sent else MIN_GRACE_S)
        rec = self._log(st, state, now=now, t_in=t_in, out=out, event="end",
                        reason=reason + (" (sofort)" if force else "")
                        + ("" if sent else " – Befehl nicht gesendet, wird wiederholt"),
                        mode_from=mode, fan_from=fan, mode_to=back_mode, fan_to=back_fan,
                        night=None)
        self._persist(st)
        return rec

    def _maybe_purge(self, now: datetime) -> None:
        day = utc_iso(now)[:10]
        if day != self._last_purge_day:
            self._last_purge_day = day
            try:
                n = self.store.purge_log()
                if n:
                    log.info("freecooling: %d alte Protokollzeilen entfernt", n)
            except Exception as exc:                 # noqa: BLE001
                log.warning("freecooling: purge fehlgeschlagen: %s", exc)

    # ---- Status für API / WebSocket -----------------------------------------
    def status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or self._now()
        cfg = self.config
        out = self.outdoor.current(now, cfg.outdoor_ttl_s)
        protected = bool(self.get_protection())
        devices = dict(self.get_devices())
        bridge = devices.get("bridge")
        bridge_online = not (isinstance(bridge, dict) and bridge.get("online") is False)
        devs: Dict[str, Any] = {}
        for serial, state in devices.items():
            if not _is_unit(state):
                continue
            st = self._dev.get(serial) or DeviceState(serial=serial)
            controlled = self._controlled(serial, state)
            t_in = self._indoor_temp(state)
            fresh = bridge_online and self._state_fresh(state, now)
            mode = state.get("operating_mode")
            delta = (t_in - out.temperature) if (t_in is not None and out) else None
            hold_left = 0
            if st.since is not None:
                hold_left = max(0, int(cfg.min_hold_s - (now - st.since).total_seconds()))
            pause_left = 0
            if st.override_until is not None:
                pause_left = max(0, int((st.override_until - now).total_seconds()))
            restore_pending = st.restore_mode is not None and mode == cfg.cool_mode
            if not cfg.enabled:
                phase = "disabled"
            elif not controlled:
                phase = "not_controlled"
            elif not fresh:
                phase = "stale"
            elif protected:
                phase = "protected"
            elif restore_pending and st.restore_attempts >= RESTORE_FAILED_AFTER:
                phase = "restore_failed"
            elif st.cooling:
                phase = "cooling" if (st.confirmed or mode == cfg.cool_mode) else "starting"
            elif pause_left > 0:
                phase = "override"
            elif mode == "Smart" and cfg.block_smart:
                phase = "smart_blocked"
            elif restore_pending:
                phase = "restoring"
            elif mode not in START_MODES:
                phase = "manual_mode"
            elif out is None:
                phase = "no_outdoor"
            elif hold_left > 0:
                phase = "hold"
            else:
                phase = "ready"
            cond_start = (t_in is not None and out is not None and t_in > cfg.t_in_min
                          and out.temperature > cfg.t_out_min and delta is not None
                          and delta >= cfg.delta_on)
            devs[serial] = {
                "serial": serial,
                "name": state.get("name") or serial,
                "role": state.get("device_role") or state.get("role"),
                "online": fresh,
                "controlled": controlled,
                "phase": phase,
                "cooling": st.cooling,
                "confirmed": st.confirmed,
                "mode": mode,
                "fan": state.get("fan_speed"),
                "t_in": t_in,
                "t_out": out.temperature if out else None,
                "delta": round(delta, 2) if delta is not None else None,
                "start_condition": bool(cond_start),
                "night": self._is_night(state, now),
                "since": utc_iso(st.since) if st.since else None,
                "hold_left_s": hold_left,
                "pause_left_s": pause_left,
                "baseline_mode": st.baseline_mode,
                "baseline_fan": st.baseline_fan,
                "restore_mode": st.restore_mode,
                "restore_attempts": st.restore_attempts,
                "last_event": st.last_event,
                "last_reason": st.last_reason,
                "last_event_ts": utc_iso(st.last_event_ts) if st.last_event_ts else None,
            }
        return {
            "config": cfg.as_dict(),
            "outdoor": self.outdoor.as_dict(now, cfg.outdoor_ttl_s),
            "protected": protected,
            "bridge_online": bridge_online,
            "devices": devs,
            "log_count": self.store.log_count(),
            "ts": utc_iso(now),
        }
