# Arquitetura — Painel IFC e Resumos

## Visão geral do fluxo

```
 cron (* * * * *) digest_schedule.py
        │  (06:15 / 13:15 / 22:10 + retentativas até :55)
        ▼
 daily_ifc_pipeline.py  ── coleta ──►  fontes (grupos/DMs WhatsApp via ifc-direct-db-reader/db-gateway,
        │                               e-mail e Drive da secretaria, Instagram, YouTube, agenda, Todoist,
        │                               financeiro (planilha), visitantes, batismos, livro de oração, n8n)
        │
        ├─ curadoria LLM: docker exec openbot → codex exec --model gpt-6-luna (reasoning high)
        │      ├─ limite de uso → codex-auth switch (troca de conta) e tenta de novo
        │      └─ falha/reprovação → LiteLLM modelo "tudo" (contêiner litellm)
        │
        ├─ validações (evidência, cobertura de DMs, perguntas, agenda da família…)
        │      → desde 04/11 falhas DEGRADAM (removem/fundem itens; campo "degraded") em vez de abortar
        │
        ▼
 daily_artifacts.py  → monta o texto do WhatsApp (estilo n8n + estilo IFC), finanças, agenda,
        │               grava manifesto mcp/delivery/<dia>[.<edição>].json (status prepared_not_sent)
        ▼
 cron (*/5) whatsapp_delivery.py --scheduled   (07:00 / 14:00 / 23:00, janela até :55)
        │   text_polish.py limpa o texto e põe o cabeçalho da edição
        │   POST Evolution  https://evolution.filipeivopereira.com  instância "Pastor Filipe"
        │   destinatário lido de mcp/secrets/summary-recipient (número fixo do Filipe)
        │   ledger mcp/delivery/<dia>[.<edição>].sent.json  (1 envio por edição/dia)
        ▼
 digest_schedule.py (publicação :05+ após o envio)
        │   docker exec ifc-mcp python3 /app/job_runner.py publish  → /opt/ifc-painel/published/dados.json
        ▼
 sync_history.py (cron */5 e após publicar)
        │   1) panel_humanize.py  → reescreve cards "Atenção agora" sem jargão
        │   2) envia snapshot ao histórico (Postgres/Supabase)
        ▼
 ifc-panel (serviço swarm, python:3.12-alpine, server.py + painel.js) lê /data/dados.json → navegador
```

## Componentes / contêineres (04/11/2026)

| Nome | Tipo | Imagem | Papel |
|---|---|---|---|
| `ifc-panel` | serviço swarm | `ifc-panel:20261104-35-ui` | Frontend do painel (server.py + index.html/painel.js/painel.css), porta 8787, auth básica via `/run/secrets/ifc-panel-auth`, lê `IFC_PANEL_JSON=/data/dados.json` |
| `ifc-mcp` | contêiner | `ifc-mcp:20261103-94-grokbot` | MCP/job_runner (`/app/job_runner.py publish|status`), `dashboard_publisher.py` (rootfs somente leitura). Estado em volume `ifc-mcp-state` (`/var/lib/docker/volumes/ifc-mcp-state/_data/data/curation/ifc-curation.json`) |
| `openbot` | contêiner | `ghcr.io/nightly-labs/openbot:0.54.0` | Roda o Codex CLI (usuário 1000, `/data/.local/bin`), `codex-auth` para trocar contas |
| `litellm` | contêiner | `ghcr.io/berriai/litellm:main-stable` | Fallback do modelo (alias `tudo`) |
| `ifc-direct-db-reader` | serviço | `ifc-evolution-direct-reader:20261101-4` | Leitura direta (somente leitura) do banco da Evolution: grupos e DMs |
| `ifc-db-gateway` | serviço | `ifc-db-gateway:20261031-9` | Gateway de banco |
| `ifc-transcription-gateway` | contêiner | `ifc-transcription-gateway:20261031-3` | Transcrição de áudios |
| `ifc-openbot-webhook-relay` | serviço | `python:3.12-alpine` | Relay de webhook do openbot |
| `openbot-whatsapp-bridge` | contêiner | `openbot-whatsapp-bridge:1.0.0` | Ponte WhatsApp do openbot |

Imagens anteriores mantidas para rollback: `ifc-panel:20261103-32`, `ifc-panel:20261103-33-grokbot`, `ifc-panel:20261104-34-dedupe`; contêineres antigos `ifc-mcp-before-job-runner-*`.

## Caminhos na VPS

| Caminho | O quê |
|---|---|
| `/opt/ifc-painel/mcp/daily_ifc_pipeline.py` | Pipeline principal (coleta, prompt, curadoria, validação/degradação). ~162 KB |
| `/opt/ifc-painel/mcp/daily_artifacts.py` | Monta o resumo/manifesto, bloco de finanças, agenda, autocheck |
| `/opt/ifc-painel/mcp/whatsapp_delivery.py` | Envio agendado/manual, fallback, ledger |
| `/opt/ifc-painel/mcp/text_polish.py` | Limpeza final do texto e cabeçalho por edição |
| `/opt/ifc-painel/mcp/digest_schedule.py` | Agendador (preparo + publicação), executado todo minuto |
| `/opt/ifc-painel/mcp/healthcheck.py` | Checagem de saúde 3x/dia |
| `/opt/ifc-painel/mcp/panel_humanize.py` | Humaniza cards do painel (chamado por sync_history) |
| `/opt/ifc-painel/mcp/sync_history.py` | Histórico do painel + chama panel_humanize |
| `/opt/ifc-painel/mcp/job-runner-overlay/` | `job_runner.py` + Dockerfile usados para gerar `ifc-mcp:20261103-94-grokbot` |
| `/opt/ifc-painel/mcp/ifc-curation-schema.json` | Schema JSON da curadoria |
| `/opt/ifc-painel/mcp/digest-runs/` | Estado de cada preparo/publicação (`<dia>.<edição>.json`, `schedule.lock`, `*.attemptN.stderr.txt`) |
| `/opt/ifc-painel/mcp/delivery/` | Manifestos e ledgers de envio |
| `/opt/ifc-painel/mcp/health/` | Estado do healthcheck (alerta 1x por problema/dia) |
| `/opt/ifc-painel/mcp/secrets/` | `summary-recipient`, `evolution-global.h` (**nunca copiar/commitar**) |
| `/opt/ifc-painel/published/dados.json` | JSON publicado que o painel lê |
| `/opt/ifc-painel/build-panel-20261104-35-ui/` | Fonte da imagem atual do painel |
| `/opt/ifc-painel/README-grokbot-20261103.md` | Log de mudanças na própria VPS |
| `/opt/ifc-painel/PATCHES-*.diff` | Diffs acumulados |
| `/run/lock/ifc-daily-panel.lock` | Lock do preparo (usado pelo fallback para saber se ainda está rodando) |
| `/var/log/ifc-*.log` | Logs (rotacionados semanalmente) |

## Modelos de IA

- **Primário:** `gpt-6-luna`, `model_reasoning_effort=high`, chamado via `docker exec -i -u 1000 openbot codex exec --model gpt-6-luna --config model_reasoning_effort=high` com o schema `ifc-curation-schema.json` copiado para o contêiner.
- **Troca de conta:** ao bater limite de uso do ChatGPT, `run_codex` roda `codex-auth list/switch` e tenta de novo uma vez por conta.
- **Fallback:** `generate_litellm()` → LiteLLM modelo `tudo` (`reasoning_effort: high`, `temperature 0.2`, `response_format json_object`).
- A validação aceita apenas `model ∈ {gpt-6-luna, tudo, freeai/tudo}` e `reasoning == high` (`curation_model_mismatch` caso contrário).
- Regra do Filipe: usar sempre o modelo mais barato que atenda. Não troque o modelo sem testar com dry-run.

## Estrutura do resumo (WhatsApp)

Cabeçalho por hora do envio (text_polish): `☀️ *Resumo da manhã*` (<12h), `🌤️ *Resumo da tarde*` (<20h), `🌙 *Resumo do dia*`. Seções no estilo n8n (destaques, ⚠️ Para você com DMs, pendências/prazos, compromissos, oração, Todoist urgente, finanças simplificadas "entradas − saídas", agenda 7 dias determinística, link do painel). Limite ~2400–2800 caracteres (`fit_whatsapp_limit` corta destaques de baixo para cima, nunca DMs/Todoist). Escrita no "estilo IFC" desde 04/11 (frases naturais, "você" para o Filipe, tom pastoral).
