# Operação, healthcheck e pendências

## Runbook rápido

| Sintoma | Onde olhar | Ação |
|---|---|---|
| Resumo não chegou | `mcp/digest-runs/<dia>.<edição>.json`, `*.attemptN.stderr.txt`, `/var/log/ifc-daily-panel.log` | Ver `reason`. Corrigir, rodar dry-run, depois (com OK do Filipe) `whatsapp_delivery.py --send` |
| Chegou só o fallback ⚠️ | idem + `delivery/<dia>[.<ed>].sent.json` | Preparo falhou ou atrasou; o ledger impede novo envio automático. Reenvio manual só com pedido do Filipe (apagar/renomear o `.sent.json` é ação destrutiva → pedir) |
| `FileNotFoundError` no log de envio | manifesto ausente | Desde 04/11 vira fallback imediato; se reaparecer, conferir `manifest_ready()` |
| Painel desatualizado | `published/dados.json` mtime, `digest-runs/<dia>.painel_*-publish.json` | `docker exec ifc-mcp python3 /app/job_runner.py publish && python3 sync_history.py` |
| `publication_blocked` | `curation_not_current` / `preparation_not_finished` | Rodar o preparo; a publicação usa o último preparo `finished` |
| Codex em limite | `primary_model_reason`, `model_route` | Troca automática de conta; fallback LiteLLM `tudo` |
| Painel não abre | `docker service ps ifc-panel`, `docker service logs ifc-panel` | Rollback de imagem (abaixo) |

## Healthcheck (`mcp/healthcheck.py`, 07:55/14:55/23:55)

Alerta (WhatsApp, só ao número fixo do Filipe, 1x por problema por dia; estado em `mcp/health/`):

| Código | Significado |
|---|---|
| `job_runner_status_quebrado` | `docker exec ifc-mcp python3 /app/job_runner.py status` não devolve JSON (regressão de import etc.) |
| `container_parado:<nome>` | `ifc-mcp`, `openbot` ou `litellm` parado |
| `edicao_<ed>:<status>` | Edição não `finished` após o horário (`nao_rodou`, `failed[:motivo]`, `estado_corrompido`) |
| `envio_<ed>:nao_enviado` | Preparo ok mas sem ledger `.sent.json` |
| `envio_<ed>:fallback_sem_resumo_completo` | Só o fallback saiu |
| edição presa >60 min / lock preso | Preparo travado (`started` há muito tempo) |
| `painel_nao_republicado_<ed>` | `dados.json` não mudou desde HH:00 da edição |
| painel >26 h | Publicação parada |
| disco >90 % | Limpar imagens antigas/logs (com cuidado) |

## Rollback de imagens

```bash
docker service update --image ifc-panel:20261104-34-dedupe ifc-panel   # antes do 35-ui
docker service update --image ifc-panel:20261103-33-grokbot ifc-panel  # antes do dedupe
docker service update --image ifc-panel:20261103-32 ifc-panel          # antes do frontend novo
```
`ifc-mcp`: contêineres antigos preservados como `ifc-mcp-before-job-runner-*`. Rollbacks de arquivos: ver [changelog-vps.md](changelog-vps.md).

## Problemas conhecidos / pendências

1. **Reviews autocompletadas** (`reviews_autofilled`): o modelo às vezes não classifica perguntas/pedidos e o código preenche como `informational`. Reduzir via prompt.
2. **Texto cru de resposta nas DMs** ("você respondeu “Oie.blz ☺️”"): limpar/abreviar respostas triviais.
3. **Frases ainda duras** ("um participante confirmou presença"): ajustar estilo/exemplos.
4. **`dashboard_publisher.py` somente leitura dentro da imagem `ifc-mcp`**: humanização do painel é feita depois, por `panel_humanize.py`. Para mudar o publisher é preciso rebuild da imagem (ver guia-codex).
5. **Fluxo n8n `tU1eIgD04wOodwyD`** (resumo antigo de backup): desativar só depois de 2 noites boas, e só com OK do Filipe. Não editar n8n sem pedido.
6. **Rotina "Resumo matinal dos grupos da IFC" do bot IFC**: pausada em 04/11 (evitar duplicado). As outras rotinas do bot IFC seguem.
7. Cobertura "desde a edição anterior" das edições 14h/23h ainda não verificada em produção.
8. Endpoint de dados do painel: confirmar caminho.
9. Comentários dentro de `/etc/cron.d/ifc-daily-panel` e `ifc-whatsapp-summary` estão desatualizados (o Python manda).
10. Antigos: chaves em texto puro em fluxos n8n `gafW` e `jO8`; webhook Pluggy antigo; pool do banco da Evolution.
