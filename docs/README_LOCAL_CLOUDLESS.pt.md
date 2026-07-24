🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · **PT** · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika – stack de app 100% sem cloud

Executa a Ambientika Local App (FastAPI + PWA) **sem a cloud da SUEDWIND e sem
internet**. A única alteração face ao stack upstream é a fonte de dados: a bridge
MQTT que consulta a cloud é substituída por uma **bridge local** que comunica com
os dispositivos de ventilação diretamente através do respetivo protocolo raw-TCP
nativo (porta 11000).

A bridge cobre agora todo o conjunto de funcionalidades sem cloud:

- monitorização + controlo de dispositivos (modo, ventilador, sensores, ponto de orvalho)
- **execução do programa semanal** (Programa semanal)
- **NeuraCell-X**: proteção contra rádon (prioridade) + **controlo do ponto de orvalho
  (Controlo do ponto de orvalho)**, com restauro exato do modo anterior.

```
BEFORE (upstream):   Device → Ambientika CLOUD → cloud bridge → MQTT → app
AFTER  (this stack): Device → local-bridge (TCP:11000) → MQTT → app     ← no cloud
```

O backend da local-app e a PWA são utilizados **sem modificações** — a bridge
publica os mesmos tópicos e o mesmo vocabulário de campos que a app espera (nomes
amigáveis de modos `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0-100 %, `airQuality`
int, `filterAlarm` bool, além de `dewPoint`).

## Ficheiros a adicionar à raiz do repositório `ambientika-local-app`

```
docker-compose.local.yml          # stack without the cloud poller
Dockerfile.bridge                 # image for the local bridge
ambientika_local_bridge.py        # the local bridge (clean-room, TCP↔MQTT)
mosquitto/config/mosquitto.conf   # local broker config
env.local.example.txt             # local config template (no cloud creds)
```

## Executar

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Direcionar os dispositivos para este host (obrigatório, uma só vez)

Os dispositivos ligam-se ao host que tiver sido escrito durante o aprovisionamento BLE:

1. **Reaprovisionamento BLE (preferível):** escrever `H_<host-ip>:11000`, `S_<ssid>`,
   `P_<wifi-pw>` em cada dispositivo.
2. **Rota estática / DNAT:** redirecionar `185.214.203.87/32` → este host e adicionar
   um alias de IP para que o host aceite pacotes destinados ao IP da cloud.

Detalhes em `CLOUD-INTEGRATION.md`.

## Tópicos MQTT

| Tópico | Dir | Significado |
|-------|-----|---------|
| `ambientika/<serial>/status` | saída | estado do dispositivo (JSON, vocabulário da app + `dewPoint`) |
| `ambientika/<serial>/availability` | saída | `online` / `offline` |
| `ambientika/<serial>/set` | entrada | comando `{mode, fanSpeed, ...}` |
| `ambientika/<serial>/schedule/set` | entrada | programa semanal completo (a partir da app) |
| `ambientika/<serial>/schedule/<day>/set` | entrada | os intervalos de um dia |
| `ambientika/neuracell/state` | saída | estado em direto do NeuraCell-X (JSON) |
| `ambientika/radon/alarm` | entrada | `ON`/`OFF` — forçar / limpar proteção contra rádon |
| `ambientika/radon/value` | entrada | leitura de rádon (Bq/m³) — dispara automaticamente no limiar |
| `ambientika/dewpoint/block` | entrada | `ON`/`OFF` — forçar / libertar bloqueio por ponto de orvalho |
| `ambientika/weather` | entrada | `{"temperature": t, "humidity": rh}` ar EXTERIOR |

## Programa semanal

Acionado por transição (edge-triggered): quando um intervalo se torna ativo para o
dia da semana/hora atuais, a bridge aplica o respetivo `mode` (+ `fanSpeed`, ou
mantém a velocidade atual se o intervalo não tiver nenhuma) exatamente **uma vez**,
para que uma alteração manual dentro de um intervalo não seja contrariada. O programa
é suspenso enquanto estiver ativa uma proteção do NeuraCell-X.

## NeuraCell-X (rádon + ponto de orvalho)

Prioridade: **rádon > ponto de orvalho > normal**. Na primeira transição para
qualquer proteção, a bridge guarda o modo/ventilador atual de cada dispositivo como
referência; quando todas as proteções cessam, executa um **restauro exato**.

- **Proteção contra rádon** — dispara quando `radon/alarm=ON` ou `radon/value ≥
  RADON_THRESHOLD`. Todos os dispositivos → `INTAKE` em `LOW` (ligeira sobrepressão de
  ar fresco). Os comandos `/set` normais são suprimidos enquanto está ativa.
- **Controlo do ponto de orvalho (Controlo do ponto de orvalho)** — dispara quando
  `dewpoint/block=ON`, ou automaticamente quando o ponto de orvalho **exterior** é
  igual ou superior ao ponto de orvalho interior (menos `DEWPOINT_MARGIN`), ou seja,
  quando ventilar acrescentaria humidade. Todos os dispositivos → `OFF`. Necessita de
  dados exteriores em `ambientika/weather`; sem eles, só funciona a sobreposição
  manual. O ponto de orvalho interior é calculado a partir da temperatura+humidade de
  cada dispositivo (fórmula de Magnus).

## Configuração (env)

| Var | Predefinição | Significado |
|-----|---------|---------|
| `MQTT_BROKER` / `MQTT_PORT` | `mqtt` / `1883` | broker |
| `MQTT_PREFIX` | `ambientika` | prefixo do tópico (manter `ambientika` para corresponder à app) |
| `LOCAL_TCP_PORT` | `11000` | porta a que os dispositivos se ligam |
| `HOUSE_ID` / `DEVICE_ROLE` / `DEVICE_ZONE` | `1` / `0` / `0` | configuração enviada ao ligar |
| `SCHEDULER_ENABLED` / `SCHEDULER_TICK` | `true` / `30` | executor do programa |
| `NEURACELL_ENABLED` / `NEURACELL_TICK` | `true` / `60` | controlador de rádon+ponto de orvalho |
| `RADON_THRESHOLD` | `100` | limiar de disparo automático em Bq/m³ `>>> CONTROL <<<` |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | ponto de orvalho automático + histerese em °C `>>> CONTROL <<<` |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW `>>> CONTROL <<<` |
| `HA_DISCOVERY` | `false` | publicar a descoberta do Home Assistant (não necessária para a app) |

## Estado da verificação

- ✅ Codec de transmissão byte a byte de acordo com `PROTOCOL.md` (temperatura e RSSI
  descodificados como **valores com sinal**).
- ✅ Ida e volta do vocabulário da app (nomes de modos, fanSpeed %, ponto de orvalho).
- ✅ Programa semanal: o acionamento por transição aplica os intervalos uma vez; caso
  contrário, não faz nada; horas normalizadas para `HH:MM`.
- ✅ NeuraCell-X: prioridade do rádon, supressão de comandos, ponto de orvalho
  automático + manual com **histerese de ±margem**, e **restauro exato** do modo
  anterior à proteção (referência obtida a partir do último alvo normal, não do eco
  do dispositivo).
- ✅ Concorrência reforçada: um único lock serializa comando/programa/NeuraCell, o
  estado de proteção é confirmado **antes** de qualquer escrita de proteção, os ciclos
  de dispositivo iteram sobre snapshots, as escritas são serializadas por dispositivo.
- ✅ Robustez: o enquadramento (framing) TCP ressincroniza após um byte perdido;
  payloads `weather` malformados são rejeitados; a reconexão preserva o estado do
  dispositivo; entradas de rádon/tempo mais antigas do que `NC_INPUT_TTL` são tratadas
  como desconhecidas; last-will MQTT + encerramento limpo.
- ✅ Conjunto de testes de regressão: **40 testes unitários/de integração**
  (`test_bridge.py`, `test_integration.py`, `test_newfindings.py`).
- ✅ End-to-end completo através de um **broker MQTT real** com um dispositivo simulado
  (`smoke_test.py`, 13/13): status + comando + programa + proteção/supressão/restauro
  de rádon + ponto de orvalho + ressincronização do enquadramento + encerramento.
- ✅ `docker compose config` válido; sem credenciais de cloud em qualquer parte do stack.
- ✅ API de callbacks do paho-mqtt 2.x (VERSION2), com fallback para 1.x mantido.
- ⛔️ **Ainda não testado em hardware real** — binário obtido por engenharia inversa +
  controlo relevante para a segurança. Validar num dispositivo físico antes de produção
  (em particular a descodificação com sinal de temperatura/RSSI).

## Aprovação final antes de produção `>>> CONTROL <<<` / `>>> MAPPING <<<`

Os mapeamentos de modo/ventilador e os limiares e alvos de rádon/ponto de orvalho são
predefinições razoáveis, não certificadas. Devem ser revistos face à especificação do
produto e afinados em `ambientika_local_bridge.py` (duas tabelas de mapeamento + os
campos de controlo `Config`). `BOOST→TIMED_EXPULSION`, `ECO→AUTO`,
`HRV→MANUAL_HEAT_RECOVERY`, os limiares de % do ventilador→nível, `RADON_THRESHOLD` e
`DEWPOINT_MARGIN` são os valores a confirmar.
