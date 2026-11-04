# Agendamento, janelas e arquivos de estado

Todos os horários são **America/Sao_Paulo (BRT, UTC-3)**. Os crons rodam com frequência alta (todo minuto / a cada 5 min) e **o próprio Python decide** se é hora — por isso os comentários antigos dentro dos arquivos de cron (ex.: "06:19/22:15", "07:00 e 23:15") estão **desatualizados**; vale o código.

## Crons (`/etc/cron.d/`)

| Arquivo | Linha | Função |
|---|---|---|
| `ifc-daily-panel` | `* * * * * root python3 /opt/ifc-painel/mcp/digest_schedule.py >>/var/log/ifc-daily-panel.log` | Preparo das edições e publicação do painel |
| `ifc-whatsapp-summary` | `*/5 * * * * root python3 /opt/ifc-painel/mcp/whatsapp_delivery.py --scheduled >>/var/log/ifc-whatsapp-summary.log` | Envio do resumo |
| `ifc-panel-history-sync` | `*/5 * * * * root python3 /opt/ifc-painel/mcp/sync_history.py >>/var/log/ifc-panel-history-sync.log` | Humaniza cards + histórico |
| `ifc-artifact-history` | `*/5 * * * * root python3 /opt/ifc-painel/mcp/sync_daily_artifacts.py >>/var/log/ifc-artifact-history.log` | Histórico de artefatos |
| `ifc-healthcheck` | `55 7,14,23 * * * root python3 /opt/ifc-painel/mcp/healthcheck.py >>/var/log/ifc-healthcheck.log` | Checagem de saúde |
| `paineis-historico` | `10 */3 * * *` e `5 6 * * *` `coletar_vps.py`; `40 3 * * *` `backup_historico.sh` | Histórico dos painéis (schema `historico` no Supabase, criado 29/10 pelo Remi) |

Logrotate: `/etc/logrotate.d/ifc-painel` → `/var/log/ifc-*.log` semanal, 6 cópias, compress, copytruncate.

## Edições

| Edição | Preparo (`digest_schedule.due_edition`) | Envio (`whatsapp_delivery --scheduled`) | Publicação do painel |
|---|---|---|---|
| `matinal` | 06:15; retentativas 06:20–06:55 | 07:00–07:55 | `painel_manha` a partir de 07:05 |
| `tarde` | 13:15; retentativas 13:20–13:55 | 14:00–14:55 | `painel_tarde` a partir de 14:05 |
| `noturno` | 22:10; retentativas 22:15–22:55 | 23:00–23:55 | `painel_noturno` a partir de 23:05 |

### Regras do preparo (`digest_schedule.py`)
- Lock `digest-runs/schedule.lock` (não roda dois ao mesmo tempo).
- Marca `digest-runs/<dia>.<edição>.json` com `status` `started` → `finished`/`failed`, `attempt`, `reason`, `model_route`, `primary_model_reason`.
- Até **3 tentativas** por edição, com no mínimo 5 min entre elas, só se a anterior for `failed`.
- Roda `daily_ifc_pipeline.py` com `IFC_DIGEST_EDITION=<edição>` e `IFC_DEFER_PANEL_PUBLICATION=1`, timeout 3000 s.
- Válido = exit 0 e `status ∈ {ok, partial_success}` e `summary_delivery ∈ {prepared_not_sent, not_requested}`.
- stderr de tentativa falha → `digest-runs/<dia>.<edição>.attempt<N>.stderr.txt` (últimos 8000 chars).

### Regras da publicação
- Usa o preparo da própria edição; se não terminou, usa o **último preparo `finished` do dia** (matinal/painel_08/painel_1330/tarde/noturno).
- Bloqueia (`publication_blocked`) se nenhum preparo terminou ou se `ifc-curation.json` é mais antigo que o preparo.
- `docker exec ifc-mcp python3 /app/job_runner.py publish` → depois `sync_history.py`; se o histórico falhar fica `published_history_pending` e é re-tentado.

### Regras do envio (`whatsapp_delivery.py`)
- Só age no `--scheduled` nas horas 7, 14, 23 e minuto ≤ 55. Edição pela hora atual: <12 matinal, <20 tarde, senão noturno.
- Manifesto: `delivery/<dia>.json` (matinal) ou `delivery/<dia>.<edição>.json`; precisa `status=prepared_not_sent`, edição certa e idade ≤ 3 h.
- **Manifesto pronto:** envia o resumo completo; a partir do minuto 40 com nota de parcial se for o caso.
- **Manifesto ausente/inválido e nenhum preparo rodando** (lock `/run/lock/ifc-daily-panel.lock` livre): envia **na hora** o fallback "⚠️ O resumo completo desta edição não ficou pronto a tempo…" + link do painel.
- **Preparo ainda rodando:** `waiting_preparation` até o minuto 50; daí fallback. Nunca termina a janela sem mensagem.
- Ledger `delivery/<dia>[.<edição>].sent.json`: 1 envio por edição/dia (send.lock). Destinatário único: arquivo `mcp/secrets/summary-recipient` (número do Filipe). Instância Evolution "Pastor Filipe"; redirects recusados.

## Arquivos de estado

| Arquivo | Significado |
|---|---|
| `mcp/digest-runs/<dia>.<edição>.json` | Estado do preparo (`matinal`, `tarde`, `noturno`) |
| `mcp/digest-runs/<dia>.painel_<slot>-publish.json` | Estado da publicação (`painel_manha/tarde/noturno`) |
| `mcp/digest-runs/<dia>.<edição>.attemptN.stderr.txt` | Erro de tentativas falhas |
| `mcp/delivery/<dia>[.<edição>].json` | Manifesto preparado (texto + fontes + `degraded`) |
| `mcp/delivery/<dia>[.<edição>].sent.json` | Ledger do envio (`accepted_by_evolution`, message_id) |
| `mcp/delivery/*.dryrun-*.json` | Manifestos de testes renomeados (não são enviados) |
| `mcp/health/` | Problemas já alertados no dia |
| `published/dados.json` | Dados do painel |

## Comandos manuais (rodar na VPS como root, em `/opt/ifc-painel/mcp`)

```bash
# Ver estado de hoje
D=$(TZ=America/Sao_Paulo date +%F); cat digest-runs/$D.*.json; ls -la delivery/ | grep $D

# Dry-run do pipeline (NÃO envia): gera manifesto prepared_not_sent
IFC_DIGEST_EDITION=tarde IFC_DEFER_PANEL_PUBLICATION=1 python3 daily_ifc_pipeline.py | tail -1
#   depois RENOMEIE o manifesto gerado (ex.: delivery/$D.tarde.json -> $D.tarde.dryrun-HHMM.json)
#   para que o preparo oficial gere outro do zero.

# Conferir o texto que seria enviado (sem enviar)
python3 whatsapp_delivery.py --check            # edição pela hora atual
python3 whatsapp_delivery.py --check-fallback   # mostra o fallback
python3 -c "import whatsapp_delivery as w; print(w.execute(send=False, edition='tarde'))"

# Envio manual (respeita ledger; só para o número fixo). Só com pedido do Filipe!
python3 whatsapp_delivery.py --send
python3 whatsapp_delivery.py --send-fallback

# Republicar painel
docker exec ifc-mcp python3 /app/job_runner.py publish && python3 sync_history.py

# Healthcheck manual
python3 healthcheck.py
```

`--test` existe mas envia uma mensagem de teste — evite.
