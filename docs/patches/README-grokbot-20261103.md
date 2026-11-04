# Correções Grok Bot — 03/11/2026
Patch: PATCHES-grokbot-20261103.diff (aplicar com: cd /opt/ifc-painel/mcp && patch -p0 --binary < ../PATCHES-grokbot-20261103.diff, ou manualmente). Backups: *.before-grokbot-*.bak em mcp/.
Motivo: desde 01/11 o painel não publicava e o resumo agendado não saía (portões de validação falhando).
1. daily_ifc_pipeline.py: bloco Todoist urgente sempre anexado; fit_whatsapp_limit (corta destaques de baixo p/ cima, máx 2400, nunca mensagens diretas/Todoist); mensagens diretas legíveis + título primeiro + sem cabeçalho duplicado; erro whatsapp_draft_too_long; run_codex troca de conta codex-auth no limite; complete_review_list preenche perguntas/pedidos não classificados como informational (contado em reviews_autofilled; >60% faltando => falha). Regra aprovada por Filipe.
2. digest_schedule.py: publicação noturna 23:10-23:59 (painel_noturno) e publicação usa a última preparação concluída do dia.
3. whatsapp_delivery.py: janelas 07:00-07:45 e 23:15-23:59 (ledger: 1 envio/edição/dia, só para o número fixo do Filipe).
Não sobrescreva estes arquivos sem reaplicar o patch.
4. job-runner-overlay/job_runner.py: import unicodedata faltando (publicacao quebrava) e REQUEST_CUES sincronizado com o pipeline (tem/temos que|de). Imagem ifc-mcp:20261103-94-grokbot; containers anteriores preservados como ifc-mcp-before-job-runner-*.
5. Tres edicoes/dia (Filipe 03/11): preparo 06:15, 13:15, 22:10 (retentativas ate :55); envio 07:00, 14:00, 23:00 (janela 45 min, 1 envio por edicao/dia, so para o numero do Filipe); publicacao do painel 07:05+, 14:05+, 23:05+ usando a ultima preparacao concluida. Backups *.before-grokbot-3x-*.bak.
6. Em andamento (Codex no conteiner openbot, NAO implantado): /data/work/painel-front-20261103 (frontend) e /data/work/digest-fix-20261103 (estilo n8n/qualidade). Ao implantar, reaplicar as mudancas 1-5.
7. Frontend novo (Codex, 3 etapas) implantado 03/11 23:33: imagem ifc-panel:20261103-33-grokbot (anterior: ifc-panel:20261103-32; arquivos web/*.before-grokbot-front-20261103.bak). Reverter: docker service update --image ifc-panel:20261103-32 ifc-panel.
8. Saude: /opt/ifc-painel/mcp/healthcheck.py (cron /etc/cron.d/ifc-healthcheck 07:55, 14:55, 23:55): smoke test do job_runner, containers, edicoes falhas/nao enviadas, lock preso >60 min, painel >26h, disco >90%; alerta 1x por problema/dia so ao numero fixo do Filipe. Logrotate: /etc/logrotate.d/ifc-painel (semanal, 6 copias).
9. Resumo estilo n8n (Codex) NAO implantado: em /var/lib/docker/volumes/openbot-data/_data/work/digest-fix-20261103; precisa de execucao real do pipeline para validar.

## 04/11 01:50 — degradação + estilo n8n (Grok Bot)
- Painel: imagem ifc-panel:20261104-34-dedupe (dedupe "Precisa de você" e Agenda). Rollback: docker service update --image ifc-panel:20261103-33-grokbot ifc-panel
- Pipeline: validações viram degradação (evidência duplicada/citação não encontrada descartadas, claims sem evidência removidas, reviews autocompletadas/redirecionadas, orações puladas, conversas faltantes com placeholder, agenda incompleta não bloqueia). Contadores em "degraded" no output.
- Entrega: --scheduled a partir do minuto 40 envia resumo parcial (nota curta) ou aviso de falha + link do painel, nunca nada. text_polish.py limpa cabeçalhos vazios, dicas de onboarding do Todoist, bloco duplicado de pendências e linha de cobertura.
- Rollback: cd /opt/ifc-painel/mcp; for f in daily_ifc_pipeline.py daily_artifacts.py whatsapp_delivery.py; do cp $f.bak-newstyle-202611040149 $f; done  (pré-degradação: *.bak-degrade-20261104001*)

## 04/11 01:51 — ajustes finais
- Finanças: resultado do mês = entradas − saídas (antes usava "saldo", inconsistente).
- DMs: o encurtamento termina em oração completa (vírgula/;/" e ") e remove palavras soltas no fim (iria, de, para…); text_polish repete a limpeza.
- Todoist: lista ampliada de dicas de onboarding excluídas também da contagem "+N sem prazo".
- Cabeçalho por edição: ☀️ Resumo da manhã / 🌤️ Resumo da tarde / 🌙 Resumo do dia (text_polish, pela hora do envio).
- Rollback deste passo: cd /opt/ifc-painel/mcp; for f in daily_artifacts.py daily_ifc_pipeline.py text_polish.py; do cp $f.bak-fix6-202611040151 $f; done

## 04/11 07:30–08:00 — incidente da edição matinal (FileNotFoundError) e correções

**O que aconteceu**
- Preparação matinal (06:15 + retry 06:35) falhou: `digest-runs/2026-11-04.matinal.json` = failed, `pipeline_output_not_json`. Reexecução manual às 07:29 mostrou a causa real: `family_schedule_validation_failed:family_evidence_missing` (o modelo citou um compromisso de família cuja evidência não batia com a fonte → `family_artifacts.save` levantava ValueError e o pipeline abortava a edição inteira).
- Sem preparação, o manifesto `delivery/2026-11-04.json` não existia → `whatsapp_delivery.py --scheduled` falhava com FileNotFoundError a cada 5 min. O fallback só era permitido a partir do minuto 40.
- Envio: às 07:50 saiu o fallback "⚠️ O resumo completo desta edição não ficou pronto a tempo…" com link do painel, só para o número fixo do Filipe. Ledger `delivery/2026-11-04.sent.json` = accepted_by_evolution (message_id 3EB01F93…). Sem duplicatas.

**Correções**
1. `whatsapp_delivery.py` (bak: `whatsapp_delivery.py.bak-fnf-202611040731`):
   - Janela agendada vai até o minuto 55 (antes 45).
   - Manifesto ausente/inválido/velho (>3h ou edição errada) e nenhuma preparação rodando (lock `/run/lock/ifc-daily-panel.lock` livre) → fallback imediato com link do painel.
   - Preparação ainda rodando → `waiting_preparation` até o minuto 50; daí fallback (nunca fica sem mensagem).
   - Novas opções: `--send-fallback` (envio manual do fallback, respeita ledger).
2. `daily_ifc_pipeline.py` (bak: `.bak-fnf-202611040732`, `.bak-family-202611040753`):
   - Falha de validação da agenda da família agora degrada (remove só os itens inválidos; em último caso grava lista vazia) e marca `degraded.family_validation_degraded:*` em vez de derrubar a edição.
   - Qualquer exceção não prevista imprime JSON `{"status":"failed","reason":"crash_<Tipo>_line<N>"}` + traceback no stderr (acaba o `pipeline_output_not_json` sem pista).
3. `digest_schedule.py` (bak: `.bak-fnf-202611040732`):
   - stderr de tentativas falhas salvo em `digest-runs/<dia>.<edição>.attempt<N>.stderr.txt`.
   - Edições com até 3 tentativas (antes 2).

**Rollback**
```
cd /opt/ifc-painel/mcp
cp -p whatsapp_delivery.py.bak-fnf-202611040731 whatsapp_delivery.py
cp -p daily_ifc_pipeline.py.bak-fnf-202611040732 daily_ifc_pipeline.py   # (desfaz family + crash JSON)
cp -p digest_schedule.py.bak-fnf-202611040732 digest_schedule.py
python3 -m py_compile whatsapp_delivery.py daily_ifc_pipeline.py digest_schedule.py
```

### 04/11 08:00–08:25 — causa raiz confirmada + mais correções
- **Causa raiz do `pipeline_output_not_json` (06:35):** `daily_artifacts.prepare_delivery` levantava `ValueError('finance_digest_details_missing')` sem tratamento (traceback só no stderr, que não era salvo). O bloco de finanças (simplificado em 03–04/11) mostra "resultado do mês = entradas − saídas", mas o autocheck exigia o valor `saldo` da planilha; em out/2026 `saldo ≠ entradas − saídas` → falha determinística sempre que o pipeline chegava à montagem do resumo. A reexecução das 07:29 parou antes, em `family_evidence_missing` (falha do modelo na agenda da família, também fatal).
- **Fix `daily_artifacts.py`** (bak `daily_artifacts.py.bak-finance-202611040805`): autocheck só exige entradas/saídas; se ainda falhar, remove o bloco de finanças em vez de abortar a edição.
- **Fix `healthcheck.py`** (bak `healthcheck.py.bak-0824-20261104`): marca edição failed (com motivo) após 3 tentativas, edição presa >60 min, `painel_nao_republicado_<edição>` se dados.json não mudou desde HH:00 da edição, e `envio_<edição>:fallback_sem_resumo_completo`.
- **Dry-run tarde 08:03–08:17** (sem envio): `partial_success`, `summary_delivery=prepared_not_sent`, modelo gpt-6-luna, degradações aplicadas (family_events_dropped 1, prayer_updates_skipped 1). `execute(send=False, edition='tarde')` → `ready_not_sent`, 2755 caracteres, sem degradação. Manifesto renomeado para `delivery/2026-11-04.tarde.dryrun-0817.json` (o das 13:15 será gerado do zero).
- **Painel republicado 08:20** via `docker exec ifc-mcp python3 /app/job_runner.py publish` + `sync_history.py` (ok). Backup do anterior: `/root/dados.json.bak-20261104-0822`.

Rollback adicional:
```
cd /opt/ifc-painel/mcp
cp -p daily_artifacts.py.bak-finance-202611040805 daily_artifacts.py
cp -p healthcheck.py.bak-0824-20261104 healthcheck.py
cp -p daily_ifc_pipeline.py.bak-family-202611040753 daily_ifc_pipeline.py   # desfaz só o degrade da família
cp -p /root/dados.json.bak-20261104-0822 /opt/ifc-painel/published/dados.json  # painel anterior
```

### 04/11 08:30 — estilo de escrita igual ao do bot IFC (pedido do Filipe)
- **Pedido:** "O jeito que vc escreve os resumos enviados pelo IFC é mais agradável… Ajuste para ficar igual ao seu estilo… A formatação está legal". Formatação/estrutura mantidas; só o texto mudou.
- **`daily_ifc_pipeline.py`** (bak `daily_ifc_pipeline.py.bak-style-20261104`):
  - Prompt: novo bloco "WRITING STYLE" (frases completas e naturais, nome primeiro + verbo no passado, "você" para o Filipe, calor pastoral em oração/luto, sugestões gentis tipo "vale confirmar com…", sem caudas burocráticas "providência/disponibilidade não confirmada", sem jargão interno nos bullets) + exemplos fictícios derivados do resumo do IFC; autocheck final agora inclui o estilo.
  - Novo `natural_direct_sentence()`: os bullets das DMs em *⚠️ Para você* viram frase natural ("Kelly avisou que…, e você respondeu “…”") em vez de "Kelly Avisou…; Resposta registrada: …". Se a frase nova perder palavras de conteúdo exigidas pelos validadores, cai no formato antigo (validação intacta).
- **`text_polish.py`** (bak `text_polish.py.bak-style-20261104`): minúscula no verbo após nome ("Kelly Avisou" → "Kelly avisou"), "; Resposta registrada:" → ", e você respondeu:", "; Filipe respondeu/enviou…" → ", e você …", "que Filipe" → "que você".
- Modelo inalterado (gpt-6-luna; fallback LiteLLM "tudo").

Rollback:
```
cd /opt/ifc-painel/mcp
cp -p daily_ifc_pipeline.py.bak-style-20261104 daily_ifc_pipeline.py
cp -p text_polish.py.bak-style-20261104 text_polish.py
python3 -m py_compile daily_ifc_pipeline.py text_polish.py
```

## 04/11 ~09:10 BRT — Painel UI (ifc-panel:20261104-35-ui)
- Seletor "Ir para uma seção" quebrado: render() chamava $("#sec-ata").addEventListener sem a seção Ata existir -> TypeError abortava render antes de preencher o select/TOC (e mostrava erro). Corrigido com ?. ; select/TOC só listam seções renderizadas; rolagem com offset da topbar; select volta a "Escolha…".
- Notas técnicas das fontes (bloco rosa section-meta-error) agora em <details class="tech-details"> fechado com resumo de 1 linha; "Cobertura de mensagens e mídias" também recolhida; aviso de topo encurtado.
- Fonte: /opt/ifc-painel/build-panel-20261104-35-ui (extraída do container 34-dedupe).
- Rollback: docker service update --image ifc-panel:20261104-34-dedupe ifc-panel
- **Dry-run tarde 08:33–08:42** (sem envio): `partial_success`, `prepared_not_sent`, gpt-6-luna via Codex. Manifesto renomeado para `delivery/2026-11-04.tarde.dryrun-0842-style.json` (o das 13:15 será gerado do zero).
- **`text_polish.py`** (2º ajuste, bak `text_polish.py.bak-style2-20261104`): cabeçalho também aceita 🕑 (vira 🌤️ *Resumo da tarde*); remove tarefas-modelo do Todoist "Descobrir os layouts…" e "Esclarecer: revise sua Entrada…".
- **Cards "Atenção agora" do painel sem jargão** — novo `mcp/panel_humanize.py`, chamado no início de `sync_history.py` (cron */5 e logo após cada publicação; bak `sync_history.py.bak-style-20261104`). Pendências puramente técnicas ("Pendente: formatos não processados", curadoria, formato inválido) saem dos cards; falhas/cobertura incompleta viram frase simples ("Drive da secretaria: a atualização de hoje falhou"). As notas técnicas continuam em `fontes` (Status das fontes). Idempotente; preserva mtime/dono/permissão do dados.json (healthcheck não é enganado). O `dashboard_publisher.py` da imagem não foi alterado (rootfs somente leitura).

Rollback (adicional):
```
cd /opt/ifc-painel/mcp
cp -p sync_history.py.bak-style-20261104 sync_history.py && rm -f panel_humanize.py
cp -p text_polish.py.bak-style2-20261104 text_polish.py
cp -p /root/dados.json.bak-20261104-humanize /opt/ifc-painel/published/dados.json
```
