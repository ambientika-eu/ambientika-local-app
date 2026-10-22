#!/usr/bin/env python3
"""Tests des Free-Cooling-Wächters (backend/freecooling).

Prüft die Regel mit festgesetzter Zeit gegen die Vorgabe:
  Start  Ti > 24 °C, Ta > 20 °C, (Ti − Ta) >= 3 K
  Ende   (Ti − Ta) < 1 K, Ti <= 23,5 °C, Ta <= 20 °C; sofort bei fehlendem Außenwert
  Halten 15 min nach jedem Umschalten
sowie Rückschaltung, SMART-Sperre, Nutzereingriff, NeuraCell-X-Vorrang,
Außenwert-Quellen, Persistenz und die REST-Schnittstelle.

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

from freecooling.controller import FreeCoolingConfig, FreeCoolingController, _parse_hours, _in_hours  # noqa: E402
from freecooling.outdoor import OutdoorStore, parse_payload, resolve_location, fetch_openweathermap  # noqa: E402
from freecooling.store import FreeCoolingStore, log_to_csv  # noqa: E402

T0 = dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.timezone.utc)
MASTER = "AABBCCDDEE01"
SLAVE = "AABBCCDDEE02"


def dev(serial=MASTER, mode="Auto", fan="Low", t=27.0, t_avg=None, role="Master",
        online=True, night=False, name=None):
    return {
        "serial": serial, "name": name or f"Gerät {serial[-2:]}", "operating_mode": mode,
        "fan_speed": fan, "temperature": t, "temperature_avg": t_avg, "humidity": 50,
        "device_role": role, "online": online, "night_alarm": night,
    }


class Rig:
    """Controller mit Fake-Uhr, Fake-Sender und Speicher im Arbeitsspeicher."""

    def __init__(self, store=None, protection=False, **cfg):
        self.now = T0
        self.devices = {}
        self.sent = []            # (serial, payload)
        self.pushed = []          # Status-Pushes (on_change)
        self.protection = protection
        self.store = store or FreeCoolingStore(":memory:")
        cfg.setdefault("enabled", True)
        base = FreeCoolingConfig(**cfg)
        self.c = FreeCoolingController(
            get_devices=lambda: self.devices,
            publish=lambda s, p: self.sent.append((s, p)),
            store=self.store, config=base,
            get_protection=lambda: self.protection,
            on_change=lambda st: self.pushed.append(st),
            now_fn=lambda: self.now,
            local_tz=dt.timezone.utc,
        )

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


# --------------------------------------------------------------- Startregel
@pytest.mark.parametrize("t_in,t_out", [(30, 32), (32, 37), (26, 24), (24.0, 20.5), (27, 20.0), (25, 22.5)])
def test_no_start_cases(t_in, t_out):
    """30/32 und 32/37 (Außen wärmer), 26/24 (Abstand 2 K), Ti nicht > 24, Ta nicht > 20."""
    r = Rig()
    r.devices[MASTER] = dev(t=t_in)
    r.outdoor(t_out)
    r.tick()
    assert r.cmds() == []
    assert r.events() == []


def test_start_27_23():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Auto", fan="Low")
    r.outdoor(23)                              # on_outdoor() bewertet sofort
    assert r.cmds() == [{"operating_mode": "MasterSlaveFlow", "fan_speed": "Medium"}]
    assert r.events() == ["start"]
    assert r.tick() == []                      # nichts Neues im nächsten Takt
    row = r.store.query_log()[0]
    assert row["t_in"] == 27 and row["t_out"] == 23 and row["delta"] == 4.0
    assert row["mode_from"] == "Auto" and row["fan_from"] == "Low"
    assert row["mode_to"] == "MasterSlaveFlow" and row["fan_to"] == "Medium"
    assert row["t_out_source"] == "sensor:test" and row["t_out_age_s"] == 0
    assert row["night"] == 0
    assert r.c.status()["devices"][MASTER]["phase"] == "cooling"
    assert r.pushed and r.pushed[-1]["devices"][MASTER]["cooling"] is True


def test_start_just_above_thresholds():
    r = Rig()
    r.devices[MASTER] = dev(t=24.1)
    r.outdoor(20.1)                            # 24.1 > 24, 20.1 > 20, Abstand 4 K
    assert r.events() == ["start"]


def test_delta_exactly_on_threshold_starts():
    r = Rig()
    r.devices[MASTER] = dev(t=26.0)
    r.outdoor(23.0)                            # genau 3,0 K
    r.tick()
    assert r.events() == ["start"]


# --------------------------------------------------------------- Enderegel
def _cooling_rig(**cfg):
    r = Rig(**cfg)
    r.devices[MASTER] = dev(t=27, mode="Auto", fan="Low")
    r.outdoor(23)
    r.tick()
    assert r.events() == ["start"]
    r.devices[MASTER]["operating_mode"] = "MasterSlaveFlow"      # Bridge meldet den Modus
    r.devices[MASTER]["fan_speed"] = "Medium"
    return r


def test_end_when_outdoor_rises_to_26_5_after_hold():
    r = _cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)                            # Abstand 0,5 K < 1 K
    assert r.events() == ["start", "end"]
    assert r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}   # exakte Rückkehr
    assert "0.5 K < 1 K" in r.store.query_log()[0]["reason"]
    assert r.c.status()["devices"][MASTER]["cooling"] is False


def test_hold_time_blocks_end_but_not_forever():
    r = _cooling_rig()
    r.advance(5 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start"]             # innerhalb der Haltezeit: kein Ende
    r.advance(11 * 60)                         # 16 min nach dem Start
    r.tick()
    assert r.events() == ["start", "end"]


def test_hold_time_blocks_restart():
    r = _cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start", "end"]
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(60)
    r.outdoor(22)                              # Startbedingung wieder erfüllt
    r.tick()
    assert r.events() == ["start", "end"]      # Haltezeit nach dem Ende
    assert r.c.status()["devices"][MASTER]["phase"] == "hold"
    r.advance(15 * 60)
    r.tick()
    assert r.events() == ["start", "end", "start"]


def test_end_when_indoor_cools_to_23_5():
    r = _cooling_rig()
    r.advance(16 * 60)
    r.devices[MASTER]["temperature"] = 23.5
    r.tick()
    assert r.events() == ["start", "end"]


def test_end_when_outdoor_falls_to_20():
    r = _cooling_rig()
    r.advance(16 * 60)
    r.outdoor(20.0)
    assert r.events() == ["start", "end"]


def test_stale_outdoor_never_starts_and_ends_immediately():
    r = Rig()
    r.devices[MASTER] = dev(t=22)              # noch kein Start
    r.outdoor(23)
    r.advance(61 * 60)                         # Außenwert 61 min alt
    r.devices[MASTER]["temperature"] = 27
    r.tick()
    assert r.events() == []
    assert r.c.status()["devices"][MASTER]["phase"] == "no_outdoor"
    # laufendes Kühlen endet sofort, auch innerhalb der Haltezeit
    r2 = _cooling_rig()
    r2.advance(61 * 60)
    r2.tick()
    assert r2.events() == ["start", "end"]
    assert "sofort" in r2.store.query_log()[0]["reason"]
    assert r2.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


def test_disabled_never_starts_and_disable_ends_cooling():
    r = Rig(enabled=False)
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    r.tick()
    assert r.cmds() == [] and r.events() == []
    assert r.c.status()["devices"][MASTER]["phase"] == "disabled"
    r2 = _cooling_rig()
    r2.c.update_config({"enabled": False})     # wendet sofort an
    assert r2.events() == ["start", "end"]
    assert r2.cmds()[-1]["operating_mode"] == "Auto"


def test_night_baseline_restores_night_and_fan_night_becomes_low():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Night", fan="Night")
    r.outdoor(23)
    assert r.cmds() == [{"operating_mode": "MasterSlaveFlow", "fan_speed": "Medium"}]
    r.devices[MASTER]["operating_mode"] = "MasterSlaveFlow"
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
    assert r.c.status()["devices"][MASTER]["phase"] == "manual_mode"


def test_smart_left_alone_when_block_smart_off():
    r = Rig(block_smart=False)
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    assert r.cmds() == []


def test_smart_block_then_hold_then_start():
    r = Rig()
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    assert r.events() == ["smart_blocked"]
    assert r.c.status()["devices"][MASTER]["phase"] == "smart_blocked"
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(60)
    r.tick()
    assert r.events() == ["smart_blocked"]     # Haltezeit nach der Sperre
    assert r.c.status()["devices"][MASTER]["phase"] == "hold"
    r.advance(15 * 60)
    r.tick()
    assert r.events() == ["smart_blocked", "start"]


def test_restore_is_retried_once_when_device_stays_in_cool_mode():
    r = _cooling_rig()
    r.advance(16 * 60)
    r.outdoor(26.5)
    assert r.events() == ["start", "end"]
    assert len(r.cmds()) == 2
    r.advance(60)
    r.tick()
    assert len(r.cmds()) == 2                  # Bestätigungsfrist läuft noch
    r.advance(200)                             # Gerät meldet weiter MasterSlaveFlow
    r.tick()
    assert len(r.cmds()) == 3 and r.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}
    assert "wiederholt" in r.store.query_log()[0]["reason"]
    r.advance(300)
    r.tick()
    assert len(r.cmds()) == 3                  # nur einmal wiederholen
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.tick()
    assert r.c.status()["devices"][MASTER]["phase"] == "hold"     # Haltezeit nach dem Ende
    r.advance(15 * 60)
    assert r.c.status()["devices"][MASTER]["phase"] == "ready"


# --------------------------------------------------------------- SMART-Sperre
def test_block_smart_switches_to_auto_once_per_grace():
    r = Rig()
    r.devices[MASTER] = dev(t=22, mode="Smart", fan="Medium")
    r.tick()
    assert r.cmds() == [{"operating_mode": "Auto", "fan_speed": "Medium"}]
    assert r.events() == ["smart_blocked"]
    r.advance(60)
    r.tick()
    assert len(r.cmds()) == 1                  # innerhalb der Bestätigungsfrist nicht erneut
    r.advance(200)
    r.tick()
    assert len(r.cmds()) == 2                  # Gerät meldet weiter SMART -> erneut
    r.devices[MASTER]["operating_mode"] = "Auto"
    r.advance(200)
    r.tick()
    assert len(r.cmds()) == 2


def test_block_smart_can_be_disabled():
    r = Rig(block_smart=False)
    r.devices[MASTER] = dev(t=22, mode="Smart")
    r.tick()
    assert r.cmds() == []


# --------------------------------------------------------------- Nutzereingriff
def test_manual_override_pauses_guard():
    r = _cooling_rig()
    r.advance(200)                             # Bestätigungsfrist vorbei
    r.devices[MASTER]["operating_mode"] = "Night"   # Nutzer hat umgestellt
    r.tick()
    assert r.events() == ["start", "override"]
    assert len(r.cmds()) == 1                  # nichts zurückgestellt
    st = r.c.status()["devices"][MASTER]
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


def test_bridge_lag_within_grace_is_not_an_override():
    r = _cooling_rig()
    r.devices[MASTER]["operating_mode"] = "Auto"    # Bridge hat noch nicht neu gepollt
    r.advance(60)
    r.tick()
    assert r.events() == ["start"]


# --------------------------------------------------------------- NeuraCell-X
def test_protection_blocks_all_commands():
    r = Rig(protection=True)
    r.devices[MASTER] = dev(t=27, mode="Smart")
    r.outdoor(23)
    r.tick()
    assert r.cmds() == [] and r.events() == []
    assert r.c.status()["devices"][MASTER]["phase"] == "protected"
    r2 = _cooling_rig()
    r2.protection = True
    r2.advance(16 * 60)
    r2.outdoor(26.5)
    assert r2.events() == ["start"]            # kein Ende, solange der Schutz greift
    r2.protection = False
    r2.tick()
    assert r2.events() == ["start", "end"]


# --------------------------------------------------------------- Geräteauswahl
def test_masters_only_by_default_and_explicit_list():
    r = Rig()
    r.devices[MASTER] = dev(t=27)
    r.devices[SLAVE] = dev(serial=SLAVE, t=27, role="Slave")
    r.outdoor(23)
    r.tick()
    assert r.cmds(MASTER) and not r.cmds(SLAVE)
    assert r.c.status()["devices"][SLAVE]["phase"] == "not_controlled"
    r2 = Rig(devices=[SLAVE])
    r2.devices[MASTER] = dev(t=27)
    r2.devices[SLAVE] = dev(serial=SLAVE, t=27, role="Slave")
    r2.outdoor(23)
    r2.tick()
    assert r2.cmds(SLAVE) and not r2.cmds(MASTER)


def test_excluding_a_cooling_device_restores_it():
    r = _cooling_rig()
    r.c.update_config({"devices": [SLAVE]})
    assert r.events() == ["start", "end"]
    assert "excluded" in r.store.query_log(serial=MASTER)[0]["reason"]


def test_bridge_pseudo_entry_is_ignored():
    r = Rig()
    r.devices["bridge"] = {"serial": "bridge", "online": True, "lastSeen": 1}   # nur availability
    r.devices[MASTER] = dev(t=27)
    r.outdoor(23)
    assert "bridge" not in r.c.status()["devices"]
    assert r.events() == ["start"] and not r.cmds("bridge")


def test_offline_device_gets_no_commands():
    r = Rig()
    r.devices[MASTER] = dev(t=27, online=False)
    r.outdoor(23)
    r.tick()
    assert r.cmds() == []


# --------------------------------------------------------------- Tag / Nacht
def test_night_fan_by_sensor_and_hours():
    r = Rig()
    r.devices[MASTER] = dev(t=27, night=True)
    r.outdoor(23)
    r.tick()
    assert r.cmds()[-1]["fan_speed"] == "Low"
    assert r.store.query_log()[0]["night"] == 1
    r2 = Rig(night_hours="22:00-06:00")
    r2.now = T0.replace(hour=23)
    r2.devices[MASTER] = dev(t=27)
    r2.outdoor(23)
    r2.tick()
    assert r2.cmds()[-1]["fan_speed"] == "Low"
    r3 = Rig(night_hours="22:00-06:00")
    r3.now = T0.replace(hour=14)
    r3.devices[MASTER] = dev(t=27)
    r3.outdoor(23)
    r3.tick()
    assert r3.cmds()[-1]["fan_speed"] == "Medium"


def test_fan_follows_day_night_while_cooling():
    r = _cooling_rig()
    r.advance(60)
    r.devices[MASTER]["night_alarm"] = True
    r.tick()
    assert r.cmds()[-1] == {"operating_mode": "MasterSlaveFlow", "fan_speed": "Low"}
    assert r.events() == ["start", "fan"]
    r.tick()
    assert len(r.cmds()) == 2                  # flankengesteuert, nicht jeden Tick


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
    r.tick()
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


def test_parse_payload_variants():
    assert parse_payload('{"temperature": 21.4, "humidity": 60}') == (21.4, 60.0)
    assert parse_payload('{"temp": "19,5"}') == (19.5, None)
    assert parse_payload("18.25") == (18.25, None)
    assert parse_payload("") == (None, None)
    assert parse_payload("[1,2]") == (None, None)
    assert parse_payload("nix") == (None, None)


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
    r.tick()
    assert r.store.query_log()[0]["t_in"] == 27.2
    r2 = Rig(use_avg_temperature=False)
    r2.devices[MASTER] = dev(t=25, t_avg=27.2)
    r2.outdoor(23)
    r2.tick()
    assert r2.events() == []                   # 25 - 23 = 2 K


# --------------------------------------------------------------- Persistenz
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
                {"cool_mode": "Auto"}, {"night_hours": "22-06"}, {"outdoor_ttl_s": 10}):
        with pytest.raises(ValueError):
            r2.c.update_config(bad)
    assert r2.c.config.delta_on == 2.5          # ungültige Werte ändern nichts


def test_runtime_state_survives_restart():
    path = os.path.join(tempfile.mkdtemp(), "fc.db")
    r = _cooling_rig(store=FreeCoolingStore(path))
    devices = r.devices
    # Neustart: neuer Controller, gleicher Speicher, Gerät noch im Kühlmodus
    r2 = Rig(store=FreeCoolingStore(path))
    r2.devices = devices
    r2.c.get_devices = lambda: devices
    r2.now = r.now + dt.timedelta(minutes=16)
    assert r2.c.status()["devices"][MASTER]["cooling"] is True
    r2.outdoor(26.5)
    assert r2.events() == ["start", "end"]
    assert r2.cmds()[-1] == {"operating_mode": "Auto", "fan_speed": "Low"}


def test_env_config_and_from_env_validation(monkeypatch):
    monkeypatch.setenv("FREECOOLING_ENABLED", "true")
    monkeypatch.setenv("FREECOOLING_DEVICES", "A, B ,")
    monkeypatch.setenv("FREECOOLING_DELTA_ON", "2")
    monkeypatch.setenv("FREECOOLING_DELTA_OFF", "0.5")
    monkeypatch.setenv("FREECOOLING_FAN_NIGHT", "low")
    c = FreeCoolingConfig.from_env()
    assert c.enabled and c.devices == ["A", "B"] and c.delta_on == 2 and c.fan_night == "Low"


def test_log_csv_and_purge():
    r = _cooling_rig()
    csv = log_to_csv(r.store.query_log(newest_first=False, limit=None))
    assert csv.splitlines()[0].startswith("ts_utc;serial;device_name;event")
    assert "MasterSlaveFlow" in csv
    assert r.store.purge_log(retention_days=0) >= 1
    assert r.store.log_count() == 0


# --------------------------------------------------------------- REST-API
@pytest.fixture(scope="module")
def api():
    os.environ["HISTORY_DB"] = os.path.join(tempfile.mkdtemp(), "app.db")
    import importlib
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        yield client, main


def test_api_roundtrip(api):
    client, main = api
    st = client.get("/api/freecooling").json()
    assert st["config"]["enabled"] is False and st["outdoor"]["valid"] is False
    assert client.put("/api/freecooling/config", json={"delta_off": 5}).status_code == 400
    cfg = client.put("/api/freecooling/config", json={"enabled": True, "delta_on": 2.5}).json()
    assert cfg["enabled"] is True and cfg["delta_on"] == 2.5
    assert client.post("/api/freecooling/outdoor", json={"temperature": 99}).status_code == 400
    assert client.post("/api/freecooling/outdoor", json={"temperature": 21.5, "kind": "x"}).status_code == 400
    res = client.post("/api/freecooling/outdoor",
                      json={"temperature": 21.5, "humidity": 60, "kind": "weather", "source": "pwa"}).json()
    assert res["reading"]["kind"] == "weather" and res["outdoor"]["valid"] is True
    main.devices[MASTER] = dev(t=27)
    ev = client.post("/api/freecooling/evaluate").json()
    assert [e["event"] for e in ev["events"]] == ["start"]
    assert ev["state"]["devices"][MASTER]["phase"] == "cooling"
    rows = client.get("/api/freecooling/log", params={"serial": MASTER}).json()
    assert rows[0]["event"] == "start" and rows[0]["t_out_source"] == "weather:pwa"
    csv = client.get("/api/freecooling/log.csv").text
    assert "start" in csv and csv.startswith("ts_utc;")
    smart = client.get(f"/api/devices/{MASTER}/smart").json()
    assert smart["sensors"]["temperature_out"] == 21.5
    assert client.get("/api/health").json()["freecooling"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
