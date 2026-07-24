🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · **IT** · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Puntare le unità Ambientika verso il bridge locale

Questo documento spiega come far comunicare un'unità Ambientika **Smart / Office** con il
**bridge locale** (`ambientika_local_bridge.py`) anziché con il cloud del produttore,
in modo che l'intero stack funzioni nella tua LAN senza internet permanente.

È il documento complementare a `README_LOCAL_CLOUDLESS.md`. Leggi le note di sicurezza alla
fine prima di intervenire su un'unità in produzione.

---

## 1. Come un'unità decide dove connettersi

Le unità di ventilazione sono **client TCP in uscita**. Dopo il provisioning, ciascuna unità
apre una connessione persistente verso un **host:porta fisso** e vi comunica tramite il
protocollo binario nativo:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Per funzionare senza cloud devi solo far sì che quella connessione in uscita arrivi invece al **tuo
host bridge**. Esistono due metodi supportati:

- **Metodo A – Reindirizzamento di rete (consigliato).** Lascia l'unità come è stata provisionata
  e reindirizza la destinazione a livello di router/firewall (o tramite DNS locale). Nessun Bluetooth.
- **Metodo B – Ri-provisioning BLE.** Scrivi una nuova destinazione direttamente nell'unità
  tramite Bluetooth LE.

Scegli **uno** per unità.

### Prerequisiti (entrambi i metodi)

- Lo stack locale è in esecuzione e il bridge è in ascolto su `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- L'unità e l'host bridge sono sulla stessa LAN (o instradabili tra loro).
- Conosci l'IP LAN dell'host bridge (indicato di seguito come `‹BRIDGE_IP›`).

---

## 2. Metodo A — Reindirizzamento di rete (consigliato, senza BLE)

Provisiona l'unità **una volta, normalmente, con l'app Ambientika** (questo richiede
internet per un breve momento). Poi reindirizza la destinazione sulla tua rete. Due varianti — scegli
quella supportata dalla tua configurazione:

**A1 — Override DNS locale (il più semplice se il DNS è sotto il tuo controllo).**
L'unità si connette all'hostname `app.ambientika.eu`. Fai puntare quel nome verso il tuo
bridge nel tuo resolver locale (router / Pi-hole / dnsmasq di Home-Assistant / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT sull'IP fisso** (se l'unità usa direttamente l'IP, oppure se
preferisci regole firewall): reindirizza `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Poiché il protocollo è plain TCP (nessun TLS, nessun certificate pinning), in entrambi i casi la
connessione dell'unità viene accettata in modo trasparente dal tuo bridge locale.

### Esempi router / firewall (variante A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Campo | Valore |
|-------|-------|
| Interface | LAN |
| Protocol | TCP |
| Destination | `185.214.203.87` |
| Destination port | `11000` |
| Redirect target IP | `‹BRIDGE_IP›` |
| Redirect target port | `11000` |

(Abilita *NAT reflection* se il bridge e le unità condividono l'interfaccia LAN.)

**OpenWrt / router Linux generico** (`iptables`):

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

### Verifica

Osserva il bridge attivarsi quando l'unità si riconnette (riprova da sola; spegnere e
riaccendere l'unità velocizza il processo):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

oppure, sull'host bridge: `sudo tcpdump -ni any tcp port 11000`.

### Note e limiti

- La variante A2 richiede un router/firewall in grado di applicare DNAT al traffico in uscita verso la WAN. Molti
  semplici router degli ISP non possono farlo — in tal caso usa A1 (DNS) o il **Metodo B**.
- Alternativa di puro routing ad A2: instrada `185.214.203.87/32` verso l'host bridge e
  aggiungi un alias IP per `185.214.203.87` su quell'host affinché accetti i pacchetti.

---

## 3. Metodo B — Ri-provisioning BLE (per unità)

Scrivi la destinazione direttamente nell'unità tramite Bluetooth LE. Mirato (un'unità alla
volta), non richiede modifiche al router.

### Interfaccia BLE

| Voce | Valore |
|------|-------|
| Nome di advertising | `VMC_<MAC>` — il MAC è il numero di serie dell'unità (12 caratteri esadecimali) |
| UUID del servizio WiFi | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| UUID della caratteristica WiFi | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Procedura

Scrivi questi tre valori nella caratteristica WiFi (`0xC302`), **in quest'ordine**:

1. `H_‹BRIDGE_IP›:11000`   — host:porta di destinazione (sostituisce il valore di fabbrica `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — SSID WiFi
3. `P_‹wifi-pw›`            — password WiFi

L'unità si connette quindi al WiFi e si collega a `H_` sulla porta 11000.

> **Comportamento previsto:** ogni scrittura può restituire un *errore "invalid length"
> (codice 13 / `0x0D`)*. È normale — il valore viene comunque applicato. Ignoralo.

### Manuale (bluetoothctl)

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

### Script di supporto (bleak)

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

## 4. Ripristino del cloud

- **Metodo A:** rimuovi la regola DNAT / port-forward (e l'override DNS, se utilizzato).
- **Metodo B:** ri-provisiona l'unità con l'app Ambientika, oppure riscrivi il valore di fabbrica
  `H_app.ambientika.eu:11000` tramite BLE.

---

## 5. Sicurezza

- Effettua i test su **una** singola unità fisica prima di applicarlo a un'intera installazione.
- Il collegamento è **plain TCP (nessun TLS)** — mantieni il bridge e le unità su una
  **LAN affidabile**; non esporre la porta 11000 a internet.
- Le funzioni integrate dell'unità (inclusa la protezione umidità / punto di rugiada nel
  firmware) continuano a funzionare indipendentemente dal bridge; il reindirizzamento cambia solo la
  destinazione di stato/comandi a *livello app*.
- Ripristina con i passaggi della sezione 4 se qualcosa non funziona correttamente.
