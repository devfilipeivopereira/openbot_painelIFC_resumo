# Guia para o Codex

## Onde está o código

| Repositório | VPS | Como entra em produção |
|---|---|---|
| `vps-snapshot/2026-11-04/mcp/*.py` | `/opt/ifc-painel/mcp/*.py` (host) | Executado direto pelo cron com `/usr/bin/python3` — basta copiar o arquivo |
| `vps-snapshot/2026-11-04/mcp/job-runner-overlay/` | `/opt/ifc-painel/mcp/job-runner-overlay/` | Rebuild da imagem `ifc-mcp` |
| `vps-snapshot/2026-11-04/panel/` | `/opt/ifc-painel/build-panel-20261104-35-ui/` | Rebuild da imagem `ifc-panel` + `docker service update` |
| `vps-snapshot/2026-11-04/cron/`, `logrotate/` | `/etc/cron.d/`, `/etc/logrotate.d/` | Copiar |

O resto do repositório (código original do projeto) pode estar **diferente** do que roda. Trate `vps-snapshot/` como a referência do estado vivo e sempre faça `diff` contra a VPS antes de sobrescrever.

## Implantar

```bash
# 1) Backup SEMPRE, com nome descritivo
cd /opt/ifc-painel/mcp
cp -p daily_ifc_pipeline.py daily_ifc_pipeline.py.bak-<motivo>-$(date +%Y%m%d%H%M)

# 2) Copiar o arquivo novo e compilar
python3 -m py_compile daily_ifc_pipeline.py

# 3) Dry-run (não envia) — ver agendamento.md
IFC_DIGEST_EDITION=tarde IFC_DEFER_PANEL_PUBLICATION=1 python3 daily_ifc_pipeline.py | tail -1
python3 -c "import whatsapp_delivery as w; print(w.execute(send=False, edition='tarde'))"
#    renomear o manifesto gerado para *.dryrun-HHMM.json

# Painel (frontend)
cd /opt/ifc-painel && cp -r build-panel-20261104-35-ui build-panel-<data>-<n>-<nome>
#  editar painel.js/painel.css/index.html; testar sintaxe: node --check painel.js (se houver node)
docker build -t ifc-panel:<data>-<n>-<nome> build-panel-<data>-<n>-<nome>
docker service update --image ifc-panel:<data>-<n>-<nome> ifc-panel
#  rollback: docker service update --image ifc-panel:20261104-35-ui ifc-panel

# job_runner (ifc-mcp): rebuild a partir de job-runner-overlay/Dockerfile e recriar o contêiner
#  preservando o antigo como ifc-mcp-before-<motivo>; depois: docker exec ifc-mcp python3 /app/job_runner.py status
```

Registre cada mudança em `/opt/ifc-painel/README-grokbot-20261103.md` **e** em `docs/vps/changelog-vps.md` (motivo, arquivos, backup, rollback).

## Regras de segurança (obrigatórias)

- Envios **só para o número fixo do Filipe** (arquivo `mcp/secrets/summary-recipient`). Nunca adicionar outros destinatários nem números de líderes.
- **Nunca** imprimir, logar ou commitar segredos (`mcp/secrets/*`, `/run/secrets/*`, chaves da Evolution/LiteLLM/Supabase, tokens codex-auth). Mascarar CPFs. No repositório o destinatário é `<RECIPIENT>`.
- Não mexer no **webhook global da Evolution**.
- Não editar fluxos **n8n** sem pedido explícito do Filipe.
- Não disparar envios reais em testes: use `--check`, `execute(send=False)`, dry-run. `--send`, `--send-fallback`, `--test` só com pedido do Filipe.
- Não apagar ledgers `.sent.json` (isso permite reenvio duplicado) sem OK.
- Backup antes de qualquer alteração; nada de exclusão permanente.
- Usar o modelo mais barato que atenda; o pipeline exige `gpt-6-luna`/`tudo` com reasoning `high` — mudar exige ajustar `curation_model_mismatch` e testar.
- Nunca mencionar CVV/188/linhas de crise nos textos.
- Respeitar janelas: evitar implantar entre 06:10–07:55, 13:10–14:55 e 22:05–23:55.

## Pontos sensíveis do código

- `daily_artifacts.prepare_delivery`: autochecks que levantam `ValueError` derrubam a edição → preferir **degradar** (remover bloco) e marcar `degraded`.
- Validadores de evidência em `daily_ifc_pipeline.py`: qualquer reescrita de texto (estilo) deve manter as palavras exigidas; `natural_direct_sentence()` já tem fallback.
- `text_polish.py` roda no envio — mudanças nele afetam o texto final sem precisar novo preparo.
- `manifest_ready()` / `prep_running()` em `whatsapp_delivery.py` definem quando sai o fallback.
- `panel_humanize.py` precisa preservar mtime do `dados.json` (o healthcheck usa).
