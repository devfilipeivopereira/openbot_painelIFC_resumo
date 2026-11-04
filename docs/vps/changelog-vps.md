# Changelog da VPS — 03/11 a 04/11/2026

Horários em BRT. Backups ficam ao lado do arquivo em `/opt/ifc-painel/mcp/` (`cp -p` preserva o mtime do original, então o mtime do backup é o do arquivo **antes** da mudança). Log original na VPS: `/opt/ifc-painel/README-grokbot-20261103.md`. Patches: [`docs/patches/`](../patches/).

> Backups mais antigos (`*.before-curation-model-*`, `*.before-sender-fields-*`, `*.before-snapshot-gate-*`, 01–03/11) são de trabalho anterior (Remi/Codex) e não estão detalhados aqui.

---

## 03/11 — Correções base (painel não publicava e resumo agendado não saía desde 01/11)

Causa: portões de validação falhando e `job_runner.py` quebrado.

| # | Arquivo | Mudança | Backup |
|---|---|---|---|
| 1 | `daily_ifc_pipeline.py` | Bloco Todoist urgente sempre anexado; `fit_whatsapp_limit` (corta destaques de baixo p/ cima, máx 2400, nunca DMs/Todoist); DMs legíveis, título primeiro, sem cabeçalho duplicado; erro `whatsapp_draft_too_long`; `run_codex` troca conta `codex-auth` no limite; `complete_review_list` preenche perguntas/pedidos não classificados como `informational` (contado em `reviews_autofilled`; >60% faltando ⇒ falha) — regra aprovada pelo Filipe | `daily_ifc_pipeline.py.before-grokbot-digest-fit-20261103T220613.bak` |
| 2 | `digest_schedule.py` | Publicação noturna; publicação usa o último preparo concluído do dia | `digest_schedule.py.before-grokbot-evening-publish-20261103T220647.bak`, `.before-0720-20261103T223246Z.bak` |
| 3 | `whatsapp_delivery.py` | Janelas de envio + ledger (1 envio/edição/dia, só número fixo) | — |
| 4 | `job-runner-overlay/job_runner.py` | `import unicodedata` faltando (publicação quebrava); `REQUEST_CUES` sincronizado com o pipeline ("tem/temos que|de"). Nova imagem `ifc-mcp:20261103-94-grokbot`; contêineres antigos preservados como `ifc-mcp-before-job-runner-*` | `job_runner.py.before-grokbot-import-20261103T231357.bak`, `.before-grokbot-cues-20261103T231528.bak` |

## 03/11 ~23:23 — Três edições por dia (pedido do Filipe: "um às 7h, um às 14h e um às 23h")
- Preparo 06:15 / 13:15 / 22:10 (retentativas até :55); envio 07:00 / 14:00 / 23:00 (janela 45 min); publicação 07:05+/14:05+/23:05+ com o último preparo concluído.
- Backups: `*.before-grokbot-3x-20261103T2323*.bak` (pipeline, digest_schedule, whatsapp_delivery, daily_artifacts).

## 03/11 23:33 — Frontend novo (Codex, 3 etapas)
- Imagem `ifc-panel:20261103-33-grokbot` (anterior `ifc-panel:20261103-32`); arquivos `web/*.before-grokbot-front-20261103.bak`. Fonte: `/opt/ifc-painel/build-panel-grokbot-20261103`.
- Rollback: `docker service update --image ifc-panel:20261103-32 ifc-panel`

## 03/11 ~23:30 — Healthcheck + logrotate
- Novo `mcp/healthcheck.py`, cron `/etc/cron.d/ifc-healthcheck` (07:55, 14:55, 23:55): smoke test do job_runner, contêineres (`ifc-mcp`, `openbot`, `litellm`), edições falhas/não enviadas, lock preso >60 min, painel >26 h, disco >90 %. Alerta 1x por problema/dia, só ao número do Filipe.
- `/etc/logrotate.d/ifc-painel` (semanal, 6 cópias, copytruncate).

## 04/11 00:19–01:50 — Degradação em vez de falha + estilo n8n
- Painel `ifc-panel:20261104-34-dedupe` (dedupe "Precisa de você" e Agenda). Rollback: `docker service update --image ifc-panel:20261103-33-grokbot ifc-panel`.
- Pipeline: validações viram degradação (evidência duplicada/citação não encontrada descartadas; claims sem evidência removidas; reviews autocompletadas/redirecionadas; orações puladas; conversas faltantes com placeholder; agenda incompleta não bloqueia). Contadores em `degraded`.
- Entrega: a partir do minuto 40 envia resumo parcial ou aviso de falha + link (nunca nada). Novo `text_polish.py` (limpa cabeçalhos vazios, dicas de onboarding do Todoist, pendências duplicadas, linha de cobertura).
- Resumo no estilo n8n (prompt/organização) implantado.
- Backups: `*.bak-degrade-202611040019/20` e `*.bak-newstyle-202611040149`.
- Rollback: `cd /opt/ifc-painel/mcp; for f in daily_ifc_pipeline.py daily_artifacts.py whatsapp_delivery.py; do cp -p $f.bak-newstyle-202611040149 $f; done`

## 04/11 01:51 — Ajustes finais
- Finanças: resultado do mês = **entradas − saídas** (antes "saldo" da planilha).
- DMs: encurtamento termina em oração completa e remove palavras soltas no fim; text_polish repete a limpeza.
- Todoist: mais dicas de onboarding excluídas (também da contagem "+N sem prazo").
- Cabeçalho por edição: ☀️ manhã / 🌤️ tarde / 🌙 dia.
- Backups `*.bak-fix6-202611040151`. Rollback: `for f in daily_artifacts.py daily_ifc_pipeline.py text_polish.py; do cp -p $f.bak-fix6-202611040151 $f; done`
- Commit no GitHub 08c6fbc (PR #1).

## 04/11 06:15–08:24 — INCIDENTE: edição matinal não saiu completa

**Linha do tempo**
- 06:15 e 06:35: preparo matinal `failed` com `reason=pipeline_output_not_json` (sem pista: stderr não era salvo).
- 07:00–07:45: `whatsapp_delivery.py --scheduled` dava `FileNotFoundError` a cada 5 min (manifesto `delivery/2026-11-04.json` não existia) e o fallback só era liberado a partir do minuto 40 **se houvesse manifesto** → nada saiu.
- ~07:07: o Filipe recebeu um resumo, mas era da rotina "Resumo matinal dos grupos da IFC" do **bot IFC (Grok Bot)**, não da VPS. Essa rotina foi **pausada** a pedido do Filipe (evitar duplicidade).
- 07:29: reexecução manual parou em `family_schedule_validation_failed:family_evidence_missing` (o modelo citou compromisso de família com evidência que não batia → `family_artifacts.save` levantava `ValueError` → edição inteira abortava).
- 07:50: saiu o **fallback** (só para o número do Filipe), ledger `delivery/2026-11-04.sent.json` = `accepted_by_evolution`.
- ~08:00: **causa raiz** do `pipeline_output_not_json`: `daily_artifacts.prepare_delivery` levantava `ValueError('finance_digest_details_missing')` — o autocheck de finanças ainda exigia o `saldo` da planilha, mas desde 01:51 o bloco mostra entradas − saídas; em out/2026 `saldo ≠ entradas − saídas` ⇒ falha determinística. (Erro introduzido na mudança de 01:51.)
- 08:20: painel republicado com dados do dia.
- 08:24: resumo completo enviado manualmente (pedido do Filipe "Envie o resumo agora então da vps"), só para o número dele.

**Correções**
1. `whatsapp_delivery.py` (bak `whatsapp_delivery.py.bak-fnf-202611040731`): janela até :55; manifesto ausente/inválido/velho (>3 h ou edição errada) sem preparo rodando ⇒ fallback imediato; preparo rodando ⇒ `waiting_preparation` até :50, depois fallback; nova opção `--send-fallback`.
2. `daily_ifc_pipeline.py` (bak `.bak-fnf-202611040732`, `.bak-family-202611040753`): falha da agenda da família degrada (remove itens inválidos; em último caso lista vazia; `degraded.family_validation_degraded:*`); qualquer exceção imprevista imprime JSON `{"status":"failed","reason":"crash_<Tipo>_line<N>"}` + traceback no stderr.
3. `digest_schedule.py` (bak `.bak-fnf-202611040732`): stderr salvo em `digest-runs/<dia>.<edição>.attempt<N>.stderr.txt`; até 3 tentativas.
4. `daily_artifacts.py` (bak `daily_artifacts.py.bak-finance-202611040805`): autocheck de finanças só exige entradas/saídas; se falhar, remove o bloco de finanças em vez de abortar.
5. `healthcheck.py` (bak `healthcheck.py.bak-0824-20261104`): marca edição `failed` (com motivo) após 3 tentativas; edição presa >60 min; `painel_nao_republicado_<edição>`; `envio_<edição>:fallback_sem_resumo_completo`.
6. Dry-run tarde 08:03–08:17: `partial_success`, `prepared_not_sent`, gpt-6-luna, degradações (`family_events_dropped 1`, `prayer_updates_skipped 1`); `execute(send=False, edition='tarde')` → `ready_not_sent`, 2755 chars. Manifesto renomeado `delivery/2026-11-04.tarde.dryrun-0817.json`.
7. Painel republicado 08:20 (`job_runner.py publish` + `sync_history.py`); backup `/root/dados.json.bak-20261104-0822`.

> Observação: como a matinal foi enviada por fora do fluxo, o healthcheck das 14:55 pode alertar sobre a edição da manhã — esperado.

**Rollback**
```bash
cd /opt/ifc-painel/mcp
cp -p whatsapp_delivery.py.bak-fnf-202611040731 whatsapp_delivery.py
cp -p daily_ifc_pipeline.py.bak-fnf-202611040732 daily_ifc_pipeline.py   # desfaz family + crash JSON (e estilo!)
cp -p digest_schedule.py.bak-fnf-202611040732 digest_schedule.py
cp -p daily_artifacts.py.bak-finance-202611040805 daily_artifacts.py
cp -p healthcheck.py.bak-0824-20261104 healthcheck.py
python3 -m py_compile whatsapp_delivery.py daily_ifc_pipeline.py digest_schedule.py daily_artifacts.py healthcheck.py
```

## 04/11 ~08:30 — Escrita no estilo do bot IFC (pedido: "O jeito que vc escreve os resumos enviados pelo IFC é mais agradável… A formatação está legal")
- Formatação/estrutura mantidas; só a escrita mudou.
- `daily_ifc_pipeline.py` (bak `daily_ifc_pipeline.py.bak-style-20261104`): bloco de prompt **"WRITING STYLE"** (frases completas, nome + verbo no passado, "você" para o Filipe, calor pastoral em oração/luto, sugestões gentis "vale confirmar com…", sem caudas burocráticas "providência/disponibilidade não confirmada", sem jargão), exemplos fictícios, autocheck inclui estilo. Nova `natural_direct_sentence()` para DMs em *⚠️ Para você* ("Kelly avisou que…, e você respondeu “…”"); se perder palavras exigidas pelos validadores, volta ao formato antigo.
- `text_polish.py` (bak `text_polish.py.bak-style-20261104`, 2º ajuste `text_polish.py.bak-style2-20261104`): minúscula no verbo após nome; "; Resposta registrada:" → ", e você respondeu:"; "que Filipe" → "que você"; cabeçalho aceita 🕑; remove tarefas-modelo do Todoist ("Descobrir os layouts…", "Esclarecer: revise sua Entrada…").
- Dry-run tarde 08:33–08:42: `partial_success`, `prepared_not_sent`. Manifesto renomeado `delivery/2026-11-04.tarde.dryrun-0842-style.json`.
- Modelo inalterado.

Rollback:
```bash
cd /opt/ifc-painel/mcp
cp -p daily_ifc_pipeline.py.bak-style-20261104 daily_ifc_pipeline.py
cp -p text_polish.py.bak-style-20261104 text_polish.py
python3 -m py_compile daily_ifc_pipeline.py text_polish.py
```

## 04/11 ~08:35–09:10 — Painel UI `ifc-panel:20261104-35-ui` + humanização
- **Bug:** seletor "Escolha uma seção" quebrado — `render()` chamava `$("#sec-ata").addEventListener` sem a seção Ata existir ⇒ `TypeError` abortava o render antes de preencher select/TOC. Corrigido com `?.`; select/TOC só listam seções renderizadas; rolagem com offset da topbar; select volta a "Escolha…".
- Notas técnicas das fontes (bloco rosa `section-meta-error`) agora em `<details class="tech-details">` fechado com resumo de 1 linha ("Detalhes técnicos da coleta"); "Cobertura de mensagens e mídias" também recolhida; aviso laranja do topo encurtado.
- Fonte: `/opt/ifc-painel/build-panel-20261104-35-ui` (extraída do contêiner 34-dedupe) → [`vps-snapshot/2026-11-04/panel/`](../../vps-snapshot/2026-11-04/panel/).
- Rollback: `docker service update --image ifc-panel:20261104-34-dedupe ifc-panel`
- **Novo `mcp/panel_humanize.py`**, chamado no início de `sync_history.py` (bak `sync_history.py.bak-style-20261104`): cards "Atenção agora" sem jargão; pendências técnicas ("formatos não processados", curadoria, formato inválido) saem dos cards; falhas viram frase simples ("Drive da secretaria: a atualização de hoje falhou"). Notas técnicas continuam em `fontes`. Idempotente; preserva mtime/dono/permissão de `dados.json`. `dashboard_publisher.py` na imagem **não** foi alterado (rootfs somente leitura).

Rollback:
```bash
cd /opt/ifc-painel/mcp
cp -p sync_history.py.bak-style-20261104 sync_history.py && rm -f panel_humanize.py
cp -p text_polish.py.bak-style2-20261104 text_polish.py
cp -p /root/dados.json.bak-20261104-humanize /opt/ifc-painel/published/dados.json
```
