🌐 [DE](docs/CLOUD-INTEGRATION.de.md) · **EN** · [IT](docs/CLOUD-INTEGRATION.it.md) · [FR](docs/CLOUD-INTEGRATION.fr.md) · [ES](docs/CLOUD-INTEGRATION.es.md) · [NL](docs/CLOUD-INTEGRATION.nl.md) · [PL](docs/CLOUD-INTEGRATION.pl.md) · [PT](docs/CLOUD-INTEGRATION.pt.md) · [SV](docs/CLOUD-INTEGRATION.sv.md) · [DA](docs/CLOUD-INTEGRATION.da.md) · [CS](docs/CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Pointing Ambientika units at the local bridge

This document explains how to make an Ambientika **Smart / Office** unit talk to
the **local bridge** (`ambientika_local_bridge.py`) instead of the manufacturer
cloud, so the whole stack runs on your LAN with no permanent internet.

It is the companion to `README_LOCAL_CLOUDLESS.md`. Read the safety notes at the
end before touching a production unit.

---

## 1. How a unit decides where to connect

The ventilation units are **outbound TCP clients**. After provisioning, each unit
opens a persistent connection to a **fixed host:port** and speaks the native
binary protocol there:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

To run cloud-free you only have to make that outbound connection land on **your
bridge host** instead. There are two supported ways:

- **Method A – Network redirect (recommended).** Leave the unit as provisioned
  and redirect the target at the router/firewall (or via local DNS). No Bluetooth.
- **Method B – BLE re-provisioning.** Write a new target directly into the unit
  over Bluetooth LE.

Pick **one** per unit.

### Prerequisites (both methods)

- The local stack is running and the bridge is listening on `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- The unit and the bridge host are on the same LAN (or routable to each other).
- You know the bridge host's LAN IP (referred to below as `‹BRIDGE_IP›`).

---

## 2. Method A — Network redirect (recommended, no BLE)

Provision the unit **once, normally, with the Ambientika app** (this needs
internet briefly). Then redirect the target on your network. Two variants — pick
whichever your setup supports:

**A1 — Local DNS override (simplest if your DNS is under your control).**
The unit connects to the hostname `app.ambientika.eu`. Point that name at your
bridge in your local resolver (router / Pi-hole / Home-Assistant dnsmasq / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT on the fixed IP** (if the unit uses the IP directly, or you
prefer firewall rules): redirect `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Because the protocol is plain TCP (no TLS, no certificate pinning), the unit's
connection is transparently accepted by your local bridge either way.

### Router / firewall examples (variant A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Field | Value |
|-------|-------|
| Interface | LAN |
| Protocol | TCP |
| Destination | `185.214.203.87` |
| Destination port | `11000` |
| Redirect target IP | `‹BRIDGE_IP›` |
| Redirect target port | `11000` |

(Enable *NAT reflection* if the bridge and units share the LAN interface.)

**OpenWrt / generic Linux router** (`iptables`):

```bash
# ensure forwarding is on: sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A PREROUTING -p tcp -d 185.214.203.87 --dport 11000 \
         -j DNAT --to-destination ‹BRIDGE_IP›:11000
```

**MikroTik / RouterOS**:

```
/ip firewall nat add chain=dstnat protocol=tcp \
    dst-address=185.214.203.87 dst-port=11000 \
    action=dst-nat to-addresses=‹BRIDGE_IP› to-ports=11000
```

### Verify

Watch the bridge come alive as the unit reconnects (it retries on its own; a
power-cycle of the unit speeds it up):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

or, on the bridge host: `sudo tcpdump -ni any tcp port 11000`.

### Notes & limits

- Variant A2 needs a router/firewall that can DNAT outbound-to-WAN traffic. Many
  simple ISP routers cannot — use A1 (DNS) or **Method B** instead.
- Pure-routing alternative to A2: route `185.214.203.87/32` to the bridge host and
  add an IP alias for `185.214.203.87` on that host so it accepts the packets.

---

## 3. Method B — BLE re-provisioning (per unit)

Write the target directly into the unit over Bluetooth LE. Targeted (one unit at
a time), needs no router changes.

### BLE interface

| Item | Value |
|------|-------|
| Advertising name | `VMC_<MAC>` — the MAC is the unit's serial (12 hex chars) |
| WiFi service UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| WiFi characteristic UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Procedure

Write these three values to the WiFi characteristic (`0xC302`), **in this order**:

1. `H_‹BRIDGE_IP›:11000`   — target host:port (replaces factory `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi SSID
3. `P_‹wifi-pw›`            — WiFi password

The unit then joins the WiFi and connects to `H_` on port 11000.

> **Expected quirk:** each write may return an *"invalid length" error
> (code 13 / `0x0D`)*. This is normal — the value is still applied. Ignore it.

### Manual (bluetoothctl)

```bash
bluetoothctl
# at the prompt:
menu scan
transport le
back
scan on            # find:  [NEW] Device AA:BB:CC:DD:EE:FF  VMC_AABBCCDDEEFF
# then: connect AA:BB:CC:DD:EE:FF
#       (write the three strings to characteristic 0000c302)
```

### Helper script (bleak)

```python
# provision_ble.py — point an Ambientika unit at the local bridge over BLE.
#   pip install bleak
#   python provision_ble.py <BRIDGE_IP> "<SSID>" "<WIFI_PW>"
import asyncio, sys
from bleak import BleakScanner, BleakClient

CHAR_UUID   = "0000c302-0000-1000-8000-00805f9b34fb"   # WiFi characteristic (0xC302)
NAME_PREFIX = "VMC_"                                     # unit advertising name

async def main(bridge_ip, ssid, pw):
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: (d.name or "").upper().startswith(NAME_PREFIX), timeout=20.0)
    if not dev:
        sys.exit("No VMC_ unit found in BLE range. Power-cycle the unit and retry.")
    print("found", dev.name, dev.address)
    async with BleakClient(dev) as client:
        for value in (f"H_{bridge_ip}:11000", f"S_{ssid}", f"P_{pw}"):
            try:
                await client.write_gatt_char(CHAR_UUID, value.encode(), response=True)
                print("wrote", value.split('_', 1)[0] + "_…")
            except Exception as exc:
                # unit returns 'invalid length (code 13)' but still applies the value
                print("  (ignored write response:", exc, ")")
    print(f"done — the unit will join '{ssid}' and connect to {bridge_ip}:11000")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit('usage: python provision_ble.py <BRIDGE_IP> "<SSID>" "<WIFI_PW>"')
    asyncio.run(main(*sys.argv[1:4]))
```

---

## 4. Reverting to the cloud

- **Method A:** remove the DNAT / port-forward rule (and the DNS override, if used).
- **Method B:** re-provision the unit with the Ambientika app, or write the factory
  target `H_app.ambientika.eu:11000` back over BLE.

---

## 5. Safety

- Test on **one** physical unit before rolling this out to a whole installation.
- The link is **plain TCP (no TLS)** — keep the bridge and the units on a
  **trusted LAN**; do not expose port 11000 to the internet.
- The unit's onboard functions (including humidity / dew-point protection in the
  firmware) keep working regardless of the bridge; the redirect only changes where
  the *app-level* status/commands go.
- Roll back with the steps in section 4 if anything misbehaves.
