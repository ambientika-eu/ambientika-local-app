#!/usr/bin/env python3
"""PRECISE end-to-end smoke test for the FIXED ambientika_local_bridge.

Runs the REAL bridge against a REAL MQTT broker (amqtt, in-process) with a
simulated ventilation unit on the raw-TCP side and a real MQTT client on the
app side. Verifies, on live wire traffic:

  1. device connect -> opt-in setup pushed, status + availability published
  2. MQTT mode command  -> correct 13-byte frame reaches the unit
  3. radon ON           -> unit driven INTAKE/LOW, neuracell state = RADON
  4. command under protection is suppressed
  5. radon OFF          -> EXACT restore of the pre-protection mode
  6. manual dew-point block ON -> unit OFF; OFF -> restore
  7. framing resync: junk byte ahead of a status frame is tolerated
  8. graceful shutdown  -> bridge availability flips offline (best effort)

Exit code 0 = all checks passed.
"""
import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.WARNING)

import paho.mqtt.client as mqtt
from amqtt.broker import Broker

import ambientika_local_bridge as alb

BROKER = "127.0.0.1"
MQTT_PORT = 1883
TCP_PORT = 41100
SERIAL = "AABBCCDDEEFF"
PREFIX = "ambientika"

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def build_status(mode=0, speed=1, hum_lvl=1, temp=22, hum=55, aq_raw=2,
                 hum_alarm=0, filt=0, night=0, role=0, last_mode=0, light=1, rssi=200):
    mac = bytes.fromhex(SERIAL)
    return bytes([0x01, 0x00]) + mac + bytes([
        mode, speed, hum_lvl, temp, hum, aq_raw, hum_alarm, filt, night,
        role, last_mode, light, rssi])


def split_downlink(buf: bytearray):
    """Parse 0x02-prefixed frames the bridge sends to the unit."""
    frames = []
    while len(buf) >= 9 and buf[0] == 0x02:
        t = buf[8]
        ln = 16 if t == 0x00 else (13 if t == 0x01 else (9 if t == 0x03 else None))
        if ln is None or len(buf) < ln:
            break
        frames.append(bytes(buf[:ln]))
        del buf[:ln]
    return frames


async def main():
    # --- start broker ------------------------------------------------------
    broker = Broker({
        "listeners": {"default": {"type": "tcp", "bind": f"{BROKER}:{MQTT_PORT}"}},
        "sys_interval": 0,
        "auth": {"allow-anonymous": True, "plugins": ["auth_anonymous"]},
    })
    await broker.start()

    # --- start the REAL bridge --------------------------------------------
    cfg = alb.Config()
    cfg.mqtt_host, cfg.mqtt_port = BROKER, MQTT_PORT
    cfg.tcp_host, cfg.tcp_port = "127.0.0.1", TCP_PORT
    cfg.scheduler_tick, cfg.neuracell_tick = 1, 1
    cfg.send_setup = True  # Exercise the explicit opt-in path in this test.
    bridge = alb.LocalBridge(cfg)
    bridge_task = asyncio.create_task(bridge.run())
    await asyncio.sleep(1.0)

    # --- app-side MQTT client (subscribe + publish) ------------------------
    msgs = {}   # topic -> last payload (str)
    cav = getattr(mqtt, "CallbackAPIVersion", None)
    app = mqtt.Client(callback_api_version=cav.VERSION2, client_id="smoke-app") if cav \
        else mqtt.Client(client_id="smoke-app")
    def on_msg(c, u, m):
        msgs[m.topic] = m.payload.decode()
    app.on_message = on_msg
    app.connect(BROKER, MQTT_PORT, 60)
    app.subscribe(f"{PREFIX}/#")
    app.loop_start()
    await asyncio.sleep(0.5)

    check("broker: bridge availability online",
          msgs.get(f"{PREFIX}/bridge/availability") == "online",
          msgs.get(f"{PREFIX}/bridge/availability", "<none>"))

    # --- simulate a device on raw TCP -------------------------------------
    reader, writer = await asyncio.open_connection("127.0.0.1", TCP_PORT)
    downlink = bytearray()
    async def pump():
        try:
            while True:
                b = await reader.read(256)
                if not b:
                    break
                downlink.extend(b)
        except asyncio.CancelledError:
            pass
    pump_task = asyncio.create_task(pump())

    # 1) device sends status
    writer.write(build_status(mode=0, speed=1)); await writer.drain()
    await asyncio.sleep(0.6)
    frames = split_downlink(downlink)
    setup = [f for f in frames if len(f) == 16 and f[8] == 0x00]
    check("1. opt-in setup pushed to unit on connect", len(setup) == 1,
          f"{len(setup)} setup frame(s)")
    st = msgs.get(f"{PREFIX}/{SERIAL}/status")
    ok_status = False
    if st:
        d = json.loads(st)
        ok_status = d.get("mode") == "SMART" and d.get("temperature") == 22
    check("1. status published (mode=SMART, temp=22)", ok_status, st or "<none>")
    check("1. availability online",
          msgs.get(f"{PREFIX}/{SERIAL}/availability") == "online")

    # 2) MQTT mode command -> unit frame
    app.publish(f"{PREFIX}/{SERIAL}/set", json.dumps({"mode": "BOOST", "fanSpeed": 100}))
    await asyncio.sleep(0.6)
    cmds = [f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]
    ok_cmd = len(cmds) == 1 and cmds[-1][9] == 6 and cmds[-1][10] == 2  # BOOST / HIGH
    check("2. mode command reached unit (mode=6 BOOST, speed=2)", ok_cmd,
          f"frame={cmds[-1].hex() if cmds else '<none>'}")

    # 3) radon ON -> INTAKE/LOW
    app.publish(f"{PREFIX}/radon/alarm", "ON")
    await asyncio.sleep(0.6)
    rc = [f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]
    ok_radon = len(rc) >= 1 and rc[-1][9] == alb.MODE_INTAKE and rc[-1][10] == alb.FAN_LOW
    check("3. radon protection drives unit INTAKE/LOW", ok_radon,
          f"frame={rc[-1].hex() if rc else '<none>'}")
    ncs = msgs.get(f"{PREFIX}/neuracell/state")
    ok_ncs = bool(ncs) and json.loads(ncs).get("activeProtection") == "RADON"
    check("3. neuracell state = RADON", ok_ncs, ncs or "<none>")

    # 4) command under protection suppressed
    downlink.clear()
    app.publish(f"{PREFIX}/{SERIAL}/set", json.dumps({"mode": "ECO"}))
    await asyncio.sleep(0.6)
    suppressed = len([f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]) == 0
    check("4. command suppressed while protection active", suppressed)

    # 5) radon OFF -> exact restore to BOOST/HIGH (the last normal target)
    downlink.clear()
    app.publish(f"{PREFIX}/radon/alarm", "OFF")
    await asyncio.sleep(0.6)
    rr = [f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]
    ok_restore = len(rr) >= 1 and rr[-1][9] == 6 and rr[-1][10] == 2
    check("5. exact restore of pre-protection mode (BOOST/HIGH)", ok_restore,
          f"frame={rr[-1].hex() if rr else '<none>'}")

    # 6) manual dew-point block ON -> OFF ; then release -> restore
    downlink.clear()
    app.publish(f"{PREFIX}/dewpoint/block", "ON")
    await asyncio.sleep(0.6)
    dp = [f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]
    ok_dew = len(dp) >= 1 and dp[-1][9] == alb.MODE_OFF
    check("6. dew-point block drives unit OFF", ok_dew,
          f"frame={dp[-1].hex() if dp else '<none>'}")
    downlink.clear()
    app.publish(f"{PREFIX}/dewpoint/block", "OFF")
    await asyncio.sleep(0.6)
    dr = [f for f in split_downlink(downlink) if len(f) == 13 and f[8] == 0x01]
    ok_dewr = len(dr) >= 1 and dr[-1][9] == 6 and dr[-1][10] == 2
    check("6. dew-point release restores BOOST/HIGH", ok_dewr,
          f"frame={dr[-1].hex() if dr else '<none>'}")

    # 7) framing resync: junk byte then a NIGHT status
    downlink.clear()
    writer.write(bytes([0x7F]) + build_status(mode=3, speed=1, temp=19))
    await writer.drain()
    await asyncio.sleep(0.6)
    st2 = msgs.get(f"{PREFIX}/{SERIAL}/status")
    ok_resync = bool(st2) and json.loads(st2).get("mode") == "NIGHT" and json.loads(st2).get("temperature") == 19
    check("7. parser resyncs after junk byte (status decoded)", ok_resync,
          st2 or "<none>")

    # 8) graceful shutdown -> bridge offline (best effort; LWT covers crashes)
    writer.close()
    with_ = getattr(writer, "wait_closed", None)
    if with_:
        try:
            await writer.wait_closed()
        except Exception:
            pass
    bridge_task.cancel()
    try:
        await bridge_task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.4)
    off = msgs.get(f"{PREFIX}/bridge/availability")
    check("8. bridge availability offline after shutdown", off == "offline", off or "<none>")

    # cleanup
    pump_task.cancel()
    app.loop_stop(); app.disconnect()
    await broker.shutdown()

    # summary
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n==== SMOKE TEST: {passed}/{total} checks passed ====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
