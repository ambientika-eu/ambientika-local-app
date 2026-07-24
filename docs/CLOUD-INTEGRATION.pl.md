🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · **PL** · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Kierowanie urządzeń Ambientika na lokalny Bridge

Ten dokument wyjaśnia, jak sprawić, by urządzenie Ambientika **Smart / Office** komunikowało się z
**lokalnym Bridge** (`ambientika_local_bridge.py`) zamiast z chmurą producenta,
tak aby cały stos działał w sieci LAN bez stałego połączenia z internetem.

Jest to uzupełnienie `README_LOCAL_CLOUDLESS.md`. Przeczytaj uwagi dotyczące bezpieczeństwa na
końcu, zanim dotkniesz urządzenia produkcyjnego.

---

## 1. Jak urządzenie decyduje, gdzie się połączyć

Urządzenia wentylacyjne są **wychodzącymi klientami TCP**. Po provisioningu każde urządzenie
otwiera trwałe połączenie z **ustalonym host:port** i komunikuje się tam natywnym
protokołem binarnym:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Aby działać bez chmury, wystarczy sprawić, by to wychodzące połączenie trafiło zamiast tego na
**host Twojego Bridge**. Istnieją dwa obsługiwane sposoby:

- **Metoda A – Przekierowanie sieciowe (zalecane).** Pozostaw urządzenie tak, jak zostało skonfigurowane,
  i przekieruj cel na routerze/zaporze (lub przez lokalny DNS). Bez Bluetooth.
- **Metoda B – Ponowny provisioning BLE.** Zapisz nowy cel bezpośrednio w urządzeniu
  przez Bluetooth LE.

Wybierz **jedną** na urządzenie.

### Wymagania wstępne (obie metody)

- Lokalny stos jest uruchomiony, a Bridge nasłuchuje na `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- Urządzenie i host Bridge znajdują się w tej samej sieci LAN (lub są dla siebie routowalne).
- Znasz adres LAN IP hosta Bridge (dalej określany jako `‹BRIDGE_IP›`).

---

## 2. Metoda A — Przekierowanie sieciowe (zalecane, bez BLE)

Skonfiguruj urządzenie **raz, w standardowy sposób, za pomocą aplikacji Ambientika** (wymaga to
na chwilę internetu). Następnie przekieruj cel w swojej sieci. Dwa warianty — wybierz
ten, który obsługuje Twoja konfiguracja:

**A1 — Lokalne nadpisanie DNS (najprostsze, jeśli masz kontrolę nad swoim DNS).**
Urządzenie łączy się z nazwą hosta `app.ambientika.eu`. Skieruj tę nazwę na swój
Bridge w lokalnym resolverze (router / Pi-hole / dnsmasq w Home-Assistant / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT na ustalonym adresie IP** (jeśli urządzenie używa adresu IP bezpośrednio lub
wolisz reguły zapory): przekieruj `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Ponieważ protokół to zwykły TCP (bez TLS, bez przypinania certyfikatów), połączenie
urządzenia jest w obu przypadkach przezroczyście akceptowane przez lokalny Bridge.

### Przykłady dla routera / zapory (wariant A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Pole | Wartość |
|-------|-------|
| Interfejs | LAN |
| Protokół | TCP |
| Cel (Destination) | `185.214.203.87` |
| Port docelowy | `11000` |
| Docelowy adres IP przekierowania | `‹BRIDGE_IP›` |
| Docelowy port przekierowania | `11000` |

(Włącz *NAT reflection*, jeśli Bridge i urządzenia współdzielą interfejs LAN.)

**OpenWrt / typowy router z systemem Linux** (`iptables`):

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

### Weryfikacja

Obserwuj, jak Bridge ożywa, gdy urządzenie ponownie się łączy (ponawia próby samodzielnie;
wyłączenie i włączenie zasilania urządzenia to przyspiesza):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

lub, na hoście Bridge: `sudo tcpdump -ni any tcp port 11000`.

### Uwagi i ograniczenia

- Wariant A2 wymaga routera/zapory, które potrafią wykonać DNAT ruchu wychodzącego do sieci WAN. Wiele
  prostych routerów ISP nie potrafi — użyj wtedy A1 (DNS) lub **Metody B**.
- Alternatywa dla A2 oparta wyłącznie na routingu: skieruj `185.214.203.87/32` na host Bridge i
  dodaj alias IP dla `185.214.203.87` na tym hoście, aby akceptował pakiety.

---

## 3. Metoda B — Ponowny provisioning BLE (dla każdego urządzenia)

Zapisz cel bezpośrednio w urządzeniu przez Bluetooth LE. Ukierunkowane (po jednym urządzeniu
naraz), nie wymaga zmian na routerze.

### Interfejs BLE

| Element | Wartość |
|------|-------|
| Nazwa rozgłoszeniowa | `VMC_<MAC>` — MAC to numer seryjny urządzenia (12 znaków hex) |
| UUID usługi WiFi | `0000a002-0000-1000-8000-00805f9b34fb` (16-bitowy `0xA002`) |
| UUID charakterystyki WiFi | `0000c302-0000-1000-8000-00805f9b34fb` (16-bitowy `0xC302`) |

### Procedura

Zapisz te trzy wartości do charakterystyki WiFi (`0xC302`), **w tej kolejności**:

1. `H_‹BRIDGE_IP›:11000`   — docelowy host:port (zastępuje fabryczny `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — SSID sieci WiFi
3. `P_‹wifi-pw›`            — hasło WiFi

Urządzenie następnie dołącza do sieci WiFi i łączy się z `H_` na porcie 11000.

> **Oczekiwana osobliwość:** każdy zapis może zwrócić *błąd "invalid length"
> (kod 13 / `0x0D`)*. To normalne — wartość i tak zostaje zastosowana. Zignoruj go.

### Ręcznie (bluetoothctl)

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

### Skrypt pomocniczy (bleak)

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

## 4. Powrót do chmury

- **Metoda A:** usuń regułę DNAT / przekierowania portu (oraz nadpisanie DNS, jeśli było użyte).
- **Metoda B:** ponownie skonfiguruj urządzenie za pomocą aplikacji Ambientika lub zapisz fabryczny
  cel `H_app.ambientika.eu:11000` z powrotem przez BLE.

---

## 5. Bezpieczeństwo

- Przetestuj na **jednym** fizycznym urządzeniu, zanim wdrożysz to w całej instalacji.
- Połączenie to **zwykły TCP (bez TLS)** — trzymaj Bridge i urządzenia w
  **zaufanej sieci LAN**; nie udostępniaj portu 11000 w internecie.
- Wbudowane funkcje urządzenia (w tym ochrona przed wilgocią / punktem rosy w
  firmware) działają nadal niezależnie od Bridge; przekierowanie zmienia tylko to, dokąd
  trafiają statusy/polecenia *na poziomie aplikacji*.
- Wycofaj zmiany zgodnie z krokami w sekcji 4, jeśli coś działa nieprawidłowo.
