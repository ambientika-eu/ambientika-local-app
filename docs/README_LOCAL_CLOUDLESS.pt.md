🌐 [DE](README_LOCAL_CLOUDLESS.de.md) · [EN](../README_LOCAL_CLOUDLESS.md) · [IT](README_LOCAL_CLOUDLESS.it.md) · [FR](README_LOCAL_CLOUDLESS.fr.md) · [ES](README_LOCAL_CLOUDLESS.es.md) · [NL](README_LOCAL_CLOUDLESS.nl.md) · [PL](README_LOCAL_CLOUDLESS.pl.md) · **PT** · [SV](README_LOCAL_CLOUDLESS.sv.md) · [DA](README_LOCAL_CLOUDLESS.da.md) · [CS](README_LOCAL_CLOUDLESS.cs.md)

# Ambientika Local App – modo de funcionamento local sem servidor

> **Enquadramento.** O modo de funcionamento recomendado da Local App é a bridge na
> cloud (`docker-compose.yml`): comprovada no terreno e prevista para a exploração
> corrente. O modo local aqui descrito foi concebido como **nível de recurso**. Mantém
> a instalação operável quando o servidor Ambientika não está acessível — durante
> janelas de manutenção, falhas de rede ou em edifícios para os quais não está prevista
> uma ligação permanente à internet. Não substitui o modo padrão.

Neste modo, a Ambientika Local App (FastAPI + PWA) opera a instalação **sem servidor
Ambientika e sem ligação à internet**. Face ao modo padrão altera-se apenas a ligação
aos equipamentos: em vez da bridge que consulta o servidor entra uma **bridge local**
que comunica com as unidades de ventilação diretamente na rede doméstica através do
seu protocolo TCP nativo (porta 11000).

```
Modo padrão:  Unidade → servidor Ambientika → bridge cloud → MQTT → app
Modo local:   Unidade → bridge local (TCP 11000) → MQTT → app      ← sem servidor
```

Mantém-se a totalidade das funcionalidades:

- monitorização e comando dos equipamentos (modo, ventilador, sensores, ponto de orvalho)
- **execução do programa semanal**
- **NeuraCell-X**: proteção contra rádon (prioritária) e **controlo do ponto de
  orvalho**, com reposição exata do modo anteriormente ativo

O backend da Local App e a PWA são utilizados **sem alterações** — a bridge local
publica os mesmos tópicos e o mesmo vocabulário de campos do modo padrão (nomes de
modo `SMART/HRV/NIGHT/ECO/BOOST/OFF`, `fanSpeed` 0–100 %, `airQuality` int,
`filterAlarm` bool, mais `dewPoint`).

## Componentes

É substituída apenas a ligação aos equipamentos; a app e a lógica de comando mantêm-se.

```
docker-compose.local.yml          # stack sem consulta ao servidor
Dockerfile.bridge                 # imagem da bridge local
ambientika_local_bridge.py        # bridge local (TCP ↔ MQTT)
mosquitto/config/mosquitto.conf   # configuração do broker local
env.local.example.txt             # modelo de configuração (sem credenciais de servidor)
```

## Executar

```bash
docker compose -f docker-compose.local.yml up -d --build
# PWA:  http://<host>:8080
```

## Direcionar os dispositivos para este host (obrigatório, uma só vez)

Os dispositivos ligam-se ao host que tiver sido escrito durante o aprovisionamento BLE:

1. **Reaprovisionamento BLE:** escrever `H_<host-ip>:11000`, `S_<ssid>`,
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
| `RADON_THRESHOLD` | `100` | limiar de disparo automático em Bq/m³ |
| `DEWPOINT_ENABLED` / `DEWPOINT_MARGIN` | `true` / `1.0` | ponto de orvalho automático + histerese em °C |
| `RADON_PROTECT_MODE` / `RADON_PROTECT_FAN` | `8` / `0` | INTAKE / LOW |
| `HA_DISCOVERY` | `false` | publicar a descoberta do Home Assistant (não necessária para a app) |

## Garantia da qualidade

- ✅ Codec de transmissão byte a byte de acordo com die Protokollspezifikation (temperatura e RSSI
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

## Parametrização

As correspondências de modo e ventilador, bem como os limiares de proteção contra
rádon e de ponto de orvalho, estão predefinidos com valores seguros para a aplicação e
são adaptáveis por projeto em `ambientika_local_bridge.py` (duas tabelas de
correspondência e os campos `Config`): `BOOST→TIMED_EXPULSION`, `ECO→AUTO`,
`HRV→MANUAL_HEAT_RECOVERY`, os limiares dos níveis de ventilação e `RADON_THRESHOLD` e
`DEWPOINT_MARGIN`. Os valores-limite específicos do edifício — em particular os
limiares de rádon impostos pelas autoridades — devem ser definidos na colocação em
serviço.

## Estado de disponibilização

O modo local é disponibilizado como **entrega controlada (modo de observação)**: a
validação no terreno sobre todo o parque de firmware instalado ainda não está
concluída e o retorno das instalações é continuamente integrado na validação. Para a
exploração corrente, a bridge cloud mantém-se a variante recomendada; o modo local é o
nível de recurso para o caso de o servidor não estar acessível. Agradecemos o retorno
através das issues deste repositório.
