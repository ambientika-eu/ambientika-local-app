🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · **ES** · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Apuntar los dispositivos Ambientika al bridge local

Este documento explica cómo hacer que un dispositivo Ambientika **Smart / Office** se comunique con
el **bridge local** (`ambientika_local_bridge.py`) en lugar de con la nube del
fabricante, de modo que todo el stack se ejecute en tu LAN sin internet permanente.

Es el complemento de `README_LOCAL_CLOUDLESS.md`. Lee las notas de seguridad del
final antes de tocar un dispositivo en producción.

---

## 1. Cómo decide un dispositivo a dónde conectarse

Los dispositivos de ventilación son **clientes TCP salientes**. Tras el aprovisionamiento, cada dispositivo
abre una conexión persistente a un **host:puerto fijo** y habla allí el protocolo
binario nativo:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Para funcionar sin nube, solo tienes que hacer que esa conexión saliente llegue a **tu
host del bridge** en su lugar. Hay dos maneras admitidas:

- **Método A – Redirección de red (recomendado).** Deja el dispositivo tal como está aprovisionado
  y redirige el destino en el router/firewall (o mediante DNS local). Sin Bluetooth.
- **Método B – Reaprovisionamiento por BLE.** Escribe un nuevo destino directamente en el dispositivo
  por Bluetooth LE.

Elige **uno** por dispositivo.

### Requisitos previos (ambos métodos)

- El stack local está en ejecución y el bridge escucha en `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- El dispositivo y el host del bridge están en la misma LAN (o son enrutables entre sí).
- Conoces la IP LAN del host del bridge (a la que nos referimos más abajo como `‹BRIDGE_IP›`).

---

## 2. Método A — Redirección de red (recomendado, sin BLE)

Aprovisiona el dispositivo **una vez, de forma normal, con la aplicación Ambientika** (esto necesita
internet brevemente). Después, redirige el destino en tu red. Dos variantes: elige
la que admita tu configuración:

**A1 — Anulación de DNS local (lo más sencillo si controlas tu DNS).**
El dispositivo se conecta al nombre de host `app.ambientika.eu`. Apunta ese nombre a tu
bridge en tu resolutor local (router / Pi-hole / dnsmasq de Home-Assistant / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — NAT de destino en la IP fija** (si el dispositivo usa la IP directamente, o
prefieres reglas de firewall): redirige `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Como el protocolo es TCP sin cifrar (sin TLS, sin fijación de certificados), la
conexión del dispositivo es aceptada de forma transparente por tu bridge local en ambos casos.

### Ejemplos de router / firewall (variante A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Campo | Valor |
|-------|-------|
| Interfaz | LAN |
| Protocolo | TCP |
| Destino | `185.214.203.87` |
| Puerto de destino | `11000` |
| IP de destino de la redirección | `‹BRIDGE_IP›` |
| Puerto de destino de la redirección | `11000` |

(Activa *NAT reflection* si el bridge y los dispositivos comparten la interfaz LAN.)

**OpenWrt / router Linux genérico** (`iptables`):

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

### Verificar

Observa cómo el bridge cobra vida a medida que el dispositivo se reconecta (lo reintenta por sí solo;
apagar y encender el dispositivo lo acelera):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

o, en el host del bridge: `sudo tcpdump -ni any tcp port 11000`.

### Notas y límites

- La variante A2 necesita un router/firewall capaz de aplicar DNAT al tráfico saliente hacia la WAN. Muchos
  routers sencillos de operador no pueden; usa en su lugar A1 (DNS) o el **Método B**.
- Alternativa de enrutamiento puro a A2: enruta `185.214.203.87/32` al host del bridge y
  añade un alias de IP para `185.214.203.87` en ese host para que acepte los paquetes.

---

## 3. Método B — Reaprovisionamiento por BLE (por dispositivo)

Escribe el destino directamente en el dispositivo por Bluetooth LE. Dirigido (un dispositivo a
la vez), no requiere cambios en el router.

### Interfaz BLE

| Elemento | Valor |
|------|-------|
| Nombre de anuncio | `VMC_<MAC>` — la MAC es el número de serie del dispositivo (12 caracteres hex) |
| UUID de servicio WiFi | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| UUID de característica WiFi | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Procedimiento

Escribe estos tres valores en la característica WiFi (`0xC302`), **en este orden**:

1. `H_‹BRIDGE_IP›:11000`   — host:puerto de destino (reemplaza el de fábrica `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — SSID de la WiFi
3. `P_‹wifi-pw›`            — contraseña de la WiFi

El dispositivo se une entonces a la WiFi y se conecta a `H_` en el puerto 11000.

> **Peculiaridad esperada:** cada escritura puede devolver un *error de "longitud no válida"
> (código 13 / `0x0D`)*. Es normal — el valor se aplica igualmente. Ignóralo.

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

### Script auxiliar (bleak)

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

## 4. Volver a la nube

- **Método A:** elimina la regla de DNAT / reenvío de puertos (y la anulación de DNS, si se usó).
- **Método B:** reaprovisiona el dispositivo con la aplicación Ambientika, o vuelve a escribir el
  destino de fábrica `H_app.ambientika.eu:11000` por BLE.

---

## 5. Seguridad

- Pruébalo en **un** dispositivo físico antes de implementarlo en toda una instalación.
- El enlace es **TCP sin cifrar (sin TLS)** — mantén el bridge y los dispositivos en una
  **LAN de confianza**; no expongas el puerto 11000 a internet.
- Las funciones integradas del dispositivo (incluida la protección de humedad / punto de rocío en el
  firmware) siguen funcionando independientemente del bridge; la redirección solo cambia a dónde
  van el estado y los comandos *a nivel de aplicación*.
- Revierte con los pasos de la sección 4 si algo no funciona correctamente.
