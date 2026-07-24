🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · **DA** · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Ret Ambientika-enheder mod den lokale bridge

Dette dokument forklarer, hvordan du får en Ambientika **Smart / Office**-enhed til
at kommunikere med den **lokale bridge** (`ambientika_local_bridge.py`) i stedet for
producentens cloud, så hele stacken kører i dit LAN uden permanent internet.

Det er ledsagedokumentet til `README_LOCAL_CLOUDLESS.md`. Læs sikkerhedsnoterne til
sidst, før du rører en produktionsenhed.

---

## 1. Hvordan en enhed afgør, hvor den skal forbinde

Ventilationsenhederne er **udgående TCP-klienter**. Efter provisioning åbner hver
enhed en persistent forbindelse til en **fast host:port** og taler den native
binærprotokol der:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

For at køre cloudfrit skal du blot sørge for, at den udgående forbindelse i stedet
lander på **din bridge-host**. Der findes to understøttede måder:

- **Metode A – Netværksomdirigering (anbefalet).** Lad enheden være som provisioneret
  og omdiriger målet på routeren/firewall'en (eller via lokal DNS). Ingen Bluetooth.
- **Metode B – BLE-genprovisionering.** Skriv et nyt mål direkte ind i enheden over
  Bluetooth LE.

Vælg **én** pr. enhed.

### Forudsætninger (begge metoder)

- Den lokale stack kører, og bridgen lytter på `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Enheden og bridge-host'en er på det samme LAN (eller kan rutes til hinanden).
- Du kender bridge-host'ens LAN-IP (herefter kaldet `‹BRIDGE_IP›`).

---

## 2. Metode A — Netværksomdirigering (anbefalet, uden BLE)

Provisionér enheden **én gang, normalt, med Ambientika-appen** (dette kræver
kortvarigt internet). Omdiriger derefter målet på dit netværk. To varianter — vælg
den, dit setup understøtter:

**A1 — Lokal DNS-tilsidesættelse (enklest, hvis din DNS er under din kontrol).**
Enheden forbinder til værtsnavnet `app.ambientika.eu`. Peg det navn mod din bridge i
din lokale resolver (router / Pi-hole / Home-Assistant dnsmasq / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT på den faste IP** (hvis enheden bruger IP'en direkte, eller du
foretrækker firewall-regler): omdiriger `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Da protokollen er ren TCP (ingen TLS, ingen certificate pinning), accepteres enhedens
forbindelse i begge tilfælde transparent af din lokale bridge.

### Router-/firewall-eksempler (variant A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Felt | Værdi |
|-------|-------|
| Interface | LAN |
| Protokol | TCP |
| Destination | `185.214.203.87` |
| Destinationsport | `11000` |
| Mål-IP for omdirigering | `‹BRIDGE_IP›` |
| Mål-port for omdirigering | `11000` |

(Aktivér *NAT reflection*, hvis bridgen og enhederne deler LAN-interfacet.)

**OpenWrt / generisk Linux-router** (`iptables`):

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

### Verificér

Se bridgen blive aktiv, når enheden genforbinder (den forsøger igen af sig selv; en
genstart af enheden fremskynder det):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

eller, på bridge-host'en: `sudo tcpdump -ni any tcp port 11000`.

### Noter & begrænsninger

- Variant A2 kræver en router/firewall, der kan DNAT'e udgående WAN-trafik. Mange
  simple ISP-routere kan ikke — brug A1 (DNS) eller **Metode B** i stedet.
- Rent routing-alternativ til A2: rut `185.214.203.87/32` til bridge-host'en og tilføj
  et IP-alias for `185.214.203.87` på den host, så den accepterer pakkerne.

---

## 3. Metode B — BLE-genprovisionering (pr. enhed)

Skriv målet direkte ind i enheden over Bluetooth LE. Målrettet (én enhed ad gangen),
kræver ingen router-ændringer.

### BLE-grænseflade

| Element | Værdi |
|------|-------|
| Advertising-navn | `VMC_<MAC>` — MAC'en er enhedens serienummer (12 hex-tegn) |
| WiFi-service-UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| WiFi-karakteristik-UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Fremgangsmåde

Skriv disse tre værdier til WiFi-karakteristikken (`0xC302`), **i denne rækkefølge**:

1. `H_‹BRIDGE_IP›:11000`   — mål host:port (erstatter fabriksindstillingen `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi-SSID
3. `P_‹wifi-pw›`            — WiFi-adgangskode

Enheden tilslutter sig derefter WiFi'et og forbinder til `H_` på port 11000.

> **Forventet særhed:** hver skrivning kan returnere en *"invalid length"-fejl
> (kode 13 / `0x0D`)*. Det er normalt — værdien anvendes alligevel. Ignorér den.

### Manuelt (bluetoothctl)

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

### Hjælpescript (bleak)

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

## 4. Tilbage til cloud'en

- **Metode A:** fjern DNAT-/port-forward-reglen (og DNS-tilsidesættelsen, hvis brugt).
- **Metode B:** genprovisionér enheden med Ambientika-appen, eller skriv fabriksmålet
  `H_app.ambientika.eu:11000` tilbage over BLE.

---

## 5. Sikkerhed

- Test på **én** fysisk enhed, før du ruller dette ud til en hel installation.
- Forbindelsen er **ren TCP (ingen TLS)** — hold bridgen og enhederne på et
  **betroet LAN**; eksponér ikke port 11000 mod internettet.
- Enhedens indbyggede funktioner (inklusive fugt-/dugpunktsbeskyttelse i firmwaren)
  fungerer fortsat uafhængigt af bridgen; omdirigeringen ændrer kun, hvor
  status/kommandoer *på app-niveau* går hen.
- Rul tilbage med trinene i afsnit 4, hvis noget ikke opfører sig korrekt.
