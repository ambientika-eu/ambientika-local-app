🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · **SV** · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Rikta Ambientika-enheter mot den lokala bryggan

Det här dokumentet förklarar hur du får en Ambientika **Smart / Office**-enhet att
kommunicera med den **lokala bryggan** (`ambientika_local_bridge.py`) i stället för
tillverkarens moln, så att hela stacken körs på ditt LAN utan permanent internet.

Det är kompletterande dokument till `README_LOCAL_CLOUDLESS.md`. Läs
säkerhetsanvisningarna i slutet innan du rör en produktionsenhet.

---

## 1. Hur en enhet avgör vart den ansluter

Ventilationsenheterna är **utgående TCP-klienter**. Efter provisionering öppnar varje
enhet en beständig anslutning till en **fast host:port** och talar det inbyggda
binärprotokollet där:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

För att köra molnfritt behöver du bara se till att den utgående anslutningen i stället
hamnar på **din bryggvärd**. Det finns två sätt som stöds:

- **Metod A – Nätverksomdirigering (rekommenderas).** Låt enheten vara provisionerad
  och omdirigera målet i routern/brandväggen (eller via lokal DNS). Ingen Bluetooth.
- **Metod B – BLE-omprovisionering.** Skriv ett nytt mål direkt in i enheten över
  Bluetooth LE.

Välj **en** per enhet.

### Förutsättningar (båda metoderna)

- Den lokala stacken körs och bryggan lyssnar på `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Enheten och bryggvärden är på samma LAN (eller nåbara för varandra).
- Du känner till bryggvärdens LAN-IP (nedan kallad `‹BRIDGE_IP›`).

---

## 2. Metod A — Nätverksomdirigering (rekommenderas, ingen BLE)

Provisionera enheten **en gång, som vanligt, med Ambientika-appen** (detta kräver
internet en kort stund). Omdirigera sedan målet i ditt nätverk. Två varianter — välj
den som din uppsättning stöder:

**A1 — Lokal DNS-override (enklast om din DNS är under din kontroll).**
Enheten ansluter till värdnamnet `app.ambientika.eu`. Peka det namnet mot din brygga
i din lokala resolver (router / Pi-hole / Home-Assistant dnsmasq / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination-NAT på den fasta IP:n** (om enheten använder IP:n direkt, eller om
du föredrar brandväggsregler): omdirigera `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Eftersom protokollet är ren TCP (ingen TLS, ingen certificate pinning) accepteras
enhetens anslutning transparent av din lokala brygga i båda fallen.

### Router-/brandväggsexempel (variant A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Fält | Värde |
|-------|-------|
| Interface | LAN |
| Protokoll | TCP |
| Destination | `185.214.203.87` |
| Destinationsport | `11000` |
| Mål-IP för omdirigering | `‹BRIDGE_IP›` |
| Mål-port för omdirigering | `11000` |

(Aktivera *NAT reflection* om bryggan och enheterna delar LAN-gränssnitt.)

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

### Verifiera

Se bryggan vakna till liv när enheten återansluter (den försöker igen på egen hand; en
strömcykling av enheten påskyndar det):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

eller, på bryggvärden: `sudo tcpdump -ni any tcp port 11000`.

### Anmärkningar och begränsningar

- Variant A2 kräver en router/brandvägg som kan DNAT:a utgående WAN-trafik. Många
  enkla ISP-routrar kan inte det — använd A1 (DNS) eller **Metod B** i stället.
- Rent routningsbaserat alternativ till A2: routa `185.214.203.87/32` till bryggvärden
  och lägg till ett IP-alias för `185.214.203.87` på den värden så att den accepterar paketen.

---

## 3. Metod B — BLE-omprovisionering (per enhet)

Skriv målet direkt in i enheten över Bluetooth LE. Riktat (en enhet i taget), kräver
inga routerändringar.

### BLE-gränssnitt

| Objekt | Värde |
|------|-------|
| Advertising-namn | `VMC_<MAC>` — MAC:en är enhetens serienummer (12 hex-tecken) |
| WiFi-tjänst-UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16-bitars `0xA002`) |
| WiFi-karakteristik-UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16-bitars `0xC302`) |

### Procedur

Skriv dessa tre värden till WiFi-karakteristiken (`0xC302`), **i denna ordning**:

1. `H_‹BRIDGE_IP›:11000`   — mål host:port (ersätter fabriks-`H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi-SSID
3. `P_‹wifi-pw›`            — WiFi-lösenord

Enheten ansluter sedan till WiFi:t och kopplar upp mot `H_` på port 11000.

> **Förväntad egenhet:** varje skrivning kan returnera ett *"invalid length"-fel
> (kod 13 / `0x0D`)*. Detta är normalt — värdet tillämpas ändå. Ignorera det.

### Manuellt (bluetoothctl)

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

### Hjälpskript (bleak)

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

## 4. Återgå till molnet

- **Metod A:** ta bort DNAT-/port-forward-regeln (och DNS-override, om den används).
- **Metod B:** omprovisionera enheten med Ambientika-appen, eller skriv tillbaka
  fabriksmålet `H_app.ambientika.eu:11000` över BLE.

---

## 5. Säkerhet

- Testa på **en** fysisk enhet innan du rullar ut detta till en hel installation.
- Länken är **ren TCP (ingen TLS)** — håll bryggan och enheterna på ett
  **betrott LAN**; exponera inte port 11000 mot internet.
- Enhetens inbyggda funktioner (inklusive fukt-/daggpunktsskydd i firmware) fortsätter
  att fungera oavsett bryggan; omdirigeringen ändrar bara vart status/kommandon
  på *app-nivå* skickas.
- Återställ med stegen i avsnitt 4 om något krånglar.
