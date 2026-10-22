#!/usr/bin/env python3
"""Tests des Free-Cooling-Wächters (backend/freecooling).

Prüft die Regel mit festgesetzter Zeit gegen die Vorgabe:
  Start  Ti > 24 °C, Ta > 20 °C, (Ti − Ta) >= 3 K
  Ende   (Ti − Ta) < 1 K, Ti <= 23,5 °C, Ta <= 19,7 °C; sofort bei fehlendem Außenwert
         oder wenn die Außenluft nicht kühler ist als innen
  Halten 15 min nach jedem Umschalten
sowie Bestätigung des Starts, Schonfristen (Bridge-Abfrage, NeuraCell-X-Freigabe,
Bridge-Unterbrechung), Übernahme verspäteter Befehle, wiederholte Rückschaltung,
SMART-Sperre, Nutzereingriff, Frische der Gerätewerte, Außenwert-Quellen,
Persistenz und die REST-Schnittstelle.

Run:  python3 -m pytest test_freecooling.py -v
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent / "backend"))

from freecooling.controller import (  # noqa: E402
    FreeCoolingConfig, FreeCoolingController, _parse_hours, _in_hours,
    RESTORE_FAST_ATTEMPTS, RESTORE_SLOW_S, SMART_BLOCK_FAST, SMART_BLOCK_SLOW_S,
)
from freecooling.outdoor import OutdoorStore, parse_payload, resolve_location, fetch_openweathermap  # noqa: E402
from freecooling.routes import clean_source  # noqa: E402
from freecooling.store import FreeCoolingStore, log_to_csv  # noqa: E402

T0 = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
MASTER = "AABBCCDDEE01"
SLAVE = "AABBCCDDEE02"
COOL = "SlaveMasterFlow"          # Master saugt ab, Sensor misst weiter den Raum


def dev(serial=MASTER, mode="Auto", fan="Low", t=27.0, t_avg=None, role="Master",
        online=True, night=False, name=None, last_seen=None):
    d = {
        "serial": serial, "name": name or f"Gerät {serial[-2:]}", "operating_mode": mode,
        "fan_speed": fan, "temperature": t, "temperature_avg": t_avg, "humidity": 50,
        "device_role": role, "online": online, "night_alarm": night,
    }
    if last_seen is not None:
        d["lastSeen"] = last_seen
    return d


class Rig:
    """Controller mit Fake-Uhr, Fake-Sender und Speicher im Arbeitsspeicher."""

    def __init__(self, store=None, protection=False, send_ok=True, **cfg):
        self.now = T0
        self.devices = {}
        self.sent = []            # (serial, payload)
        self.pushed = []          # Status-Pushes (on_change)
        self.protection = protection
        self.send_ok = send_ok
        self.store = store or FreeCoolingStore(":memory:")
        cfg.setdefault("enabled", True)
        base = FreeCoolingConfig(**cfg)
        self.c = FreeCoolingController(
            get_devices=lambda: self.devices,
            publish=self._publish,
            store=self.store, config=base,
            get_protection=lambda: self.protection,
            on_change=lambda st: self.pushed.append(st),
            now_fn=lambda: self.now,
            local_tz=dt.timezone.utc,
        )

    def _publish(self, serial, payload):
        if not self.send_ok:
            raise RuntimeError("MQTT_ERR_NO_CONN")
        self.sent.append((serial, payload))

    def advance(self, seconds):
        self.now = self.now + dt.timedelta(seconds=seconds)

    def outdoor(self, t, kind="sensor", h=None, source="test"):
        return self.c.on_outdoor(kind, t, h, source=source)

    def tick(self):
        return self.c.tick(self.now)

    def cmds(self, serial=MASTER):
        return [p for s, p in self.sent if s == serial]

    def events(self, serial=None):
        rows = self.store.query_log(serial=serial, newest_first=False, limit=None)
        return [r["event"] for r in rows if r["event"] != "config"]

    def phase(self, serial=MASTER):
        return self.c.status(self.now)["devices"][serial]["phase"]

    def confirm(self, serial=MASTER, fan="Medium"):
        """Die Bridge meldet den gesendeten Kühlmodus (nächste Abfrage)."""
        self.devices[serial]["operating_mode"] = COOL
        self.devices[serial]["fan_speed"] = fan


def cooling_rig(**cfg):
    """Wächter kühlt bestätigt, Haltezeit läuft."""
    r = Rig(**cfg)
    r.devices[MASTER] = dev(t=27, mode="Auto", fan="Low")
    r.outdoor(23)
    assert r.events() == ["start"]
    r.confirm()
    r.advance(30)
    r.tick()
    assert r.c.status(r.now)["devices"][MASTER]["confirmed"] is True
    return r


# --------------------------------------------------------------- Startregel
@pytest.mark.parametrize("t_in,t_out", [(30, 32), (32, 37), (26, 24), (24.0, 20.5), (27, 20.0), (25, 22.5)])
def test_no_start_cases(t_in, t_out):
    """30/32 und 32/37 (Außen wärmer), 26/24 (Abstand 2 K), Ti nicht > 24, Ta nicht > 20."""
    r = Rig()
    r.devices[MASTER] = dev(t=t_in)
    r.outdoor(t_out)
    r.tick()
    assert r.cmds() == [] and r.events() == []


def test_start_27_23_logs_everything():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Auto", fan="Low")
    r.outdoor(23)                              # on_outdoor() bewertet sofort
    assert r.cmds() == [{"operating_mode": COOL, "fan_speed": "Medium"}]
    assert r.events() == ["start"]
    assert r.tick() == []                      # nichts Neues im nächsten Takt
    row = r.store.query_log()[0]
    assert row["t_in"] == 27 and row["t_out"] == 23 and row["delta"] == 4.0
    assert row["mode_from"] == "Auto" and row["fan_from"] == "Low"
    assert row["mode_to"] == COOL and row["fan_to"] == "Medium"
    assert row["t_out_source"] == "sensor:test" and row["t_out_age_s"] == 0
    assert row["night"] == 0
    assert r.phase() == "starting"             # noch unbestätigt
    r.confirm()
    assert r.phase() == "cooling"
    assert r.pushed and r.pushed[-1]["devices"][MASTER]["cooling"] is True


def test_start_just_above_thresholds_and_exactly_3k():
    r = Rig()
    r.devices[MASTER] = dev(t=24.1)
    r.outdoor(20.1)                            # 24.1 > 24, 20.1 > 20, Abstand 4 K
    assert r.events() == ["start"]
    r2 = Rig()
    r2.devices[MASTER] = dev(t=26.0)
    r2.outdoor(23.0)                           # genau 3,0 K
    assert r2.events() == ["start"]


# --------------------------------------------------------------- Enderegel
def test_end_when_outdoor_rises_to_26_5_after_hold():
    r = cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)                            # Abstand 0,5 K < 1 K
    assert r.events() == ["start", "end"]
    assert r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}   # exakte Rückkehr
    assert "0.5 K < 1 K" in r.store.query_log()[0]["reason"]
    assert r.c.status(r.now)["devices"][MASTER]["cooling"] is False
    assert r.phase() == "restoring"            # bis das Gerät die Rückkehr meldet
    r.devices[MASTER]["operating_mode"] = "Auto"
    assert r.phase() == "hold"


def test_hold_time_blocks_end_but_not_forever():
    r = cooling_rig()
    r.advance(5 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start"]             # innerhalb der Haltezeit: kein Ende
    r.advance(11 * 60)                         # 16 min nach dem Start
    r.tick()
    assert r.events() == ["start", "end"]


def test_outdoor_warmer_than_indoor_ends_immediately_despite_hold():
    """Genau der beanstandete Fall: Außen wärmer als innen -> sofort Wärmerückgewinnung."""
    r = cooling_rig()
    r.advance(60)
    r.outdoor(27.5)                            # Sensor-Sprung / Inversion
    assert r.events() == ["start", "end"]
    assert "nicht kühler" in r.store.query_log()[0]["reason"]


def test_hold_time_blocks_restart():
    r = cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start", "end"]
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(60)
    r.outdoor(22)                              # Startbedingung wieder erfüllt
    r.tick()
    assert r.events() == ["start", "end"]      # Haltezeit nach dem Ende
    assert r.phase() == "hold"
    r.advance(15 * 60)
    r.tick()
    assert r.events() == ["start", "end", "start"]


def test_end_when_indoor_cools_to_23_5():
    r = cooling_rig()
    r.advance(16 * 60)
    r.devices[MASTER]["temperature"] = 23.5
    r.tick()
    assert r.events() == ["start", "end"]


def test_end_when_outdoor_falls_below_20_with_hysteresis():
    r = cooling_rig()
    r.advance(16 * 60)
    r.outdoor(19.8)                            # 20,0 − 0,3 = 19,7: noch kein Ende
    assert r.events() == ["start"]
    r.outdoor(19.7)
    assert r.events() == ["start", "end"]
    r2 = cooling_rig(t_out_hyst=0.0)           # ohne Totband genau wie die Vorgabe
    r2.advance(16 * 60)
    r2.outdoor(20.0)
    assert r2.events() == ["start", "end"]


def test_stale_outdoor_never_starts_and_ends_immediately():
    r = Rig()
    r.devices[MASTER] = dev(t=22)              # noch kein Start
    r.outdoor(23)
    r.advance(61 * 60)                         # Außenwert 61 min alt
    r.devices[MASTER]["temperature"] = 27
    r.tick()
    assert r.events() == []
    assert r.phase() == "no_outdoor"
    r2 = cooling_rig()
    r2.advance(61 * 60)
    r2.tick()
    assert r2.events() == ["start", "end"]
    assert "sofort" in r2.store.query_log()[0]["reason"]
    assert r2.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}
    r3 = cooling_rig()
    r3.c.outdoor._readings.clear()             # Außenwert fällt komplett weg
    r3.advance(60)
    r3.tick()
    assert r3.events() == ["start", "end"]     # auch innerhalb der Haltezeit


def test_disabled_never_starts_and_disable_ends_cooling():
    r = Rig(enabled=False)
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    r.tick()
    assert r.cmds() == [] and r.events() == []
    assert r.phase() == "disabled"
    r2 = cooling_rig()
    r2.c.update_config({"enabled": False})     # wendet sofort an
    assert r2.events() == ["start", "end"]
    assert r2.cmds()[-1]["operating_mode"] == "Auto"


def test_disable_does_not_override_a_user_who_already_changed_the_mode():
    r = cooling_rig()
    r.advance(200)
    r.devices[MASTER]["operating_mode"] = "Night"     # Nutzer hat umgestellt
    r.c.update_config({"enabled": False})
    assert r.events() == ["start", "override"]
    assert len(r.cmds()) == 1                  # kein Rückkehrbefehl gegen den Nutzer


def test_night_baseline_restores_night_and_fan_night_becomes_low():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Night", fan="Night")
    r.outdoor(23)
    assert r.cmds() == [{"operating_mode": COOL, "fan_speed": "Medium"}]
    r.confirm()
    r.advance(16 * 60)
    r.outdoor(26.8)
    assert r.cmds()[-1] == {"operating_mode": "Night", "fan_speed": "Low"}   # Meldewert Night -> Low


@pytest.mark.parametrize("mode", ["Off", "AwayHome", "Surveillance", "MasterSlaveFlow",
                                  "SlaveMasterFlow", "Intake", "Expulsion", "TimedExpulsion", None])
def test_no_start_from_user_chosen_modes(mode):
    """Aus, Außer Haus, Überwachung und manuelle Luftstrom-Modi bleiben unangetastet."""
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode=mode)
    r.outdoor(23)
    assert r.cmds() == [] and r.events() == []
    assert r.phase() == "manual_mode"


# --------------------------------------------------------------- Bestätigung / verspätete Befehle
def test_bridge_lag_within_grace_is_not_an_override():
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    r.advance(60)                              # Bridge hat noch nicht neu gepollt
    r.tick()
    assert r.events() == ["start"]


def test_unconfirmed_start_is_not_an_override_and_late_command_is_adopted():
    """Punkt 2 der Prüfung: Befehl kommt erst nach Minuten an (Broker/Bridge weg)."""
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    r.advance(200)                             # Schonfrist vorbei, Gerät meldet weiter Auto
    r.tick()
    assert r.events() == ["start", "unconfirmed"]
    assert r.phase() == "hold" and r.c.status(r.now)["devices"][MASTER]["pause_left_s"] == 0
    r.advance(5 * 60)
    r.confirm()                                # jetzt kommt der Befehl an
    r.tick()
    assert r.events() == ["start", "unconfirmed", "adopted"]
    assert r.phase() == "cooling"
    r.advance(16 * 60)
    r.outdoor(27.5)                            # und wird nach der Regel beendet
    assert r.events()[-1] == "end"
    assert r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


def test_late_command_outside_adopt_window_is_manual():
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    r.advance(200)
    r.tick()
    assert r.events() == ["start", "unconfirmed"]
    r.advance(31 * 60)                         # nach dem Übernahmefenster
    r.confirm()
    r.tick()
    assert r.events() == ["start", "unconfirmed"]
    assert r.phase() == "manual_mode"


def test_send_failure_is_logged_and_retried_after_hold():
    r = Rig(send_ok=False)
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    assert r.events() == ["send_failed"]
    assert r.c.status(r.now)["devices"][MASTER]["cooling"] is False
    r.advance(60)
    r.tick()
    assert r.events() == ["send_failed"]       # Haltezeit, nicht jeden Tick
    r.send_ok = True
    r.advance(15 * 60)
    r.tick()
    assert r.events() == ["send_failed", "start"]


def test_end_command_failure_is_retried_until_device_confirms():
    r = cooling_rig()
    r.advance(16 * 60)
    r.send_ok = False
    r.outdoor(26.5)
    assert r.events() == ["start", "end"]
    assert "nicht gesendet" in r.store.query_log()[0]["reason"]
    r.send_ok = True
    r.advance(61)
    r.tick()                                   # Gerät meldet weiter den Kühlmodus -> wiederholen
    assert r.events() == ["start", "end", "end"]
    assert r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.tick()
    assert r.c.status(r.now)["devices"][MASTER]["restore_mode"] is None


# --------------------------------------------------------------- Rückschaltung wiederholen
def test_restore_is_retried_with_backoff_and_shows_restore_failed():
    r = cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start", "end"]
    n = len(r.cmds())
    r.advance(60)
    r.tick()
    assert len(r.cmds()) == n                  # Bestätigungsfrist läuft noch
    for i in range(1, RESTORE_FAST_ATTEMPTS + 1):
        r.advance(181)                         # Gerät meldet weiter den Kühlmodus
        r.tick()
        assert len(r.cmds()) == n + i and r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}
    assert r.phase() == "restore_failed"       # sichtbar nach drei Fehlversuchen
    r.advance(181)
    r.tick()
    assert len(r.cmds()) == n + RESTORE_FAST_ATTEMPTS         # jetzt nur noch alle 30 min
    r.advance(RESTORE_SLOW_S)
    r.tick()
    assert len(r.cmds()) == n + RESTORE_FAST_ATTEMPTS + 1
    assert "wiederholt" in r.store.query_log()[0]["reason"]
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.tick()
    assert r.phase() in ("hold", "ready")      # bestätigt


def test_restore_is_retried_even_when_guard_was_disabled_meanwhile():
    """Punkt 4 der Prüfung: Wächter aus + verlorener Befehl darf kein Gerät im Kühlmodus lassen."""
    r = cooling_rig()
    r.c.update_config({"enabled": False})
    assert r.events() == ["start", "end"]
    n = len(r.cmds())
    r.advance(181)
    r.tick()                                   # Gerät meldet weiter den Kühlmodus
    assert len(r.cmds()) == n + 1 and r.cmds()[-1]["operating_mode"] == "Auto"
    assert r.events() == ["start", "end", "end"]


def test_restart_with_pending_restore_keeps_retrying():
    path = os.path.join(tempfile.mkdtemp(), "fc.db")
    r = cooling_rig(store=FreeCoolingStore(path))
    r.advance(16 * 60)
    r.outdoor(26.5)
    devices = r.devices
    r2 = Rig(store=FreeCoolingStore(path))
    r2.devices = devices
    r2.c.get_devices = lambda: devices
    r2.now = r.now + dt.timedelta(seconds=200)
    r2.tick()
    assert r2.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


# --------------------------------------------------------------- NeuraCell-X
def test_protection_blocks_all_commands():
    r = Rig(protection=True)
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    r.tick()
    assert r.cmds() == [] and r.events() == []
    assert r.phase() == "protected"
    r2 = cooling_rig()
    r2.protection = True
    r2.advance(16 * 60)
    r2.outdoor(26.5)
    assert r2.events() == ["start"]            # kein Ende, solange der Schutz greift
    r2.protection = False
    r2.tick()
    assert r2.events() == ["start", "end"]


def test_protection_release_window_is_not_an_override():
    """Punkt 1 der Prüfung: Nach der Freigabe meldet das Gerät kurz noch Intake."""
    r = cooling_rig()
    r.advance(10 * 60)
    r.protection = True
    r.devices[MASTER]["operating_mode"] = "Intake"   # Bridge hat den Schutzmodus gesetzt
    r.tick()
    r.advance(20 * 60)
    r.protection = False                       # Schutz aufgehoben, Bridge pollt erst in 30 s
    r.tick()
    assert r.events() == ["start"]             # kein override
    r.advance(30)
    r.confirm()                                # Bridge hat den Kühlmodus wiederhergestellt
    r.tick()
    assert r.phase() == "cooling"
    r.outdoor(31)                              # Außen wärmer -> Ende, nicht "manual_mode"
    assert r.events() == ["start", "end"]
    assert r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


def test_protection_release_with_user_change_after_grace_is_override():
    r = cooling_rig()
    r.protection = True
    r.devices[MASTER]["operating_mode"] = "Intake"
    r.tick()
    r.protection = False
    r.tick()
    r.advance(200)                             # Schonfrist vorbei, Gerät steht auf Night
    r.devices[MASTER]["operating_mode"] = "Night"
    r.tick()
    assert r.events() == ["start", "override"]


# --------------------------------------------------------------- Nutzereingriff
def test_manual_override_pauses_guard():
    r = cooling_rig()
    r.advance(200)
    r.devices[MASTER]["operating_mode"] = "Night"   # Nutzer hat umgestellt
    r.tick()
    assert r.events() == ["start", "override"]
    assert len(r.cmds()) == 1                  # nichts zurückgestellt
    st = r.c.status(r.now)["devices"][MASTER]
    assert st["phase"] == "override" and st["cooling"] is False and st["pause_left_s"] > 0
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(30 * 60)
    r.outdoor(23)
    r.tick()
    assert r.events() == ["start", "override"]  # Pause 60 min
    r.advance(31 * 60)
    r.outdoor(23)
    r.tick()
    assert r.events() == ["start", "override", "start"]


def test_user_choosing_smart_is_respected_during_pause():
    """Punkt 6 der Prüfung: SMART-Sperre nicht gegen einen frischen Eingriff."""
    r = cooling_rig()
    r.advance(200)
    r.devices[MASTER]["operating_mode"] = "Smart"
    r.tick()
    assert r.events() == ["start", "override"]
    r.advance(60)
    r.tick()
    assert r.events() == ["start", "override"] and len(r.cmds()) == 1
    assert r.phase() == "override"
    r.advance(61 * 60)                         # nach der Pause greift die Sperre wieder
    r.tick()
    assert r.events() == ["start", "override", "smart_blocked"]


# --------------------------------------------------------------- SMART-Sperre
def test_block_smart_backs_off():
    r = Rig()
    r.devices[MASTER] = dev(t=22, mode="Smart", fan="Medium")
    r.outdoor(23)
    assert r.cmds() == [{"operating_mode": "Auto", "fan_speed": "Medium"}]
    assert r.events() == ["smart_blocked"] and r.phase() == "smart_blocked"
    r.advance(60)
    r.tick()
    assert len(r.cmds()) == 1                  # innerhalb der Bestätigungsfrist nicht erneut
    for i in range(2, SMART_BLOCK_FAST + 1):
        r.advance(181)
        r.tick()
        assert len(r.cmds()) == i              # Gerät meldet weiter SMART -> erneut
    r.advance(181)
    r.tick()
    assert len(r.cmds()) == SMART_BLOCK_FAST   # danach nur noch stündlich
    r.advance(SMART_BLOCK_SLOW_S)
    r.tick()
    assert len(r.cmds()) == SMART_BLOCK_FAST + 1
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.outdoor(23)                              # Außenwert war inzwischen abgelaufen
    assert r.c.status(r.now)["devices"][MASTER]["phase"] == "hold"


def test_block_smart_can_be_disabled_and_smart_is_then_left_alone():
    r = Rig(block_smart=False)
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    assert r.cmds() == []


def test_smart_block_then_hold_then_start():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    assert r.events() == ["smart_blocked"]
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(60)
    r.tick()
    assert r.events() == ["smart_blocked"] and r.phase() == "hold"
    r.advance(15 * 60)
    r.tick()
    assert r.events() == ["smart_blocked", "start"]


# --------------------------------------------------------------- Geräteauswahl / Frische
def test_masters_only_by_default_and_explicit_list():
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.devices[SLAVE] = dev(serial=SLAVE, t=27, role="Slave")
    r.outdoor(23)
    assert r.cmds(MASTER) and not r.cmds(SLAVE)
    assert r.phase(SLAVE) == "not_controlled"
    r2 = Rig(devices=[SLAVE])
    r2.devices[MASTER] = dev(t=27)
    r2.devices[SLAVE] = dev(serial=SLAVE, t=27, role="Slave")
    r2.outdoor(23)
    assert r2.cmds(SLAVE) and not r2.cmds(MASTER)


def test_excluding_a_cooling_device_restores_it():
    r = cooling_rig()
    r.c.update_config({"devices": [SLAVE]})
    assert r.events() == ["start", "end"]
    assert "excluded" in r.store.query_log(serial=MASTER)[0]["reason"]


def test_bridge_pseudo_entry_is_ignored():
    r = Rig()
    r.devices["bridge"] = {"serial": "bridge", "online": True, "lastSeen": 1}   # nur availability
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    assert "bridge" not in r.c.status(r.now)["devices"]
    assert r.events() == ["start"] and not r.cmds("bridge")


def test_offline_or_stale_device_and_offline_bridge_get_no_commands():
    """Punkt 7 der Prüfung: alte Gerätewerte oder Bridge offline sperren jede Entscheidung."""
    r = Rig()
    r.devices[MASTER] = dev(t=27, online=False)
    r.outdoor(23)
    assert r.cmds() == [] and r.phase() == "stale"
    r2 = Rig()
    r2.devices[MASTER] = dev(t=27, last_seen=(T0 - dt.timedelta(hours=6)).timestamp())
    r2.outdoor(23)
    assert r2.cmds() == [] and r2.phase() == "stale"
    r3 = Rig()
    r3.devices["bridge"] = {"serial": "bridge", "online": False}
    r3.devices[MASTER] = dev(t=27, last_seen=T0.timestamp())
    r3.outdoor(23)
    assert r3.cmds() == [] and r3.phase() == "stale"
    r3.devices["bridge"]["online"] = True
    r3.tick()
    assert r3.events() == ["start"]


def test_bridge_outage_while_cooling_gets_grace_after_return():
    r = cooling_rig()
    r.advance(5 * 60)
    r.devices["bridge"] = {"serial": "bridge", "online": False}
    r.devices[MASTER]["operating_mode"] = "Auto"     # letzter bekannter Stand ist alt/unklar
    r.advance(30 * 60)
    r.tick()
    assert r.events() == ["start"]             # nichts entschieden
    r.devices["bridge"]["online"] = True
    r.tick()
    assert r.events() == ["start"]             # Schonfrist nach der Rückkehr
    r.advance(30)
    r.confirm()
    r.tick()
    assert r.phase() == "cooling"


# --------------------------------------------------------------- Tag / Nacht
def test_night_fan_by_sensor_and_hours():
    r = Rig()
    r.devices[MASTER] = dev(t=27, night=True)
    r.outdoor(23)
    assert r.cmds()[-1]["fan_speed"] == "Low"
    assert r.store.query_log()[0]["night"] == 1
    r2 = Rig(night_hours="22:00-06:00")
    r2.now = T0.replace(hour=23)
    r2.devices[MASTER] = dev(t=27)
    r2.outdoor(23)
    assert r2.cmds()[-1]["fan_speed"] == "Low"
    r3 = Rig(night_hours="22:00-06:00")
    r3.now = T0.replace(hour=14)
    r3.devices[MASTER] = dev(t=27)
    r3.outdoor(23)
    assert r3.cmds()[-1]["fan_speed"] == "Medium"


def test_fan_follows_day_night_while_cooling_edge_triggered():
    r = cooling_rig()
    r.advance(60)
    r.devices[MASTER]["night_alarm"] = True
    r.tick()
    assert r.cmds()[-1] == {"operating_mode": COOL, "fan_speed": "Low"}
    assert r.events() == ["start", "fan"]
    r.tick()
    assert len(r.cmds()) == 2                  # flankengesteuert, nicht jeden Tick
    # Punkt 11: Stufe stimmt schon (Nutzer hat Low gewählt) -> Flanke trotzdem merken
    r2 = cooling_rig()
    r2.devices[MASTER]["fan_speed"] = "Low"
    r2.devices[MASTER]["night_alarm"] = True
    r2.tick()
    assert len(r2.cmds()) == 1                 # nichts zu senden
    r2.devices[MASTER]["night_alarm"] = False  # Morgen: zurück auf die Tagstufe
    r2.tick()
    assert r2.cmds()[-1] == {"operating_mode": COOL, "fan_speed": "Medium"}


def test_hours_helpers():
    assert _parse_hours("22:00-06:00") == (1320, 360)
    assert _parse_hours("7:30-9:00") == (450, 540)
    assert _parse_hours("25:00-06:00") is None and _parse_hours("") is None
    assert _in_hours((1320, 360), 23 * 60) and _in_hours((1320, 360), 5 * 60)
    assert not _in_hours((1320, 360), 12 * 60)
    assert _in_hours((450, 540), 480) and not _in_hours((450, 540), 540)


# --------------------------------------------------------------- Außenwert
def test_sensor_beats_weather_and_weather_is_fallback():
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.outdoor(30, kind="weather", source="owm")
    r.outdoor(23, kind="sensor", source="haus")
    assert r.events() == ["start"]
    assert r.store.query_log()[0]["t_out_source"] == "sensor:haus"
    r2 = Rig()
    r2.devices[MASTER] = dev(t=22)
    r2.outdoor(23, kind="sensor", source="haus")
    r2.advance(61 * 60)                        # Sensor abgelaufen
    r2.devices[MASTER]["temperature"] = 27
    r2.outdoor(23.5, kind="weather", source="owm")
    assert r2.events() == ["start"]
    assert r2.store.query_log()[0]["t_out_source"] == "weather:owm"


def test_outdoor_store_rejects_implausible_values():
    o = OutdoorStore(ttl_s=60)
    assert o.update("sensor", "abc") is None
    assert o.update("sensor", 99) is None
    assert o.update("bogus", 20) is None
    assert o.update("sensor", "21,5", "55", "x").temperature == 21.5
    assert o.current(ttl_s=60).humidity == 55
    assert o.set_humidity("sensor", 60) and o.current(ttl_s=60).humidity == 60
    assert not o.set_humidity("weather", 60)


def test_parse_payload_variants_and_clean_source():
    assert parse_payload('{"temperature": 21.4, "humidity": 60}') == (21.4, 60.0)
    assert parse_payload('{"temp": "19,5"}') == (19.5, None)
    assert parse_payload("18.25") == (18.25, None)
    assert parse_payload("") == (None, None)
    assert parse_payload("[1,2]") == (None, None)
    assert parse_payload("nix") == (None, None)
    assert clean_source('<img src=x onerror=alert(1)>haus', "x") == "img src=x onerror=alert(1)haus"
    assert clean_source(None, "http:sensor") == "http:sensor"
    assert len(clean_source("a" * 200, "x")) == 80


def test_status_push_is_throttled_for_frequent_readings():
    r = Rig()
    r.devices[MASTER] = dev(t=22)
    for i in range(30):
        r.outdoor(23.0, source="haus")        # alle 2 s derselbe Wert
        r.advance(2)
    assert len(r.pushed) <= 2
    r.outdoor(24.0, source="haus")             # geänderter Wert -> Push
    assert len(r.pushed) >= 2


def test_openweathermap_helpers_with_fake_fetch():
    calls = []
    def fake(url):
        calls.append(url)
        if "/geo/" in url:
            return [{"lat": 50.08, "lon": 8.24, "name": "Wiesbaden", "country": "DE"}]
        return {"main": {"temp": 21.3, "humidity": 58}, "name": "Wiesbaden"}
    assert resolve_location("k", "50.1, 8.2", fetch=fake) == (50.1, 8.2, "50.1, 8.2")
    assert resolve_location("k", "Wiesbaden,DE", fetch=fake) == (50.08, 8.24, "Wiesbaden, DE")
    assert fetch_openweathermap("k", 50.08, 8.24, fetch=fake) == (21.3, 58.0, "Wiesbaden")
    with pytest.raises(ValueError):
        resolve_location("k", "Nirgendwo", fetch=lambda u: [])
    with pytest.raises(ValueError):
        fetch_openweathermap("k", 1, 1, fetch=lambda u: {"cod": 401})


def test_temperature_avg_preferred_unless_disabled():
    r = Rig()
    r.devices[MASTER] = dev(t=25, t_avg=27.2)
    r.outdoor(23)
    assert r.store.query_log()[0]["t_in"] == 27.2
    r2 = Rig(use_avg_temperature=False)
    r2.devices[MASTER] = dev(t=25, t_avg=27.2)
    r2.outdoor(23)
    assert r2.events() == []                   # 25 - 23 = 2 K


# --------------------------------------------------------------- Persistenz / Konfiguration
def test_config_persists_and_validates():
    path = os.path.join(tempfile.mkdtemp(), "fc.db")
    r = Rig(store=FreeCoolingStore(path))
    r.c.update_config({"delta_on": 2.5, "fan_night": "medium", "night_hours": "23:00-05:30"})
    assert r.c.config.fan_night == "Medium"
    r2 = Rig(store=FreeCoolingStore(path))
    assert r2.c.config.delta_on == 2.5 and r2.c.config.night_hours == "23:00-05:30"
    assert r2.c.config.enabled is True
    rows = r2.store.query_log()
    assert rows[0]["event"] == "config" and "delta_on=2.5" in rows[0]["reason"]
    for bad in ({"delta_off": 3.0}, {"delta_on": 0}, {"fan_day": "Turbo"}, {"rest_mode": "Smart"},
                {"cool_mode": "Auto"}, {"cool_mode": "Intake"}, {"night_hours": "22-06"},
                {"outdoor_ttl_s": 10}, {"min_hold_s": 0}, {"min_hold_s": 30},
                {"confirm_grace_s": 0}, {"state_ttl_s": 5}, {"t_out_hyst": -1}):
        with pytest.raises(ValueError):
            r2.c.update_config(bad)
    assert r2.c.config.delta_on == 2.5          # ungültige Werte ändern nichts


def test_runtime_state_survives_restart():
    path = os.path.join(tempfile.mkdtemp(), "fc.db")
    r = cooling_rig(store=FreeCoolingStore(path))
    devices = r.devices
    # Neustart: neuer Controller, gleicher Speicher, Gerät noch im Kühlmodus
    r2 = Rig(store=FreeCoolingStore(path))
    r2.devices = devices
    r2.c.get_devices = lambda: devices
    r2.now = r.now + dt.timedelta(minutes=16)
    assert r2.c.status(r2.now)["devices"][MASTER]["cooling"] is True
    r2.outdoor(26.5)
    assert r2.events() == ["start", "end"]
    assert r2.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


def test_future_timestamps_are_clamped_after_clock_jump():
    path = os.path.join(tempfile.mkdtemp(), "fc.db")
    r = cooling_rig(store=FreeCoolingStore(path))
    r.advance(200)
    r.devices[MASTER]["operating_mode"] = "Night"
    r.tick()                                   # override mit Pause bis T+60 min
    devices = r.devices
    r2 = Rig(store=FreeCoolingStore(path))     # Uhr springt zurück auf T0
    r2.devices = devices
    r2.c.get_devices = lambda: devices
    st = r2.c.status(r2.now)["devices"][MASTER]
    assert st["pause_left_s"] == 0 and st["hold_left_s"] <= 900   # statt Stunden/Jahren


def test_env_config_and_invalid_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FREECOOLING_ENABLED", "true")
    monkeypatch.setenv("FREECOOLING_DEVICES", "A, B ,")
    monkeypatch.setenv("FREECOOLING_DELTA_ON", "2")
    monkeypatch.setenv("FREECOOLING_DELTA_OFF", "0.5")
    monkeypatch.setenv("FREECOOLING_FAN_NIGHT", "low")
    c = FreeCoolingConfig.from_env()
    assert c.enabled and c.devices == ["A", "B"] and c.delta_on == 2 and c.fan_night == "Low"
    monkeypatch.setenv("FREECOOLING_FAN_DAY", "Turbo")     # Punkt 10: darf das Backend nicht stoppen
    ctl = FreeCoolingController(get_devices=lambda: {}, publish=lambda s, p: None,
                                store=FreeCoolingStore(":memory:"))
    assert ctl.config.fan_day == "Medium" and ctl.config.enabled is False


def test_log_csv_and_purge():
    r = cooling_rig()
    csv = log_to_csv(r.store.query_log(newest_first=False, limit=None))
    assert csv.splitlines()[0].startswith("ts_utc;serial;device_name;event")
    assert COOL in csv
    assert r.store.purge_log(retention_days=0) >= 1
    assert r.store.log_count() == 0


# --------------------------------------------------------------- Backend-Integration
def test_retained_outdoor_messages_are_ignored():
    """Punkt 8 der Prüfung: retained Werte haben ein unbekanntes Alter."""
    os.environ.setdefault("HISTORY_DB", os.path.join(tempfile.mkdtemp(), "app.db"))
    import importlib
    main = importlib.import_module("main")
    import asyncio
    main.loop = asyncio.new_event_loop()
    calls = []
    main.loop.call_soon_threadsafe = lambda fn, *a: calls.append((fn, a))
    main._on_outdoor_mqtt("ambientika/weather", '{"temperature": 21}', retained=True)
    assert calls == []
    main._on_outdoor_mqtt("ambientika/weather", '{"temperature": 21}', retained=False)
    assert calls and calls[0][1][:2] == ("sensor", 21.0)


@pytest.fixture(scope="module")
def api():
    os.environ.setdefault("HISTORY_DB", os.path.join(tempfile.mkdtemp(), "app.db"))
    import importlib
    main = importlib.import_module("main")
    main.freecooling.publish = lambda s, p: None    # kein Broker im Test
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        yield client, main


def test_api_roundtrip(api):
    client, main = api
    st = client.get("/api/freecooling").json()
    assert st["config"]["enabled"] is False and st["outdoor"]["valid"] is False
    assert client.put("/api/freecooling/config", json={"delta_off": 5}).status_code == 400
    assert client.put("/api/freecooling/config", json={"min_hold_s": 0}).status_code == 400
    cfg = client.put("/api/freecooling/config", json={"enabled": True, "delta_on": 2.5}).json()
    assert cfg["enabled"] is True and cfg["delta_on"] == 2.5
    assert client.post("/api/freecooling/outdoor", json={"temperature": 99}).status_code == 400
    assert client.post("/api/freecooling/outdoor", json={"temperature": 21.5, "kind": "x"}).status_code == 400
    res = client.post("/api/freecooling/outdoor",
                      json={"temperature": 21.5, "humidity": 60, "kind": "weather",
                            "source": "<b>pwa</b>"}).json()
    assert res["reading"]["kind"] == "weather" and res["reading"]["source"] == "bpwa/b"
    assert res["outdoor"]["valid"] is True
    main.devices[MASTER] = dev(t=27)
    ev = client.post("/api/freecooling/evaluate").json()
    assert [e["event"] for e in ev["events"]] == ["start"]
    assert ev["state"]["devices"][MASTER]["phase"] == "starting"
    rows = client.get("/api/freecooling/log", params={"serial": MASTER}).json()
    assert rows[0]["event"] == "start" and rows[0]["t_out_source"] == "weather:bpwa/b"
    csv = client.get("/api/freecooling/log.csv").text
    assert "start" in csv and csv.startswith("ts_utc;")
    smart = client.get(f"/api/devices/{MASTER}/smart").json()
    assert smart["sensors"]["temperature_out"] == 21.5
    assert client.get("/api/health").json()["freecooling"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
