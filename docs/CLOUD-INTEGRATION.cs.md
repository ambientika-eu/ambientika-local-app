🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · **CS**

# CLOUD-INTEGRATION.md — Nasměrování jednotek Ambientika na lokální bridge

Tento dokument popisuje, jak přimět jednotku Ambientika **Smart / Office** ke komunikaci s
**lokální bridge** (`ambientika_local_bridge.py`) namísto výrobcova
cloudu, takže celý stack běží ve vaší síti LAN bez trvalého internetu.

Je doplňkem k `README_LOCAL_CLOUDLESS.md`. Než se dotknete produkční jednotky, přečtěte si
bezpečnostní poznámky na konci.

---

## 1. Jak jednotka rozhoduje, kam se připojit

Větrací jednotky jsou **odchozí TCP klienti**. Po provisioningu každá jednotka
otevře trvalé spojení k **pevnému host:port** a komunikuje tam nativním
binárním protokolem:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Pro provoz bez cloudu stačí, aby toto odchozí spojení skončilo místo toho na **vašem
hostiteli s bridge**. Existují dva podporované způsoby:

- **Metoda A – Přesměrování v síti (doporučeno).** Ponechte jednotku tak, jak byla nakonfigurována,
  a přesměrujte cíl na routeru/firewallu (nebo přes lokální DNS). Bez Bluetooth.
- **Metoda B – Opětovný provisioning přes BLE.** Zapište nový cíl přímo do jednotky
  přes Bluetooth LE.

Pro každou jednotku zvolte **jednu** z nich.

### Předpoklady (obě metody)

- Lokální stack běží a bridge naslouchá na `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Jednotka a hostitel s bridge jsou ve stejné síti LAN (nebo jsou vzájemně směrovatelní).
- Znáte LAN IP hostitele s bridge (dále označováno jako `‹BRIDGE_IP›`).

---

## 2. Metoda A — Přesměrování v síti (doporučeno, bez BLE)

Nakonfigurujte jednotku **jednou, běžně, pomocí aplikace Ambientika** (k tomu je krátce
potřeba internet). Poté přesměrujte cíl ve vaší síti. Dvě varianty — zvolte
tu, kterou vaše nastavení podporuje:

**A1 — Lokální přepsání DNS (nejjednodušší, pokud máte DNS pod kontrolou).**
Jednotka se připojuje k hostname `app.ambientika.eu`. Nasměrujte tento název na vaši
bridge ve vašem lokálním resolveru (router / Pi-hole / Home-Assistant dnsmasq / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT na pevné IP** (pokud jednotka používá IP přímo, nebo
dáváte přednost pravidlům firewallu): přesměrujte `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Protože protokol je prostý TCP (bez TLS, bez připínání certifikátů), spojení jednotky
vaše lokální bridge tak jako tak transparentně přijme.

### Příklady router / firewall (varianta A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Pole | Hodnota |
|-------|-------|
| Rozhraní | LAN |
| Protokol | TCP |
| Cíl | `185.214.203.87` |
| Cílový port | `11000` |
| Cílová IP přesměrování | `‹BRIDGE_IP›` |
| Cílový port přesměrování | `11000` |

(Pokud bridge a jednotky sdílejí rozhraní LAN, povolte *NAT reflection*.)

**OpenWrt / obecný Linux router** (`iptables`):

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

### Ověření

Sledujte, jak bridge ožívá, když se jednotka znovu připojí (opakuje pokusy sama;
vypnutí a zapnutí jednotky to urychlí):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

nebo na hostiteli s bridge: `sudo tcpdump -ni any tcp port 11000`.

### Poznámky a omezení

- Varianta A2 vyžaduje router/firewall, který umí provést DNAT provozu směřujícího ven do WAN. Mnoho
  jednoduchých ISP routerů to neumí — použijte místo toho A1 (DNS) nebo **metodu B**.
- Alternativa k A2 založená čistě na směrování: nasměrujte `185.214.203.87/32` na hostitele s bridge a
  na tomto hostiteli přidejte IP alias pro `185.214.203.87`, aby pakety přijímal.

---

## 3. Metoda B — Opětovný provisioning přes BLE (pro každou jednotku)

Zapište cíl přímo do jednotky přes Bluetooth LE. Cílené (vždy jedna jednotka),
nevyžaduje žádné změny na routeru.

### Rozhraní BLE

| Položka | Hodnota |
|------|-------|
| Advertising název | `VMC_<MAC>` — MAC je sériové číslo jednotky (12 hex znaků) |
| WiFi service UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16bitové `0xA002`) |
| WiFi characteristic UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16bitové `0xC302`) |

### Postup

Zapište tyto tři hodnoty do WiFi charakteristiky (`0xC302`), **v tomto pořadí**:

1. `H_‹BRIDGE_IP›:11000`   — cílový host:port (nahrazuje tovární `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi SSID
3. `P_‹wifi-pw›`            — WiFi heslo

Jednotka se poté připojí k WiFi a spojí se s `H_` na portu 11000.

> **Očekávaná zvláštnost:** každý zápis může vrátit *chybu "invalid length"
> (kód 13 / `0x0D`)*. To je normální — hodnota se přesto použije. Ignorujte ji.

### Ručně (bluetoothctl)

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

### Pomocný skript (bleak)

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

## 4. Návrat ke cloudu

- **Metoda A:** odstraňte pravidlo DNAT / port-forward (a přepsání DNS, pokud bylo použito).
- **Metoda B:** znovu nakonfigurujte jednotku pomocí aplikace Ambientika, nebo přes BLE zapište zpět
  tovární cíl `H_app.ambientika.eu:11000`.

---

## 5. Bezpečnost

- Než to nasadíte na celou instalaci, otestujte na **jedné** fyzické jednotce.
- Spojení je **prostý TCP (bez TLS)** — ponechte bridge a jednotky v
  **důvěryhodné síti LAN**; nevystavujte port 11000 do internetu.
- Vestavěné funkce jednotky (včetně ochrany proti vlhkosti / rosnému bodu ve
  firmwaru) fungují dál bez ohledu na bridge; přesměrování mění pouze to, kam
  směřují stav/příkazy na *úrovni aplikace*.
- Pokud se cokoli chová nesprávně, vraťte se zpět podle kroků v části 4.
