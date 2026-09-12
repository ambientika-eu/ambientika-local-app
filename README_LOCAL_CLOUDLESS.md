🌐 [DE](docs/README_LOCAL_CLOUDLESS.de.md) · **EN** · [IT](docs/README_LOCAL_CLOUDLESS.it.md) · [FR](docs/README_LOCAL_CLOUDLESS.fr.md) · [ES](docs/README_LOCAL_CLOUDLESS.es.md) · [NL](docs/README_LOCAL_CLOUDLESS.nl.md) · [PL](docs/README_LOCAL_CLOUDLESS.pl.md) · [PT](docs/README_LOCAL_CLOUDLESS.pt.md) · [SV](docs/README_LOCAL_CLOUDLESS.sv.md) · [DA](docs/README_LOCAL_CLOUDLESS.da.md) · [CS](docs/README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – local operating mode without the server

> **Scope.** The recommended operating mode of the Local App is the cloud bridge
> (`docker-compose.yml`): proven in the field and intended for regular operation. The
> local operating mode described here is designed as a **fallback**. It keeps the
> installation operable when the Ambientika server cannot be reached — during
> maintenance windows, network outages, or in buildings where a permanent internet
> connection is not intended. It does not replace the standard mode.

In this mode the Ambientika Local App (FastAPI + PWA) runs the installation **without
the Ambientika server and without an internet connection**. Compared to the standard
mode, only the device link changes: the bridge that polls the server is replaced by a
**local bridge** that addresses the ventilation units directly on the home network via
their native TCP protocol (port 11000).

```
Standard mode:  Unit → Ambientika server → cloud bridge → MQTT → app
Local mode:     Unit → local bridge (TCP 11000) → MQTT → app       ← no server
```

The full feature set is retained:

- device monitoring and control (mode, fan, sensors, dew point)
- **weekly schedule execution**
- **NeuraCell-X**: radon protection (priority) and **dew-point control**, with exact
  restore of the previously active mode

The Local App backend and PWA are used **unchanged** — the local bridge publishes the
same topics and the same field vocabulary as the standard mode (mode names
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int, `filterAlarm`
bool, plus `dewPoint`).

## Components

Only the device link is exchanged; the app and the control logic stay the same.

```
docker-compose.local.yml          # stack without the server poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # local bridge (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # local broker configuration
env.local.example.txt             # configuration template (no server credentials)
```

## Run

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Point the units at this host (required, one-time)

The units connect to whatever host was written during BLE provisioning:

1. **BLE re-provisioning:** write `H_<host-ip>:11000`, `S_<ssid>`,
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
| `RADON_THRESHOLD` | `100` | Bq/m³ auto-trip threshold |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | auto dew-point + °C hysteresis |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publish Home Assistant discovery (not needed by the app) |

## Quality assurance

- ✅ Wire codec byte-for-byte against the protocol specification (temperature & RSSI decoded
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

## Parameterisation

Mode and fan mappings as well as the thresholds for radon and dew-point protection are
preset with application-safe defaults and can be adapted per project in
`ambientika_local_bridge.py` (two mapping tables and the `Config` fields):
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, the fan-level
thresholds, and `RADON_THRESHOLD` and `DEWPOINT_MARGIN`. Site-specific limits — in
particular officially mandated radon thresholds — must be set at commissioning.

## Release status

The local operating mode is provided as a **controlled release (observation mode)**:
field validation across the entire deployed firmware base is not yet complete, and
feedback from installations is continuously fed back into the release. For regular
operation the cloud bridge remains the recommended variant; the local mode is the
fallback for the case that the server cannot be reached. Please report feedback via
the issues of this repository.
