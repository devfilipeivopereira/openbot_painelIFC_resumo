# Snapshot VPS — 05/11/2026 (America/Sao_Paulo)

Código operacional sanitizado do OpenBot/Remi_2 e dos sistemas novos:

- **atividades/** — coleta WhatsApp/agenda/email/Todoist → Supabase `atividades.*` → Google Sheet; relatório semanal `.md` no Drive.
- **contatos/** — base unificada (`contato.py`) + papéis IFC (`lideres.example.json`).
- **bin/evo-db** — leitura Evolution via Postgres (só leitura).
- **bin/openbot-update.sh** — atualização automática diária do OpenBot com backup/rollback.
- **openbot/bin/** — wrappers no container Remi_2: `evo-ler`, `evo-enviar`, `contato`.
- **openbot/remi2/** — `AGENTS.md` e `TOOLS.md` do Remi_2 (telefones do dono mascarados).
- **cron/** — agendamentos relacionados.

## Sanitização aplicada

- Telefones e JIDs substituídos por placeholders (`<OWNER_WA>`, `<FAMILY_*>`, `<PHONE_REDACTED>`, `<JID_REDACTED>`).
- E-mails pessoais substituídos por placeholders.
- Nenhum `.env`, chave API, token, cookie, JWT ou arquivo de segredo incluído.
- Excluídos de propósito: `family_jids.json`, `drive.json`, `sheet.json`, `out/`, `relatorios/`, dumps do Postgres/Supabase, base de contatos completa (~18k), `opencode.json`, `composio.key`, `pg.env`, logs.

Segredos continuam só na VPS, lidos de arquivos 0600. Ver `docs/CONTINUIDADE.md` e `docs/SECURITY.md`.
