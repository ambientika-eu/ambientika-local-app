🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · **FR** · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – mode de fonctionnement local sans serveur

> **Positionnement.** Le mode de fonctionnement recommandé de la Local App est le
> bridge cloud (`docker-compose.yml`) : éprouvé sur le terrain et prévu pour
> l'exploitation courante. Le mode local décrit ici est conçu comme un **niveau de
> repli**. Il maintient l'installation utilisable lorsque le serveur Ambientika est
> injoignable — lors de fenêtres de maintenance, de pannes réseau ou dans des
> bâtiments où une connexion internet permanente n'est pas prévue. Il ne remplace pas
> le mode standard.

Dans ce mode, l'Ambientika Local App (FastAPI + PWA) pilote l'installation **sans
serveur Ambientika et sans connexion internet**. Par rapport au mode standard, seule
la liaison aux appareils change : le bridge qui interroge le serveur est remplacé par
un **bridge local** qui s'adresse aux appareils de ventilation directement sur le
réseau domestique via leur protocole TCP natif (port 11000).

```
Mode standard :  Appareil → serveur Ambientika → bridge cloud → MQTT → app
Mode local :     Appareil → bridge local (TCP 11000) → MQTT → app    ← sans serveur
```

L'ensemble des fonctionnalités reste disponible :

- surveillance et commande des appareils (mode, ventilateur, capteurs, point de rosée)
- **exécution du programme hebdomadaire**
- **NeuraCell-X** : protection radon (prioritaire) et **contrôle du point de rosée**,
  avec restauration exacte du mode précédemment actif

Le backend de la Local App et la PWA sont utilisés **sans modification** — le bridge
local publie les mêmes topics et le même vocabulaire de champs que le mode standard
(noms de modes `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int,
`filterAlarm` bool, plus `dewPoint`).

## Composants

Seule la liaison aux appareils est remplacée ; l'application et la logique de commande
restent identiques.

```
docker-compose.local.yml          # stack sans interrogation du serveur
Dockerfile.bridge                 # image du bridge local
ambientika_local_bridge.py        # bridge local (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # configuration du broker local
env.local.example.txt             # modèle de configuration (sans identifiants serveur)
```

## Exécution

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Diriger les appareils vers cet hôte (obligatoire, une seule fois)

Les appareils se connectent à l'hôte qui a été enregistré lors du provisionnement BLE :

1. **Re-provisionnement BLE :** écrire `H_<host-ip>:11000`, `S_<ssid>`,
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
| `SEND_SETUP` | `false` | active explicitement l'écriture de la topologie à la connexion ; vérifiez d'abord toutes les valeurs ci-dessous |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | valeurs de configuration utilisées uniquement avec `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | exécuteur du programme |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | contrôleur radon + point de rosée |
| `RADON_THRESHOLD` | `100` | seuil de déclenchement automatique en Bq/m³ |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | point de rosée automatique + hystérésis en °C |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publie le discovery Home Assistant (non requis par l'application) |

## Assurance qualité

- ✅ Codec de trame octet par octet conforme à die Protokollspezifikation (température et RSSI décodés
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

## Paramétrage

Les correspondances de modes et de ventilateur ainsi que les seuils de protection
radon et point de rosée sont préréglés avec des valeurs sûres pour l'application et
sont adaptables par projet dans `ambientika_local_bridge.py` (deux tables de
correspondance et les champs `Config`) : `BOOST→TIMED_EXPULSION`, `ECO→AUTO`,
`HRV→MANUAL_HEAT_RECOVERY`, les seuils des niveaux de ventilation ainsi que
`RADON_THRESHOLD` et `DEWPOINT_MARGIN`. Les valeurs limites propres au bâtiment — en
particulier les seuils de radon imposés par les autorités — doivent être définies lors
de la mise en service.

## Statut de publication

Le mode local est fourni sous forme de **livraison contrôlée (mode d'observation)** :
la validation sur le terrain sur l'ensemble du parc de firmwares déployés n'est pas
encore achevée et les retours des installations alimentent en continu la validation.
Pour l'exploitation courante, le bridge cloud reste la variante recommandée ; le mode
local est le niveau de repli au cas où le serveur serait injoignable. Merci de faire
remonter vos retours via les issues de ce dépôt.
