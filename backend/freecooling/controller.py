"""
Regellogik des Free-Cooling-Wächters.
=====================================

Reine Python-Logik ohne asyncio und ohne MQTT-Abhängigkeit: Der Controller
bekommt die Geräteliste, eine Sendefunktion, den Speicher und die Außenwerte
von außen und wird per ``tick(now)`` aufgerufen. Dadurch ist jede Entscheidung
mit festgesetzter Zeit testbar.

Regel je gesteuertem Gerät (Standardwerte, alle konfigurierbar):

  Start   T_innen > 24 °C  UND  T_außen > 20 °C  UND  (T_innen − T_außen) >= 3,0 K
  Ende    (T_innen − T_außen) < 1,0 K  ODER  T_innen <= 23,5 °C  ODER  T_außen <= 20 °C
  Sofort  kein gültiger Außenwert (älter als 60 min), Wächter aus, Gerät nicht
          mehr in der Geräteliste
  Halten  nach jedem Umschalten mindestens 15 min im neuen Zustand

Zum Kühlen wird der Luftstrom Master-Slave gesetzt (tagsüber Stufe 2, nachts
Stufe 1); danach wird der Zustand vor dem Kühlen exakt wiederhergestellt. War
das SMART, wird stattdessen AUTO gesetzt, weil der Wächter genau die
Free-Cooling-Entscheidung des SMART-Modus ersetzt. Solange der Wächter aktiv
ist, hält er die gesteuerten Geräte aus SMART heraus (abschaltbar).

Ein Eingriff des Nutzers (Modus am Gerät oder in der App geändert, während der
Wächter kühlt) wird erkannt: Der Wächter zieht sich zurück, stellt nichts um
und startet für eine Pause (Standard 60 min) nicht neu. Läuft eine
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
COOL_MODES = ("MasterSlaveFlow", "SlaveMasterFlow", "Intake", "Expulsion")
# Nur aus diesen Ruhezuständen heraus startet der Wächter. Aus, Außer Haus,
# Überwachung und manuell gewählte Luftstrom-Modi sind Entscheidungen des
# Nutzers und bleiben unangetastet; SMART wird (bei block_smart) erst ersetzt.
START_MODES = ("Auto", "ManualHeatRecovery", "Night")
FAN_SPEEDS = ("Low", "Medium", "High")
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
    delta_on: float = 3.0
    delta_off: float = 1.0
    min_hold_s: int = 900
    outdoor_ttl_s: int = 3600
    cool_mode: str = "MasterSlaveFlow"
    fan_day: str = "Medium"
    fan_night: str = "Low"
    night_hours: str = ""            # "22:00-06:00" oder leer (dann nur Dämmerungssensor)
    rest_mode: str = "Auto"          # ersetzt SMART beim Zurückschalten
    block_smart: bool = True
    override_pause_s: int = 3600
    confirm_grace_s: int = 180       # Zeit, bis die Bridge den gesendeten Modus meldet
    use_avg_temperature: bool = True

    @classmethod
    def from_env(cls) -> "FreeCoolingConfig":
        c = cls()
        c.enabled = _envb("FREECOOLING_ENABLED", c.enabled)
        devs = os.getenv("FREECOOLING_DEVICES", "")
        c.devices = [d.strip() for d in devs.split(",") if d.strip()]
        c.t_in_min = _envf("FREECOOLING_T_IN_MIN", c.t_in_min)
        c.t_out_min = _envf("FREECOOLING_T_OUT_MIN", c.t_out_min)
        c.delta_on = _envf("FREECOOLING_DELTA_ON", c.delta_on)
        c.delta_off = _envf("FREECOOLING_DELTA_OFF", c.delta_off)
        c.min_hold_s = _envi("FREECOOLING_MIN_HOLD_S", c.min_hold_s)
        c.outdoor_ttl_s = _envi("FREECOOLING_OUTDOOR_TTL_S", c.outdoor_ttl_s)
        c.cool_mode = os.getenv("FREECOOLING_COOL_MODE", c.cool_mode)
        c.fan_day = os.getenv("FREECOOLING_FAN_DAY", c.fan_day)
        c.fan_night = os.getenv("FREECOOLING_FAN_NIGHT", c.fan_night)
        c.night_hours = os.getenv("FREECOOLING_NIGHT_HOURS", c.night_hours)
        c.rest_mode = os.getenv("FREECOOLING_REST_MODE", c.rest_mode)
        c.block_smart = _envb("FREECOOLING_BLOCK_SMART", c.block_smart)
        c.override_pause_s = _envi("FREECOOLING_OVERRIDE_PAUSE_S", c.override_pause_s)
        c.confirm_grace_s = _envi("FREECOOLING_CONFIRM_GRACE_S", c.confirm_grace_s)
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
        for name in ("t_in_min", "t_out_min", "delta_on", "delta_off"):
            v = _num(getattr(self, name))
            if v is None:
                errors.append(f"{name}: Zahl erwartet")
            else:
                setattr(self, name, float(v))
        for name in ("min_hold_s", "outdoor_ttl_s", "override_pause_s", "confirm_grace_s"):
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
            if self.outdoor_ttl_s < 60:
                errors.append("outdoor_ttl_s: mindestens 60 s")
        self.cool_mode = str(self.cool_mode).strip()
        if self.cool_mode not in COOL_MODES:
            errors.append(f"cool_mode: einer von {', '.join(COOL_MODES)}")
        self.rest_mode = str(self.rest_mode).strip()
        if self.rest_mode not in OPERATING_MODES or self.rest_mode in ("Smart", "Off") \
                or self.rest_mode in COOL_MODES:
            errors.append("rest_mode: ein Wärmerückgewinnungs-Modus wie Auto oder ManualHeatRecovery")
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
    since: Optional[datetime] = None          # letztes Umschalten durch den Wächter
    baseline_mode: Optional[str] = None       # Zustand vor dem Kühlen
    baseline_fan: Optional[str] = None
    night: Optional[bool] = None              # Nachtstufe aktiv (während des Kühlens)
    override_until: Optional[datetime] = None
    smart_blocked_at: Optional[datetime] = None
    restore_mode: Optional[str] = None        # nach dem Ende: erwarteter Modus, bis bestätigt
    restore_fan: Optional[str] = None
    restore_retried: bool = False
    last_event: Optional[str] = None
    last_reason: Optional[str] = None
    last_event_ts: Optional[datetime] = None

    _DT = ("since", "override_until", "smart_blocked_at", "last_event_ts")

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in self._DT:
            v = getattr(self, k)
            d[k] = utc_iso(v) if v else None
        return d

    @classmethod
    def from_json(cls, serial: str, data: Dict[str, Any]) -> "DeviceState":
        s = cls(serial=serial)
        for f in fields(cls):
            if f.name == "serial" or f.name not in data:
                continue
            v = data[f.name]
            if f.name in cls._DT:
                v = _parse_iso(v)
            setattr(s, f.name, v)
        s.cooling = bool(s.cooling)
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
    publish(serial, {"operating_mode": .., "fan_speed": ..}) -> sendet an die Bridge
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
        # damit eine Änderung aus der Oberfläche einen Neustart überlebt.
        saved = store.load_config()
        base = config or FreeCoolingConfig.from_env()
        if saved:
            try:
                base = FreeCoolingConfig.from_dict(saved, base=base)
            except ValueError as exc:
                log.warning("freecooling: gespeicherte Konfiguration ungültig (%s) – Standard", exc)
        self.config = base
        self.outdoor = outdoor or OutdoorStore(ttl_s=self.config.outdoor_ttl_s)
        self.outdoor.ttl_s = self.config.outdoor_ttl_s
        self._dev: Dict[str, DeviceState] = {
            serial: DeviceState.from_json(serial, data)
            for serial, data in store.load_states().items()
        }
        self._last_purge_day: Optional[str] = None

    # ---- Konfiguration ------------------------------------------------------
    def update_config(self, updates: Dict[str, Any]) -> FreeCoolingConfig:
        new = FreeCoolingConfig.from_dict(updates, base=self.config)
        changed = {k: v for k, v in new.as_dict().items() if self.config.as_dict().get(k) != v}
        self.config = new
        self.outdoor.ttl_s = new.outdoor_ttl_s
        self.store.save_config(new.as_dict())
        if changed:
            self.store.log({"ts_utc": utc_iso(self.now_fn()), "serial": "-", "event": "config",
                            "reason": ", ".join(f"{k}={v}" for k, v in sorted(changed.items()))})
        # Sofort anwenden (z.B. Wächter aus -> laufendes Kühlen beenden).
        self.tick()
        return self.config

    # ---- Außenwert ----------------------------------------------------------
    def on_outdoor(self, kind: str, temperature: Any, humidity: Any = None,
                   source: str = "") -> Optional[OutdoorReading]:
        r = self.outdoor.update(kind, temperature, humidity, source, ts=self._now())
        if r is not None:
            events = self.tick()
            if not events and self.on_change:        # tick() pusht nur bei Ereignissen
                try:
                    self.on_change(self.status())
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

    def _send(self, serial: str, mode: str, fan: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"operating_mode": mode}
        if fan:
            payload["fan_speed"] = fan
        try:
            self.publish(serial, payload)
        except Exception as exc:                     # nie den Tick sterben lassen
            log.warning("freecooling: senden an %s fehlgeschlagen: %s", serial, exc)
        return payload

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
        out = self.outdoor.current(now, self.config.outdoor_ttl_s)
        protected = bool(self.get_protection())
        devices = dict(self.get_devices())
        for serial, state in devices.items():
            if not _is_unit(state):
                continue
            ev = self._evaluate(serial, state, now, out, protected)
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
                  out: Optional[OutdoorReading], protected: bool) -> Optional[Dict[str, Any]]:
        cfg = self.config
        st = self._state_for(serial)
        controlled = cfg.enabled and self._controlled(serial, state)
        mode = state.get("operating_mode")
        fan = state.get("fan_speed")
        t_in = self._indoor_temp(state)
        online = state.get("online") is not False

        # 1) Wächter aus / Gerät nicht (mehr) gesteuert: laufendes Kühlen beenden.
        if not controlled:
            if st.cooling and not protected and online:
                return self._end(st, state, now, t_in, out, mode, fan,
                                 "disabled" if not cfg.enabled else "excluded", force=True)
            return None

        # 2) NeuraCell-X greift ein oder Gerät offline: nichts senden.
        if protected or not online:
            return None

        # 3) Eingriff des Nutzers erkennen: Wächter kühlt, Gerät meldet anderen Modus.
        if st.cooling and mode is not None and mode != cfg.cool_mode:
            if st.since is None or (now - st.since).total_seconds() > cfg.confirm_grace_s:
                st.cooling = False
                st.since = now
                st.override_until = now + timedelta(seconds=cfg.override_pause_s)
                st.baseline_mode = st.baseline_fan = None
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="override",
                                reason=f"Gerät meldet {mode} statt {cfg.cool_mode} – Eingriff des Nutzers "
                                       f"oder Befehl nicht angekommen, Pause {cfg.override_pause_s // 60} min",
                                mode_from=cfg.cool_mode, fan_from=None, mode_to=mode, fan_to=fan,
                                night=None)
                self._persist(st)
                return rec
            return None

        # 4) Zustand "kühlt": Endebedingungen prüfen, Nachtstufe nachführen.
        if st.cooling:
            held = st.since is not None and (now - st.since).total_seconds() < cfg.min_hold_s
            if out is None:
                return self._end(st, state, now, t_in, None, mode, fan,
                                 "kein gültiger Außenwert", force=True)
            if t_in is None:
                return self._end(st, state, now, None, out, mode, fan,
                                 "kein Innenwert", force=True)
            delta = t_in - out.temperature
            reason = None
            if delta < cfg.delta_off:
                reason = f"Abstand {delta:.1f} K < {cfg.delta_off:g} K"
            elif t_in <= cfg.t_in_min - 0.5:
                reason = f"innen {t_in:.1f} °C <= {cfg.t_in_min - 0.5:g} °C"
            elif out.temperature <= cfg.t_out_min:
                reason = f"außen {out.temperature:.1f} °C <= {cfg.t_out_min:g} °C"
            if reason and not held:
                return self._end(st, state, now, t_in, out, mode, fan, reason)
            # Tag/Nacht-Stufe nachführen (kein Moduswechsel, nur die Stufe).
            night = self._is_night(state, now)
            want = cfg.fan_night if night else cfg.fan_day
            if st.night is not None and night != st.night and fan != want:
                self._send(serial, cfg.cool_mode, want)
                st.night = night
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="fan",
                                reason="Nachtstufe" if night else "Tagstufe",
                                mode_from=cfg.cool_mode, fan_from=fan, mode_to=cfg.cool_mode,
                                fan_to=want, night=night)
                self._persist(st)
                return rec
            return None

        # 5) Zustand "bereit": Rückkehr bestätigen, SMART blockieren, Pausen beachten,
        #    Startbedingung prüfen.
        if st.restore_mode is not None and mode is not None:
            if mode != cfg.cool_mode:
                st.restore_mode = st.restore_fan = None          # Rückkehr bestätigt
                st.restore_retried = False
                self._persist(st)
            elif (not st.restore_retried and st.since is not None
                  and (now - st.since).total_seconds() > cfg.confirm_grace_s):
                # Gerät steht nach dem Ende weiter im Kühlmodus: Rückkehr einmal wiederholen.
                st.restore_retried = True
                self._send(serial, st.restore_mode, st.restore_fan)
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="end",
                                reason=f"Rückkehr wiederholt – Gerät meldet weiter {mode}",
                                mode_from=mode, fan_from=fan, mode_to=st.restore_mode,
                                fan_to=st.restore_fan, night=None)
                self._persist(st)
                return rec
        if cfg.block_smart and mode == "Smart":
            if (st.smart_blocked_at is None
                    or (now - st.smart_blocked_at).total_seconds() > cfg.confirm_grace_s):
                st.smart_blocked_at = now
                st.since = now                                   # Haltezeit vor einem Start
                sent_fan = _sendable_fan(fan)
                self._send(serial, cfg.rest_mode, sent_fan)
                rec = self._log(st, state, now=now, t_in=t_in, out=out, event="smart_blocked",
                                reason=f"SMART durch Wächter ersetzt -> {cfg.rest_mode}",
                                mode_from="Smart", fan_from=fan, mode_to=cfg.rest_mode,
                                fan_to=sent_fan, night=None)
                self._persist(st)
                return rec
            return None
        if mode not in START_MODES:                              # Aus, Außer Haus, manuell …
            return None
        if st.override_until is not None and now < st.override_until:
            return None
        if st.since is not None and (now - st.since).total_seconds() < cfg.min_hold_s:
            return None
        if out is None or t_in is None:
            return None
        delta = t_in - out.temperature
        if not (t_in > cfg.t_in_min and out.temperature > cfg.t_out_min and delta >= cfg.delta_on):
            return None
        # Start: Zustand vorher merken (SMART wird nie zurückgesetzt, dafür rest_mode).
        night = self._is_night(state, now)
        want = cfg.fan_night if night else cfg.fan_day
        st.baseline_mode = mode if (mode and mode != "Smart") else cfg.rest_mode
        st.baseline_fan = _sendable_fan(fan)
        self._send(serial, cfg.cool_mode, want)
        st.cooling, st.since, st.night, st.override_until = True, now, night, None
        st.restore_mode = st.restore_fan = None
        st.restore_retried = False
        rec = self._log(st, state, now=now, t_in=t_in, out=out, event="start",
                        reason=f"innen {t_in:.1f} °C, außen {out.temperature:.1f} °C, "
                               f"Abstand {delta:.1f} K >= {cfg.delta_on:g} K",
                        mode_from=mode, fan_from=fan, mode_to=cfg.cool_mode, fan_to=want,
                        night=night)
        self._persist(st)
        return rec

    def _end(self, st: DeviceState, state: Dict[str, Any], now: datetime,
             t_in: Optional[float], out: Optional[OutdoorReading],
             mode: Optional[str], fan: Optional[str], reason: str,
             force: bool = False) -> Dict[str, Any]:
        cfg = self.config
        back_mode = st.baseline_mode or cfg.rest_mode
        if back_mode == "Smart" or back_mode in COOL_MODES:
            back_mode = cfg.rest_mode
        back_fan = _sendable_fan(st.baseline_fan, default="Medium")
        self._send(st.serial, back_mode, back_fan)
        st.cooling, st.since, st.night = False, now, None
        st.baseline_mode = st.baseline_fan = None
        st.restore_mode, st.restore_fan, st.restore_retried = back_mode, back_fan, False
        rec = self._log(st, state, now=now, t_in=t_in, out=out, event="end",
                        reason=reason + (" (sofort)" if force else ""),
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
        devs: Dict[str, Any] = {}
        for serial, state in dict(self.get_devices()).items():
            if not _is_unit(state):
                continue
            st = self._dev.get(serial) or DeviceState(serial=serial)
            controlled = self._controlled(serial, state)
            t_in = self._indoor_temp(state)
            online = state.get("online") is not False
            delta = (t_in - out.temperature) if (t_in is not None and out) else None
            hold_left = 0
            if st.since is not None:
                hold_left = max(0, int(cfg.min_hold_s - (now - st.since).total_seconds()))
            pause_left = 0
            if st.override_until is not None:
                pause_left = max(0, int((st.override_until - now).total_seconds()))
            if not cfg.enabled:
                phase = "disabled"
            elif not controlled:
                phase = "not_controlled"
            elif not online:
                phase = "offline"
            elif protected:
                phase = "protected"
            elif st.cooling:
                phase = "cooling"
            elif pause_left > 0:
                phase = "override"
            elif state.get("operating_mode") == "Smart" and cfg.block_smart:
                phase = "smart_blocked"
            elif state.get("operating_mode") not in START_MODES:
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
                "online": online,
                "controlled": controlled,
                "phase": phase,
                "cooling": st.cooling,
                "mode": state.get("operating_mode"),
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
                "last_event": st.last_event,
                "last_reason": st.last_reason,
                "last_event_ts": utc_iso(st.last_event_ts) if st.last_event_ts else None,
            }
        return {
            "config": cfg.as_dict(),
            "outdoor": self.outdoor.as_dict(now, cfg.outdoor_ttl_s),
            "protected": protected,
            "devices": devs,
            "ts": utc_iso(now),
        }
