🌐 [DE](CLOUD-INTEGRATION.de.md) · [EN](../CLOUD-INTEGRATION.md) · [IT](CLOUD-INTEGRATION.it.md) · **FR** · [ES](CLOUD-INTEGRATION.es.md) · [NL](CLOUD-INTEGRATION.nl.md) · [PL](CLOUD-INTEGRATION.pl.md) · [PT](CLOUD-INTEGRATION.pt.md) · [SV](CLOUD-INTEGRATION.sv.md) · [DA](CLOUD-INTEGRATION.da.md) · [CS](CLOUD-INTEGRATION.cs.md)

# CLOUD-INTEGRATION.md — Diriger les appareils Ambientika vers le bridge local

Ce document explique comment faire communiquer un appareil Ambientika **Smart / Office** avec le
**bridge local** (`ambientika_local_bridge.py`) au lieu du cloud du fabricant,
afin que toute la stack fonctionne sur votre LAN sans Internet permanent.

Il s'agit du document complémentaire de `README_LOCAL_CLOUDLESS.md`. Lisez les notes de sécurité à la
fin avant d'intervenir sur un appareil en production.

---

## 1. Comment un appareil décide où se connecter

Les appareils de ventilation sont des **clients TCP sortants**. Après le provisionnement, chaque appareil
ouvre une connexion persistante vers un **host:port fixe** et y communique via le
protocole binaire natif :

```
factory target:   app.ambientika.eu : 11000   (resolves to 185.214.203.87, plain TCP — no TLS)
```

Pour fonctionner sans cloud, il vous suffit de faire en sorte que cette connexion sortante aboutisse plutôt à votre **hôte
bridge**. Deux méthodes sont prises en charge :

- **Méthode A – Redirection réseau (recommandée).** Laissez l'appareil tel qu'il a été provisionné
  et redirigez la destination au niveau du routeur/pare-feu (ou via le DNS local). Aucun Bluetooth.
- **Méthode B – Re-provisionnement BLE.** Écrivez une nouvelle destination directement dans l'appareil
  via Bluetooth LE.

Choisissez **une** méthode par appareil.

### Prérequis (les deux méthodes)

- La stack locale est en cours d'exécution et le bridge écoute sur `‹bridge-host›:11000` :
  ```bash
  docker compose -f docker-compose.local.yml up -d --build
  ```
- L'appareil et l'hôte bridge sont sur le même LAN (ou routables l'un vers l'autre).
- Vous connaissez l'IP LAN de l'hôte bridge (désignée ci-dessous par `‹BRIDGE_IP›`).

---

## 2. Méthode A — Redirection réseau (recommandée, sans BLE)

Provisionnez l'appareil **une fois, normalement, avec l'application Ambientika** (cela nécessite
brièvement Internet). Redirigez ensuite la destination sur votre réseau. Deux variantes — choisissez
celle que votre configuration prend en charge :

**A1 — Redéfinition DNS locale (la plus simple si votre DNS est sous votre contrôle).**
L'appareil se connecte au nom d'hôte `app.ambientika.eu`. Faites pointer ce nom vers votre
bridge dans votre résolveur local (routeur / Pi-hole / dnsmasq de Home-Assistant / AdGuard) :

```
app.ambientika.eu   →   ‹BRIDGE_IP›
```

**A2 — Destination NAT sur l'IP fixe** (si l'appareil utilise directement l'IP, ou si vous
préférez les règles de pare-feu) : redirigez `185.214.203.87:11000 → ‹BRIDGE_IP›:11000`.

Comme le protocole est du plain TCP (aucun TLS, aucun certificate pinning), dans les deux cas la
connexion de l'appareil est acceptée de manière transparente par votre bridge local.

### Exemples routeur / pare-feu (variante A2)

**OPNsense / pfSense** — *Firewall → NAT → Port Forward* :

| Champ | Valeur |
|-------|-------|
| Interface | LAN |
| Protocol | TCP |
| Destination | `185.214.203.87` |
| Destination port | `11000` |
| Redirect target IP | `‹BRIDGE_IP›` |
| Redirect target port | `11000` |

(Activez *NAT reflection* si le bridge et les appareils partagent l'interface LAN.)

**OpenWrt / routeur Linux générique** (`iptables`) :

```bash
# ensure forwarding is on: sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A PREROUTING -p tcp -d 185.214.203.87 --dport 11000 \
         -j DNAT --to-destination ‹BRIDGE_IP›:11000
```

**MikroTik / RouterOS** :

```
/ip firewall nat add chain=dstnat protocol=tcp \
    dst-address=185.214.203.87 dst-port=11000 \
    action=dst-nat to-addresses=‹BRIDGE_IP› to-ports=11000
```

### Vérification

Observez le bridge s'activer lorsque l'appareil se reconnecte (il réessaie tout seul ; éteindre et
rallumer l'appareil accélère le processus) :

```bash
docker compose -f docker-compose.local.yml logs -f local-bridge
# expect:  device connected from ‹unit-ip› … registered device ‹SERIAL›
# and a retained message on  ambientika/‹SERIAL›/status
```

ou, sur l'hôte bridge : `sudo tcpdump -ni any tcp port 11000`.

### Notes et limites

- La variante A2 nécessite un routeur/pare-feu capable d'appliquer un DNAT au trafic sortant vers le WAN. De nombreux
  routeurs FAI simples ne le peuvent pas — dans ce cas, utilisez A1 (DNS) ou la **Méthode B**.
- Alternative en routage pur à A2 : routez `185.214.203.87/32` vers l'hôte bridge et
  ajoutez un alias IP pour `185.214.203.87` sur cet hôte afin qu'il accepte les paquets.

---

## 3. Méthode B — Re-provisionnement BLE (par appareil)

Écrivez la destination directement dans l'appareil via Bluetooth LE. Ciblé (un appareil à la
fois), ne nécessite aucune modification du routeur.

### Interface BLE

| Élément | Valeur |
|------|-------|
| Nom d'advertising | `VMC_<MAC>` — le MAC est le numéro de série de l'appareil (12 caractères hexadécimaux) |
| UUID du service WiFi | `0000a002-0000-1000-8000-00805f9b34fb` (16-bit `0xA002`) |
| UUID de la caractéristique WiFi | `0000c302-0000-1000-8000-00805f9b34fb` (16-bit `0xC302`) |

### Procédure

Écrivez ces trois valeurs dans la caractéristique WiFi (`0xC302`), **dans cet ordre** :

1. `H_‹BRIDGE_IP›:11000`   — host:port de destination (remplace la valeur d'usine `H_app.ambientika.eu:11000`)
2. `S_‹ssid›`               — SSID WiFi
3. `P_‹wifi-pw›`            — mot de passe WiFi

L'appareil rejoint alors le WiFi et se connecte à `H_` sur le port 11000.

> **Comportement attendu :** chaque écriture peut renvoyer une *erreur "invalid length"
> (code 13 / `0x0D`)*. C'est normal — la valeur est tout de même appliquée. Ignorez-la.

### Manuel (bluetoothctl)

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

### Script de support (bleak)

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

## 4. Retour au cloud

- **Méthode A :** supprimez la règle DNAT / port-forward (et la redéfinition DNS, si utilisée).
- **Méthode B :** re-provisionnez l'appareil avec l'application Ambientika, ou réécrivez la valeur d'usine
  `H_app.ambientika.eu:11000` via BLE.

---

## 5. Sécurité

- Effectuez les tests sur **un** seul appareil physique avant de le déployer sur toute une installation.
- La liaison est en **plain TCP (aucun TLS)** — gardez le bridge et les appareils sur un
  **LAN de confiance** ; n'exposez pas le port 11000 sur Internet.
- Les fonctions intégrées de l'appareil (y compris la protection humidité / point de rosée dans le
  firmware) continuent de fonctionner indépendamment du bridge ; la redirection ne change que la
  destination des status/commandes au *niveau application*.
- Revenez en arrière avec les étapes de la section 4 si quelque chose fonctionne mal.
