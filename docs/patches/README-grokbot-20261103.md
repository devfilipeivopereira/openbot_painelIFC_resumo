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
