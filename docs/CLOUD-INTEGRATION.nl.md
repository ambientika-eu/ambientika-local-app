🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · **NL** · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Ambientika-toestellen op de lokale bridge richten

Dit document legt uit hoe u een Ambientika **Smart / Office**-toestel met de
**lokale bridge** (`ambientika_local_bridge.py`) laat communiceren in plaats van met de
fabrikantcloud, zodat de hele stack in uw LAN draait zonder permanent internet.

Het is de aanvulling op `README_LOCAL_CLOUDLESS.md`. Lees de veiligheidsopmerkingen aan het
einde voordat u een productietoestel aanraakt.

---

## 1. Hoe een toestel bepaalt waarmee het verbindt

De ventilatietoestellen zijn **uitgaande TCP-clients**. Na de provisioning opent elk toestel
een persistente verbinding naar een **vaste host:port** en spreekt daar het native
binaire protocol:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Om cloudvrij te draaien hoeft u er alleen voor te zorgen dat die uitgaande verbinding op **uw
bridge-host** terechtkomt in plaats daarvan. Er zijn twee ondersteunde manieren:

- **Methode A – Netwerkomleiding (aanbevolen).** Laat het toestel zoals geprovisioneerd
  en leid het doel om op de router/firewall (of via lokaal DNS). Geen Bluetooth.
- **Methode B – BLE-herprovisioning.** Schrijf een nieuw doel rechtstreeks in het toestel
  via Bluetooth LE.

Kies **één** per toestel.

### Vereisten (beide methoden)

- De lokale stack draait en de bridge luistert op `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Het toestel en de bridge-host bevinden zich in hetzelfde LAN (of zijn naar elkaar routeerbaar).
- U kent het LAN-IP van de bridge-host (hierna aangeduid als `‹BRIDGE_IP›`).

---

## 2. Methode A — Netwerkomleiding (aanbevolen, zonder BLE)

Provisioneer het toestel **eenmaal, normaal, met de Ambientika-app** (dit vereist
kortstondig internet). Leid vervolgens het doel om in uw netwerk. Twee varianten — kies
degene die uw opstelling ondersteunt:

**A1 — Lokale DNS-override (het eenvoudigst als uw DNS onder uw beheer valt).**
Het toestel verbindt met de hostnaam `app.ambientika.eu`. Wijs die naam naar uw
bridge in uw lokale resolver (router / Pi-hole / Home-Assistant dnsmasq / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT op het vaste IP** (als het toestel het IP rechtstreeks gebruikt, of u
firewallregels verkiest): leid `185.214.203.87:11000 → ‹BRIDGE_IP›:11000` om.

Omdat het protocol puur TCP is (geen TLS, geen certificate-pinning), wordt de verbinding van het
toestel in beide gevallen transparant door uw lokale bridge geaccepteerd.

### Router-/firewallvoorbeelden (variant A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Veld | Waarde |
|-------|-------|
| Interface | LAN |
| Protocol | TCP |
| Bestemming | `185.214.203.87` |
| Bestemmingspoort | `11000` |
| Doel-IP van de omleiding | `‹BRIDGE_IP›` |
| Doelpoort van de omleiding | `11000` |

(Schakel *NAT reflection* in als de bridge en de toestellen dezelfde LAN-interface delen.)

**OpenWrt / generieke Linux-router** (`iptables`):

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

### Controleren

Kijk hoe de bridge tot leven komt zodra het toestel opnieuw verbindt (het probeert vanzelf
opnieuw; het toestel uit- en inschakelen versnelt dit):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

of, op de bridge-host: `sudo tcpdump -ni any tcp port 11000`.

### Opmerkingen & grenzen

- Variant A2 vereist een router/firewall die uitgaand WAN-verkeer kan DNAT'en. Veel
  eenvoudige ISP-routers kunnen dat niet — gebruik in dat geval A1 (DNS) of **Methode B**.
- Zuivere routing-alternatief voor A2: route `185.214.203.87/32` naar de bridge-host en
  voeg op die host een IP-alias voor `185.214.203.87` toe zodat hij de pakketten accepteert.

---

## 3. Methode B — BLE-herprovisioning (per toestel)

Schrijf het doel rechtstreeks in het toestel via Bluetooth LE. Gericht (één toestel
tegelijk), vereist geen routerwijzigingen.

### BLE-interface

| Element | Waarde |
|------|-------|
| Advertising-naam | `VMC_<MAC>` — de MAC is het serienummer van het toestel (12 hex-tekens) |
| WiFi-service-UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| WiFi-characteristic-UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Procedure

Schrijf deze drie waarden naar de WiFi-characteristic (`0xC302`), **in deze volgorde**:

1. `H_‹BRIDGE_IP›:11000`   — doel host:port (vervangt fabrieks `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi-SSID
3. `P_‹wifi-pw›`            — WiFi-wachtwoord

Het toestel verbindt dan met de WiFi en maakt verbinding met `H_` op poort 11000.

> **Verwachte eigenaardigheid:** elke schrijfactie kan een *"invalid length"-fout
> (code 13 / `0x0D`)* teruggeven. Dit is normaal — de waarde wordt toch toegepast. Negeer het.

### Handmatig (bluetoothctl)

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

### Hulpscript (bleak)

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

## 4. Terug naar de cloud

- **Methode A:** verwijder de DNAT-/port-forwardregel (en de DNS-override, indien gebruikt).
- **Methode B:** provisioneer het toestel opnieuw met de Ambientika-app, of schrijf het fabrieks-
  doel `H_app.ambientika.eu:11000` terug via BLE.

---

## 5. Veiligheid

- Test op **één** fysiek toestel voordat u dit uitrolt naar een volledige installatie.
- De verbinding is **puur TCP (geen TLS)** — houd de bridge en de toestellen op een
  **vertrouwd LAN**; stel poort 11000 niet bloot aan het internet.
- De ingebouwde functies van het toestel (inclusief vocht-/dauwpuntbescherming in de
  firmware) blijven werken ongeacht de bridge; de omleiding verandert alleen waar de
  status/commando's *op app-niveau* naartoe gaan.
- Rol terug met de stappen in sectie 4 als er iets misgaat.
