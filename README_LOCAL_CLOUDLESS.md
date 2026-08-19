🌐 [DE](docs/README_LOCAL_CLOUDLESS.de.md) · **EN** · [IT](docs/README_LOCAL_CLOUDLESS.it.md) · [FR](docs/README_LOCAL_CLOUDLESS.fr.md) · [ES](docs/README_LOCAL_CLOUDLESS.es.md) · [NL](docs/README_LOCAL_CLOUDLESS.nl.md) · [PL](docs/README_LOCAL_CLOUDLESS.pl.md) · [PT](docs/README_LOCAL_CLOUDLESS.pt.md) · [SV](docs/README_LOCAL_CLOUDLESS.sv.md) · [DA](docs/README_LOCAL_CLOUDLESS.da.md) · [CS](docs/README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – 100% cloud-free app stack

Runs the Ambientika Local App (FastAPI + PWA) with **no SUEDWIND cloud and no
internet**. The only change versus the upstream stack is the data source: the
cloud-polling MQTT bridge is replaced by a **local bridge** that talks to the
ventilation units directly over their native raw-TCP protocol (port 11000).

The bridge now covers the full feature set cloud-free:

- device monitoring + control (mode, fan, sensors, dew point)
- **weekly schedule execution** (Wochenzeitplan)
- **NeuraCell-X**: radon protection (priority) + **dew-point control
  (Taupunktsteuerung)**, with exact restore of the previous mode.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

The local-app backend and PWA are used **unmodified** — the bridge publishes the
same topics and the same field vocabulary the app expects (friendly mode names
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, plus `dewPoint`).

## Files to add to the `ambientika-local-app` repo root

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Run

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Point the units at this host (required, one-time)

The units connect to whatever host was written during BLE provisioning:

1. **BLE re-provisioning (preferred):** write `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` to each unit.
2. **Static route / DNAT:** redirect `185.214.203.87/32` → this host and add an
   IP alias so the host accepts packets for the cloud IP.

Details in `CLOUD-INTEGRATION.md`.

## MQTT topics

| Topic | Dir | Meaning |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | device state (JSON, app vocabulary + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | `{mode, fanSpeed, ...}` command |
| `ambientika/<serial>/schedule/set` | in | full weekly schedule (from the app) |
| `ambientika/<serial>/schedule/<day>/set` | in | one day's slots |
| `ambientika/neuracell/state` | out | NeuraCell-X live status (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — force / clear radon protection |
| `ambientika/radon/value` | in | radon reading (Bq/m³) — auto trips at threshold |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — force / release dew-point block |
| `ambientika/weather` | in | `{"temperature": t, "humidity": rh}` OUTDOOR air |

## Weekly schedule

Edge-triggered: when a slot becomes active for the current weekday/time, the
bridge applies its `mode` (+ `fanSpeed`, or keeps the current speed if the slot
has none) exactly **once**, so a manual change inside a slot is not fought. The
schedule is suspended while a NeuraCell-X protection is engaged.

## NeuraCell-X (radon + dew-point)

Priority: **radon > dew-point > normal**. On the first transition into any
protection the bridge saves each unit's current mode/fan as a baseline; when all
protections clear it performs an **exact restore**.

- **Radon protection** — trips when `radon/alarm=ON` or `radon/value ≥
  RADON_THRESHOLD`. All units → `INTAKE` at `LOW` (gentle fresh-air overpressure).
  Normal `/set` commands are suppressed while active.
- **Dew-point control (Taupunktsteuerung)** — trips when `dewpoint/block=ON`, or
  automatically when the **outdoor** dew point is at/above the indoor dew point
  (minus `DEWPOINT_MARGIN`), i.e. ventilating would add moisture. All units →
  `OFF`. Needs outdoor data on `ambientika/weather`; without it, only the manual
  override works. Indoor dew point is computed from each unit's temp+humidity
  (Magnus formula).

## Configuration (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | topic prefix (keep `ambientika` to match the app) |
| `LOCAL_TCP_PORT` | `11000` | port the units connect to |
| `SEND_SETUP` | `false` | opt in to writing device topology on connect; verify all values below first |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | setup values used only when `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | schedule executor |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | radon+dew-point controller |
| `RADON_THRESHOLD` | `100` | Bq/m³ auto-trip threshold `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | auto dew-point + °C hysteresis `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publish Home Assistant discovery (not needed by the app) |

## Verification status

- ✅ Wire codec byte-for-byte against `PROTOCOL.md` (temperature & RSSI decoded
  **signed**).
- ✅ App-vocabulary round-trip (mode names, fanSpeed %, dew point).
- ✅ Weekly schedule: edge-trigger applies slots once; no-op otherwise; times
  normalised to `HH:MM`.
- ✅ NeuraCell-X: radon priority, command suppression, auto + manual dew-point
  with **±margin hysteresis**, and **exact restore** of the pre-protection mode
  (baseline taken from the last normal target, not the device echo).
- ✅ Concurrency hardened: a single lock serialises command/schedule/NeuraCell,
  protection state is committed **before** any protective write, device loops
  iterate snapshots, writes are per-device serialised.
- ✅ Robustness: TCP framing resyncs after a stray byte; malformed `weather`
  payloads rejected; reconnect preserves device state; radon/weather inputs
  older than `NC_INPUT_TTL` treated as unknown; MQTT last-will + clean shutdown.
- ✅ Regression suite: **40 unit/integration tests** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Full end-to-end through a **real MQTT broker** with a simulated unit
  (`smoke_test.py`, 13/13): status + command + schedule + radon protect/suppress/
  restore + dew-point + framing resync + shutdown.
- ✅ `docker compose config` valid; no cloud credentials anywhere in the stack.
- ✅ paho-mqtt 2.x callback API (VERSION2), 1.x fallback retained.
- ⛔️ **Not yet tested on real hardware** — reverse-engineered binary + safety-
  relevant control. Validate on one physical unit before production (in
  particular the signed temperature/RSSI decoding).

## Sign-off before production `>>> CONTROL <<<` / `>>> MAPPING <<<`

The mode/fan mappings and the radon/dew-point thresholds & targets are sensible
defaults, not certified. Have them reviewed against the product spec and tuned in
`ambientika_local_bridge.py` (two mapping tables + the `Config` control fields).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, the fan %→level
thresholds, `RADON_THRESHOLD`, and `DEWPOINT_MARGIN` are the values to confirm.
