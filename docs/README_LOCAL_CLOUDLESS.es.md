🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · **ES** · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · [PT](README_LOCAL_CLOUDLESS.pt.md) · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – Stack de aplicación 100% sin nube

Ejecuta la Ambientika Local App (FastAPI + PWA) **sin la nube de SUEDWIND y sin
internet**. El único cambio respecto al stack original es la fuente de datos: el
MQTT bridge que consulta la nube se sustituye por un **bridge local** que se comunica con los
dispositivos de ventilación directamente mediante su protocolo raw-TCP nativo (puerto 11000).

El bridge ahora cubre todo el conjunto de funciones sin nube:

- supervisión y control de dispositivos (modo, ventilador, sensores, punto de rocío)
- **ejecución del programa semanal** (Wochenzeitplan)
- **NeuraCell-X**: protección contra radón (prioridad) + **control del punto de rocío
  (Taupunktsteuerung)**, con restauración exacta del modo anterior.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

El backend de local-app y la PWA se usan **sin modificar**: el bridge publica los
mismos temas y el mismo vocabulario de campos que la aplicación espera (nombres de modo legibles
`SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality` int,
`filterAlarm` bool, además de `dewPoint`).

## Archivos que añadir a la raíz del repositorio `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Ejecutar

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Apuntar los dispositivos a este host (obligatorio, una sola vez)

Los dispositivos se conectan al host que se haya escrito durante el aprovisionamiento por BLE:

1. **Reaprovisionamiento por BLE (preferido):** escribe `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` en cada dispositivo.
2. **Ruta estática / DNAT:** redirige `185.214.203.87/32` → este host y añade un
   alias de IP para que el host acepte los paquetes destinados a la IP de la nube.

Más detalles en `CLOUD-INTEGRATION.md`.

## Temas MQTT

| Tema | Dir | Significado |
|-------|-----|---------|
| `ambientika/<serial>/status` | out | estado del dispositivo (JSON, vocabulario de la aplicación + `dewPoint`) |
| `ambientika/<serial>/availability` | out | `online` / `offline` |
| `ambientika/<serial>/set` | in | comando `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | in | programa semanal completo (desde la aplicación) |
| `ambientika/<serial>/schedule/<day>/set` | in | franjas de un día |
| `ambientika/neuracell/state` | out | estado en directo de NeuraCell-X (JSON) |
| `ambientika/radon/alarm` | in | `ON`/`OFF` — fuerza / desactiva la protección contra radón |
| `ambientika/radon/value` | in | lectura de radón (Bq/m³) — se activa automáticamente al alcanzar el umbral |
| `ambientika/dewpoint/block` | in | `ON`/`OFF` — fuerza / libera el bloqueo del punto de rocío |
| `ambientika/weather` | in | `{"temperature": t, "humidity": rh}` aire EXTERIOR |

## Programa semanal

Activado por flanco: cuando una franja pasa a estar activa para el día de la semana y la hora actuales, el
bridge aplica su `mode` (+ `fanSpeed`, o mantiene la velocidad actual si la franja
no tiene ninguna) exactamente **una vez**, de modo que no interfiere con un cambio manual dentro de una franja. El
programa se suspende mientras haya una protección de NeuraCell-X activa.

## NeuraCell-X (radón + punto de rocío)

Prioridad: **radón > punto de rocío > normal**. En la primera transición a cualquier
protección, el bridge guarda el modo/ventilador actual de cada dispositivo como referencia; cuando todas
las protecciones se desactivan, realiza una **restauración exacta**.

- **Protección contra radón** — se activa cuando `radon/alarm=ON` o `radon/value ≥
  RADON_THRESHOLD`. Todos los dispositivos → `INTAKE` a `LOW` (ligera sobrepresión de aire fresco).
  Los comandos `/set` normales se suprimen mientras está activa.
- **Control del punto de rocío (Taupunktsteuerung)** — se activa cuando `dewpoint/block=ON`, o
  automáticamente cuando el punto de rocío **exterior** es igual o superior al punto de rocío interior
  (menos `DEWPOINT_MARGIN`), es decir, cuando ventilar añadiría humedad. Todos los dispositivos →
  `OFF`. Necesita datos exteriores en `ambientika/weather`; sin ellos, solo funciona la
  anulación manual. El punto de rocío interior se calcula a partir de la temperatura y la humedad de cada dispositivo
  (fórmula de Magnus).

## Configuración (env)

| Var | Valor predeterminado | Significado |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | prefijo de tema (mantén `ambientika` para que coincida con la aplicación) |
| `LOCAL_TCP_PORT` | `11000` | puerto al que se conectan los dispositivos |
| `SEND_SETUP` | `false` | activa explícitamente la escritura de la topología al conectar; comprueba primero todos los valores siguientes |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | valores de configuración usados solo con `SEND_SETUP=true` |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | ejecutor del programa |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | controlador de radón y punto de rocío |
| `RADON_THRESHOLD` | `100` | umbral de activación automática en Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | punto de rocío automático + histéresis en °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publica el discovery de Home Assistant (no lo necesita la aplicación) |

## Estado de verificación

- ✅ Códec de transmisión byte a byte conforme a `PROTOCOL.md` (temperatura y RSSI decodificadas
  **con signo**).
- ✅ Ida y vuelta del vocabulario de la aplicación (nombres de modo, fanSpeed %, punto de rocío).
- ✅ Programa semanal: la activación por flanco aplica las franjas una vez; sin efecto en caso contrario; horas
  normalizadas a `HH:MM`.
- ✅ NeuraCell-X: prioridad del radón, supresión de comandos, punto de rocío automático y manual
  con **histéresis de ±margen**, y **restauración exacta** del modo previo a la protección
  (referencia tomada del último objetivo normal, no del eco del dispositivo).
- ✅ Concurrencia reforzada: un único bloqueo serializa comando/programa/NeuraCell,
  el estado de protección se confirma **antes** de cualquier escritura de protección, los bucles de dispositivo
  iteran sobre instantáneas, las escrituras se serializan por dispositivo.
- ✅ Robustez: el encuadre TCP se resincroniza tras un byte erróneo; se rechazan las
  cargas útiles `weather` con formato incorrecto; la reconexión conserva el estado del dispositivo; las entradas de radón/tiempo
  más antiguas que `NC_INPUT_TTL` se tratan como desconocidas; last-will de MQTT + apagado limpio.
- ✅ Conjunto de regresión: **40 pruebas unitarias/de integración** (`test_bridge.py`,
  `test_integration.py`, `test_newfindings.py`).
- ✅ Prueba completa de extremo a extremo a través de un **broker MQTT real** con un dispositivo simulado
  (`smoke_test.py`, 13/13): estado + comando + programa + protección/supresión/
  restauración de radón + punto de rocío + resincronización de encuadre + apagado.
- ✅ `docker compose config` válido; no hay credenciales de la nube en ninguna parte del stack.
- ✅ API de callbacks de paho-mqtt 2.x (VERSION2), se mantiene la compatibilidad con 1.x.
- ⛔️ **Aún no probado en hardware real** — binario obtenido por ingeniería inversa + control
  relevante para la seguridad. Valídalo en un dispositivo físico antes de pasar a producción (en
  particular, la decodificación con signo de temperatura/RSSI).

## Aprobación antes de producción `>>> CONTROL <<<` / `>>> MAPPING <<<`

Las asignaciones de modo/ventilador y los umbrales y objetivos de radón/punto de rocío son valores
predeterminados razonables, no certificados. Conviene revisarlos frente a la especificación del producto y ajustarlos en
`ambientika_local_bridge.py` (dos tablas de asignación + los campos de control de `Config`).
`BOOST→TIMED_EXPULSION`, `ECO→AUTO`, `HRV→MANUAL_HEAT_RECOVERY`, los umbrales de % de ventilador→nivel,
`RADON_THRESHOLD` y `DEWPOINT_MARGIN` son los valores que hay que confirmar.
