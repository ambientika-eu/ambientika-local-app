🌐 **DE** · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Ambientika-Geräte auf die lokale Bridge ausrichten

Dieses Dokument erklärt, wie Sie ein Ambientika **Smart / Office** Gerät mit der
**lokalen Bridge** (`ambientika_local_bridge.py`) statt mit der Hersteller-Cloud
kommunizieren lassen, sodass der gesamte Stack in Ihrem LAN ohne dauerhafte
Internetverbindung läuft.

Es ist die Ergänzung zu `README_LOCAL_CLOUDLESS.md`. Lesen Sie die
Sicherheitshinweise am Ende, bevor Sie ein Produktivgerät anfassen.

---

## 1. Wie ein Gerät entscheidet, wohin es sich verbindet

Die Lüftungsgeräte sind **ausgehende TCP-Clients**. Nach dem Provisioning öffnet
jedes Gerät eine persistente Verbindung zu einem **festen host:port** und spricht
dort das native Binärprotokoll:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Um cloudfrei zu betreiben, müssen Sie nur dafür sorgen, dass diese ausgehende
Verbindung stattdessen auf **Ihrem Bridge-Host** landet. Es gibt zwei unterstützte
Wege:

- **Methode A – Netzwerk-Umleitung (empfohlen).** Belassen Sie das Gerät wie
  provisioniert und leiten Sie das Ziel am Router/an der Firewall (oder per lokalem
  DNS) um. Kein Bluetooth.
- **Methode B – BLE-Neu-Provisioning.** Schreiben Sie ein neues Ziel direkt über
  Bluetooth LE in das Gerät.

Wählen Sie **eine** pro Gerät.

### Voraussetzungen (beide Methoden)

- Der lokale Stack läuft und die Bridge lauscht auf `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Das Gerät und der Bridge-Host befinden sich im selben LAN (oder sind gegenseitig
  routbar).
- Sie kennen die LAN-IP des Bridge-Hosts (im Folgenden als `‹BRIDGE_IP›` bezeichnet).

---

## 2. Methode A — Netzwerk-Umleitung (empfohlen, ohne BLE)

Provisionieren Sie das Gerät **einmal, normal, mit der Ambientika-App** (dies
benötigt kurzzeitig Internet). Leiten Sie dann das Ziel in Ihrem Netzwerk um. Zwei
Varianten — wählen Sie diejenige, die Ihr Setup unterstützt:

**A1 — Lokale DNS-Übersteuerung (am einfachsten, wenn Ihr DNS unter Ihrer Kontrolle
steht).**
Das Gerät verbindet sich mit dem Hostnamen `app.ambientika.eu`. Zeigen Sie diesen
Namen in Ihrem lokalen Resolver (Router / Pi-hole / Home-Assistant dnsmasq / AdGuard)
auf Ihre Bridge:

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT auf der festen IP** (falls das Gerät die IP direkt verwendet
oder Sie Firewall-Regeln bevorzugen): leiten Sie `185.214.203.87:11000 →
‹BRIDGE_IP›:11000` um.

Da das Protokoll reines TCP ist (kein TLS, kein Certificate-Pinning), wird die
Verbindung des Geräts in beiden Fällen transparent von Ihrer lokalen Bridge
angenommen.

### Router-/Firewall-Beispiele (Variante A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Feld | Wert |
|-------|-------|
| Interface | LAN |
| Protokoll | TCP |
| Ziel | `185.214.203.87` |
| Zielport | `11000` |
| Ziel-IP der Umleitung | `‹BRIDGE_IP›` |
| Zielport der Umleitung | `11000` |

(Aktivieren Sie *NAT reflection*, wenn Bridge und Geräte dieselbe LAN-Schnittstelle teilen.)

**OpenWrt / generischer Linux-Router** (`iptables`):

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

### Überprüfen

Beobachten Sie, wie die Bridge aktiv wird, sobald sich das Gerät neu verbindet (es
versucht es von selbst erneut; ein Aus- und Einschalten des Geräts beschleunigt es):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

oder, auf dem Bridge-Host: `sudo tcpdump -ni any tcp port 11000`.

### Hinweise & Grenzen

- Variante A2 benötigt einen Router/eine Firewall, der/die ausgehenden WAN-Verkehr
  per DNAT umleiten kann. Viele einfache ISP-Router können das nicht — verwenden Sie
  stattdessen A1 (DNS) oder **Methode B**.
- Reine Routing-Alternative zu A2: Routen Sie `185.214.203.87/32` zum Bridge-Host und
  fügen Sie auf diesem Host einen IP-Alias für `185.214.203.87` hinzu, damit er die
  Pakete annimmt.

---

## 3. Methode B — BLE-Neu-Provisioning (pro Gerät)

Schreiben Sie das Ziel direkt über Bluetooth LE in das Gerät. Gezielt (ein Gerät
nach dem anderen), erfordert keine Router-Änderungen.

### BLE-Schnittstelle

| Element | Wert |
|------|-------|
| Advertising-Name | `VMC_<MAC>` — die MAC ist die Seriennummer des Geräts (12 Hex-Zeichen) |
| WiFi-Service-UUID | `0000a002-0000-1000-8000-00805f9b34fb` (16-Bit `0xA002`) |
| WiFi-Charakteristik-UUID | `0000c302-0000-1000-8000-00805f9b34fb` (16-Bit `0xC302`) |

### Vorgehen

Schreiben Sie diese drei Werte in die WiFi-Charakteristik (`0xC302`), **in dieser
Reihenfolge**:

1. `H_‹BRIDGE_IP›:11000`   — Ziel-host:port (ersetzt werkseitig `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — WiFi-SSID
3. `P_‹wifi-pw›`            — WiFi-Passwort

Das Gerät verbindet sich dann mit dem WiFi und stellt eine Verbindung zu `H_` auf
Port 11000 her.

> **Erwartete Eigenheit:** Jeder Schreibvorgang kann einen *"invalid length"-Fehler
> (Code 13 / `0x0D`)* zurückgeben. Das ist normal — der Wert wird trotzdem
> übernommen. Ignorieren Sie ihn.

### Manuell (bluetoothctl)

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

### Hilfsskript (bleak)

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

## 4. Zurück zur Cloud

- **Methode A:** Entfernen Sie die DNAT-/Port-Forward-Regel (und die
  DNS-Übersteuerung, falls verwendet).
- **Methode B:** Provisionieren Sie das Gerät erneut mit der Ambientika-App oder
  schreiben Sie das werkseitige Ziel `H_app.ambientika.eu:11000` über BLE zurück.

---

## 5. Sicherheit

- Testen Sie an **einem** physischen Gerät, bevor Sie dies auf eine gesamte
  Installation ausrollen.
- Die Verbindung ist **reines TCP (kein TLS)** — halten Sie die Bridge und die
  Geräte in einem **vertrauenswürdigen LAN**; geben Sie Port 11000 nicht ins
  Internet frei.
- Die geräteinternen Funktionen (einschließlich Feuchte-/Taupunktschutz in der
  Firmware) arbeiten unabhängig von der Bridge weiter; die Umleitung ändert nur,
  wohin die Status-/Befehle *auf App-Ebene* gehen.
- Rollen Sie mit den Schritten in Abschnitt 4 zurück, falls etwas nicht funktioniert.
