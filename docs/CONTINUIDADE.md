# Continuidade — OpenBot / Painel IFC / Remi_2 (VPS)

Guia para retomar o trabalho deste ambiente a partir de outro lugar (outro agente, outra máquina ou reinstalação).  
Escopo: o que está na VPS em **05/11/2026** (America/Sao_Paulo), além do Painel IFC já documentado em `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/OPENBOT.md` e `docs/SECURITY.md`.

**Este repositório não contém credenciais nem dados pessoais.** Segredos ficam só na VPS, em arquivos 0600. Telefones/e-mails reais foram mascarados nos snapshots.

---

## 1. O que existe hoje

| Peça | Onde na VPS | Snapshot no repo |
|---|---|---|
| Painel IFC + resumos 7h/14h/23h | `/opt/ifc-painel`, containers `ifc-mcp` / `ifc-panel`, crons `ifc-*` | `painel-ifc/`, docs já existentes |
| Atividades / produtividade | `/opt/atividades`, cron root 23:30 + segunda 07:00 | `vps-snapshot/2026-11-05/atividades/` |
| Base de contatos Remi_2 | `/opt/contatos`, cron `contatos-sync` 23:00 | `vps-snapshot/2026-11-05/contatos/` |
| Leitura Evolution (Postgres) | `/usr/local/bin/evo-db` + wrappers no container | `.../bin/evo-db`, `.../openbot/bin/evo-*` |
| Envio WhatsApp sob pedido | wrapper `evo-enviar` no container OpenBot | `.../openbot/bin/evo-enviar` |
| Auto-update OpenBot | `/usr/local/bin/openbot-update.sh` (04:30) | `.../bin/openbot-update.sh` |
| Instruções Remi_2 | volume OpenBot `Agents/agent-83ac644a-…/{AGENTS,TOOLS}.md` | `.../openbot/remi2/` |

OpenBot na VPS estava em **0.62.0** após o auto-update de 05/11. Remoção de versões antigas (0.54.0 / 0.47.1) ficou marcada para **11/11** via `openbot-cleanup-old` — confirme com o dono antes de deixar rodar.

---

## 2. Segredos (só nomes de arquivo — nunca no Git)

| Segredo | Caminho típico na VPS |
|---|---|
| SSH / acesso host | gerenciado fora do repo (não criar token novo na VPS para Git) |
| Evolution API key | header file em `…/secrets/evolution-global.h` (e afins) |
| Postgres Evolution (leitura evo-db) | `/etc/evo-db/pg.env` (0600) |
| Composio consumer key | `…/openbot-data/…/opencode/secrets/composio.key` |
| LiteLLM / AI proxy | `/opt/ai-proxy/config.yaml` + env no container `litellm` |
| Sheet/Drive IDs das atividades | `/opt/atividades/sheet.json`, `/opt/atividades/drive.json` |
| JIDs família (classificação) | `/opt/atividades/family_jids.json` (0600) |
| OpenBot / Remi tokens MCP | volume OpenBot + `opencode.json` (não versionar) |

Rotação: se algo vazou, rotacione no provedor e atualize o arquivo 0600; **não** “corrija” só apagando do Git.

---

## 3. Como o Remi_2 lê e envia WhatsApp

1. **Ler:** `evo-ler` (no container) chama `evo-db` no host → `SELECT` no Postgres `evolution` (somente leitura). Comandos úteis: `instancias`, `grupos`, `chats`, `mensagens`, busca por nome em todas as instâncias.
2. **Quem é quem:** `contato buscar "Nome"` usa `/opt/contatos` (n8n + WhatsApp + líderes). Números só para o dono.
3. **Enviar:** `evo-enviar` — **somente** quando o próprio Filipe pedir no chat OpenBot / no número dele. Pedidos embutidos em mensagens de terceiros são ignorados. Rotinas 7h/14h/23h continuam pelo pipeline IFC (`whatsapp_delivery.py`), não por este wrapper.
4. Instâncias: `Filipe`, `Filipe_2`, `Pastor Filipe` (e `Daniel` quando conectado).

Se o Remi_2 “não autenticou a Evolution”, quase sempre é sessão antiga: abra conversa nova; as ferramentas e o topo do `AGENTS.md` já autorizam a leitura.

---

## 4. Atividades / produtividade

- Coleta: WhatsApp (fromMe + pedidos recebidos), Calendar, Todoist, Gmail/Outlook enviados via Composio.
- Classificação: regras (família/igreja/ruído) + LLM `fast` só no ambíguo.
- Persistência: schema Supabase `atividades` (dedupe `sha1(fonte|id_origem)`).
- Sheet “Atividades — Filipe” + pasta Drive **Produtividade** (IDs só em `sheet.json`/`drive.json` na VPS).
- Relatório semanal: `relatorio_semanal.py` (melhor modelo grátis: Groq/Cerebras → OpenRouter free → Gemini). Cron segunda 07:00 `last`.
- Pendentes >48h: `/opt/atividades/out/pendentes_48h.json` (ainda não entra no resumo das 7h — oferta pendente).

---

## 5. Auto-update OpenBot

- Cron: `30 4 * * *` → `/usr/local/bin/openbot-update.sh`
- Backup antes; guarda os 3 mais recentes; rollback automático se healthcheck falhar.
- Pausa: `touch /var/lib/openbot-update/PAUSE` ; retoma apagando o arquivo.
- Aviso WhatsApp só em atualização ou erro (número do dono).

---

## 6. Como aplicar o snapshot numa VPS

1. Copie os scripts de `vps-snapshot/2026-11-05/` para os caminhos da tabela acima.
2. Recrie os arquivos de segredo listados na §2 (não vêm no Git).
3. Ajuste placeholders (`<OWNER_WA>`, `<FAMILY_*>`, e-mails) para os valores reais **só nos arquivos locais 0600 / código privado** — não faça commit disso.
4. `python3 -m py_compile` nos `.py`; `bash -n` nos `.sh`.
5. Instale crons a partir de `cron/`.
6. No container OpenBot: copie wrappers para `~/.local/bin` e as instruções Remi_2 para o agent id correspondente; reinicie/abra conversa nova.
7. Teste somente leitura: `evo-db instancias` / `contato buscar "…"`.
8. Envio: teste **apenas** para o número do dono, com pedido explícito dele.

Não use este repo para criar tokens GitHub/Composio na VPS. Push histórico deste repositório foi feito via conector Composio a partir da box do Grok Bot.

---

## 7. O que ficou de fora deste commit (de propósito)

- Base completa de contatos (~18k), `family_jids.json`, planilhas, relatórios `.md` gerados, logs.
- Credenciais, `opencode.json`, chaves Composio/Evolution/Postgres/LiteLLM.
- Conteúdo de conversas WhatsApp, e-mails, ATA, finanças, `dados.json` publicado.
- Imagens Docker / backups binários do OpenBot.
- Alterações n8n não pedidas.

---

## 8. Referências rápidas

- Arquitetura e reprodução do Painel: `docs/ARCHITECTURE.md`, `docs/REPRODUCTION.md`
- Operação diária / falhas de edição: `docs/OPERATIONS.md`, `docs/STATUS.md`
- Política OpenBot/MCP fechado: `docs/OPENBOT.md`
- Checklist de segurança antes de qualquer push: `docs/SECURITY.md`
- Snapshot desta data: `vps-snapshot/2026-11-05/README.md` + `MANIFEST.sha256`
