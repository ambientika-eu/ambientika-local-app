🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · **FR** · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – stack applicative 100% sans cloud

Exécute l'Ambientika Local App (FastAPI + PWA) **sans cloud SUEDWIND ni
Internet**. La seule différence par rapport à la stack d'origine est la source des données : le
bridge MQTT qui interroge le cloud est remplacé par un **bridge local** qui communique
avec les appareils de ventilation directement via leur protocole raw-TCP natif (port 11000).

Le bridge couvre désormais l'ensemble des fonctionnalités sans cloud :

- surveillance et contrôle des appareils (mode, ventilateur, capteurs, point de rosée)
- **exécution du programme hebdomadaire** (Wochenzeitplan)
- **NeuraCell-X** : protection radon (prioritaire) + **contrôle du point de rosée
  (Taupunktsteuerung)**, avec restauration exacte du mode précédent.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

Le backend de la local-app et la PWA sont utilisés **sans modification** — le bridge publie les
mêmes topics et le même vocabulaire de champs que l'application attend (noms de modes conviviaux
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, plus `dewPoint`).

## Fichiers à ajouter à la racine du dépôt `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Exécution

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Diriger les appareils vers cet hôte (obligatoire, une seule fois)

Les appareils se connectent à l'hôte qui a été enregistré lors du provisionnement BLE :

1. **Re-provisionnement BLE (recommandé) :** écrire `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` sur chaque appareil.
2. **Route statique / DNAT :** rediriger `185.214.203.87/32` → cet hôte et ajouter un
   alias IP pour que l'hôte accepte les paquets destinés à l'IP du cloud.

Détails dans `CLOUD-INTEGRATION.md`.

## Topics MQTT

| Topic | Dir | Signification |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | état de l'appareil (JSON, vocabulaire de l'application + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | commande `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | in | programme hebdomadaire complet (depuis l'application) |
| `ambientika/<serial>/schedule/<day>/set` | in | créneaux d'un jour |
| `ambientika/neuracell/state` | out | état en direct de NeuraCell-X (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — forcer / effacer la protection radon |
| `ambientika/radon/value` | in | mesure du radon (Bq/m³) — se déclenche automatiquement au seuil |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — forcer / relâcher le blocage du point de rosée |
| `ambientika/weather` | in | air EXTÉRIEUR `{"temperature": t, "humidity": rh}` |

## Programme hebdomadaire

Déclenché sur front : lorsqu'un créneau devient actif pour le jour/l'heure actuels, le
bridge applique son `mode` (+ `fanSpeed`, ou conserve la vitesse actuelle si le créneau
n'en spécifie pas) exactement **une seule fois**, afin de ne pas contrarier une modification manuelle
effectuée à l'intérieur d'un créneau. Le programme est suspendu tant qu'une protection NeuraCell-X est active.

## NeuraCell-X (radon + point de rosée)

Priorité : **radon > point de rosée > normal**. Lors de la première transition vers une
protection quelconque, le bridge enregistre le mode/ventilateur actuel de chaque appareil comme référence ; lorsque toutes
les protections sont levées, il effectue une **restauration exacte**.

- **Protection radon** — se déclenche lorsque `radon/alarm=ON` ou `radon/value ≥
  RADON_THRESHOLD`. Tous les appareils → `INTAKE` à `LOW` (légère surpression d'air frais).
  Les commandes `/set` normales sont supprimées tant qu'elle est active.
- **Contrôle du point de rosée (Taupunktsteuerung)** — se déclenche lorsque `dewpoint/block=ON`, ou
  automatiquement lorsque le point de rosée **extérieur** est égal ou supérieur au point de rosée intérieur
  (moins `DEWPOINT_MARGIN`), c'est-à-dire lorsque ventiler ajouterait de l'humidité. Tous les appareils →
  `OFF`. Nécessite des données extérieures sur `ambientika/weather` ; sans elles, seul le forçage
  manuel fonctionne. Le point de rosée intérieur est calculé à partir de la température+humidité de chaque appareil
  (formule de Magnus).

## Configuration (env)

| Var | Default | Signification |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | préfixe des topics (conserver `ambientika` pour correspondre à l'application) |
| `LOCAL_TCP_PORT` | `11000` | port auquel les appareils se connectent |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | configuration envoyée à la connexion |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | exécuteur du programme |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | contrôleur radon + point de rosée |
| `RADON_THRESHOLD` | `100` | seuil de déclenchement automatique en Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | point de rosée automatique + hystérésis en °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publie le discovery Home Assistant (non requis par l'application) |

## État de la vérification

- ✅ Codec de trame octet par octet conforme à `PROTOCOL.md` (température et RSSI décodés
  en **signé**).
- ✅ Aller-retour du vocabulaire de l'application (noms de modes, fanSpeed %, point de rosée).
- ✅ Programme hebdomadaire : le déclenchement sur front applique les créneaux une seule fois ; sinon no-op ; heures
  normalisées en `HH:MM`.
- ✅ NeuraCell-X : priorité radon, suppression des commandes, point de rosée automatique + manuel
  avec **hystérésis ±marge**, et **restauration exacte** du mode précédant la protection
  (référence prise sur la dernière cible normale, et non sur l'écho de l'appareil).
- ✅ Concurrence renforcée : un unique verrou sérialise commande/programme/NeuraCell,
  l'état de protection est validé **avant** toute écriture de protection, les boucles sur les appareils
  itèrent sur des instantanés, les écritures sont sérialisées par appareil.
- ✅ Robustesse : le tramage TCP se resynchronise après un octet parasite ; les charges utiles `weather`
  malformées sont rejetées ; la reconnexion préserve l'état de l'appareil ; les entrées radon/weather
  plus anciennes que `NC_INPUT_TTL` sont traitées comme inconnues ; last-will MQTT + arrêt propre.
- ✅ Suite de régression : **40 tests unitaires/d'intégration** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Test de bout en bout complet à travers un **broker MQTT réel** avec un appareil simulé
  (`smoke_test.py`, 13/13) : status + commande + programme + protection radon/suppression/
  restauration + point de rosée + resynchronisation du tramage + arrêt.
- ✅ `docker compose config` valide ; aucune information d'identification cloud nulle part dans la stack.
- ✅ API de callback paho-mqtt 2.x (VERSION2), repli 1.x conservé.
- ⛔️ **Pas encore testé sur du matériel réel** — binaire rétro-conçu + contrôle
  critique pour la sécurité. Valider sur un appareil physique avant la mise en production (en
  particulier le décodage signé de la température/RSSI).

## Validation avant la mise en production `>>> CONTROL <<<` / `>>> MAPPING <<<`

Les correspondances mode/ventilateur et les seuils et cibles radon/point de rosée sont des valeurs
par défaut raisonnables, non certifiées. Faites-les examiner au regard des spécifications produit et ajustez-les dans
`ambientika_local_bridge.py` (deux tables de correspondance + les champs de contrôle `Config`).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, les seuils ventilateur %→niveau,
`RADON_THRESHOLD` et `DEWPOINT_MARGIN` sont les valeurs à confirmer.
