🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · [FR](CLOUD-INTEGRATION.fr.md) · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · **PT** · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Direcionar dispositivos Ambientika para a bridge local

Este documento explica como fazer com que um dispositivo Ambientika **Smart / Office**
comunique com a **bridge local** (`ambientika_local_bridge.py`) em vez da cloud do
fabricante, para que todo o stack seja executado na sua LAN sem internet permanente.

É o complemento de `README_LOCAL_CLOUDLESS.md`. Leia as notas de segurança no final
antes de mexer num dispositivo em produção.

---

## 1. Como um dispositivo decide onde se ligar

Os dispositivos de ventilação são **clientes TCP de saída**. Após o aprovisionamento,
cada dispositivo abre uma ligação persistente a um **host:porta fixo** e fala aí o
protocolo binário nativo:

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Para funcionar sem cloud, basta fazer com que essa ligação de saída chegue ao **seu
host da bridge**. Existem duas formas suportadas:

- **Método A – Redirecionamento de rede (recomendado).** Deixe o dispositivo tal como
  foi aprovisionado e redirecione o destino no router/firewall (ou através de DNS
  local). Sem Bluetooth.
- **Método B – Reaprovisionamento BLE.** Escreva um novo destino diretamente no
  dispositivo através de Bluetooth LE.

Escolha **um** por dispositivo.

### Pré-requisitos (ambos os métodos)

- O stack local está em execução e a bridge está à escuta em `‹bridge-host›:11000`:
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- O dispositivo e o host da bridge estão na mesma LAN (ou são encaminháveis entre si).
- Conhece o IP LAN do host da bridge (referido abaixo como `‹BRIDGE_IP›`).

---

## 2. Método A — Redirecionamento de rede (recomendado, sem BLE)

Aprovisione o dispositivo **uma vez, normalmente, com a app Ambientika** (isto requer
internet por breves instantes). Depois, redirecione o destino na sua rede. Duas
variantes — escolha a que a sua configuração suportar:

**A1 — Substituição de DNS local (a mais simples se o seu DNS estiver sob o seu
controlo).** O dispositivo liga-se ao nome de host `app.ambientika.eu`. Aponte esse
nome para a sua bridge no seu resolvedor local (router / Pi-hole / dnsmasq do Home
Assistant / AdGuard):

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — NAT de destino no IP fixo** (se o dispositivo usar o IP diretamente, ou se
preferir regras de firewall): redirecione `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Como o protocolo é TCP simples (sem TLS, sem certificate pinning), a ligação do
dispositivo é aceite de forma transparente pela sua bridge local em qualquer dos casos.

### Exemplos de router / firewall (variante A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward*:

| Campo | Valor |
|-------|-------|
| Interface | LAN |
| Protocolo | TCP |
| Destino | `185.214.203.87` |
| Porta de destino | `11000` |
| IP de destino do redirecionamento | `‹BRIDGE_IP›` |
| Porta de destino do redirecionamento | `11000` |

(Ative a *NAT reflection* se a bridge e os dispositivos partilharem a interface LAN.)

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

Observe a bridge ganhar vida à medida que o dispositivo se reconecta (tenta novamente
por si só; desligar e ligar o dispositivo acelera o processo):

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

ou, no host da bridge: `sudo tcpdump -ni any tcp port 11000`.

### Notas e limitações

- A variante A2 necessita de um router/firewall capaz de fazer DNAT ao tráfego de saída
  para a WAN. Muitos routers simples de operador não o conseguem — use antes A1 (DNS)
  ou o **Método B**.
- Alternativa de encaminhamento puro à A2: encaminhe `185.214.203.87/32` para o host da
  bridge e adicione um alias de IP para `185.214.203.87` nesse host, para que aceite os
  pacotes.

---

## 3. Método B — Reaprovisionamento BLE (por dispositivo)

Escreva o destino diretamente no dispositivo através de Bluetooth LE. Direcionado (um
dispositivo de cada vez), não requer alterações no router.

### Interface BLE

| Item | Valor |
|------|-------|
| Nome de advertising | `VMC_<MAC>` — o MAC é o número de série do dispositivo (12 caracteres hex) |
| UUID de serviço WiFi | `0000a002-0000-1000-8000-00805f9b34fb` (16 bits `0xA002`) |
| UUID da característica WiFi | `0000c302-0000-1000-8000-00805f9b34fb` (16 bits `0xC302`) |

### Procedimento

Escreva estes três valores na característica WiFi (`0xC302`), **por esta ordem**:

1. `H_‹BRIDGE_IP›:11000`   — host:porta de destino (substitui o de fábrica `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — SSID do WiFi
3. `P_‹wifi-pw›`            — palavra-passe do WiFi

O dispositivo junta-se então ao WiFi e liga-se a `H_` na porta 11000.

> **Peculiaridade esperada:** cada escrita pode devolver um *erro de "comprimento
> inválido" (código 13 / `0x0D`)*. Isto é normal — o valor é aplicado à mesma. Ignore-o.

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

## 4. Reverter para a cloud

- **Método A:** remova a regra de DNAT / port-forward (e a substituição de DNS, se
  utilizada).
- **Método B:** reaprovisione o dispositivo com a app Ambientika, ou volte a escrever o
  destino de fábrica `H_app.ambientika.eu:11000` através de BLE.

---

## 5. Segurança

- Teste num **único** dispositivo físico antes de implementar isto numa instalação
  completa.
- A ligação é **TCP simples (sem TLS)** — mantenha a bridge e os dispositivos numa
  **LAN de confiança**; não exponha a porta 11000 à internet.
- As funções integradas do dispositivo (incluindo a proteção contra humidade / ponto de
  orvalho no firmware) continuam a funcionar independentemente da bridge; o
  redirecionamento apenas altera para onde vão o estado/os comandos *ao nível da app*.
- Reverta com os passos da secção 4 se algo se comportar mal.
