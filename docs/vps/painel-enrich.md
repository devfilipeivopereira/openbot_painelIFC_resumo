
## 04/11 10:05 — enriquecimento do painel (panel_enrich.py), Grok Bot

Objetivo: o `published/dados.json` passa a alimentar todas as seções do Painel IFC. O pipeline das 06:15, 13:15 e 22:10 não foi alterado.

- Novo `mcp/panel_enrich.py`, mais o parser `mcp/enrich/louvor_parse.py`, que é uma cópia do coletar_louvor.py da box. Roda no host pelo cron `/etc/cron.d/ifc-panel-enrich` a cada 5 min, com timeout de 900 s e lock. Só trabalha quando o publisher grava um dados.json novo (o mtime mudou).
- Grava a chave `painel_ifc` dentro do `published/dados.json` e preserva o mtime, o dono e o modo do arquivo, então a checagem de saúde por idade do painel não muda. Cada parte falha sozinha e volta ao último valor bom, guardado em `/var/lib/ifc-panel-enrich/cache.json`.
- Tudo é só leitura:
  - visitantes: Supabase `public.visitantes`, com psql em modo read-only e sem telefones;
  - categorias e maiores lançamentos: Supabase transactions;
  - n8n: GET em /workflows e /executions, com nomes e saúde de 8 fluxos;
  - participantes: Evolution `GET /group/participants` (totais, admins e sem nome);
  - Louvor: Evolution `POST /chat/findMessages` no grupo do Louvor, últimos 60 dias;
  - Instagram: o coletor Composio do container com janela de 150 dias, gravando em `ifc-job/data/enrich/instagram_recent.json`, sem tocar no staging;
  - mensagens diretas e Mulheres: só leitura do staging.
- Log: `/var/log/ifc-panel-enrich.log`, já coberto pelo glob do logrotate `ifc-*.log`.
- Backup do painel: `backups/dados.json.bak-gaps-20261104`.
- Rollback:
  ```
  rm /etc/cron.d/ifc-panel-enrich
  python3 -c "import json;p='/opt/ifc-painel/published/dados.json';d=json.load(open(p));d.pop('painel_ifc',None);json.dump(d,open(p,'w'),ensure_ascii=False)"
  ```
  Os arquivos `mcp/panel_enrich.py` e `mcp/enrich/` podem ficar ou ser apagados.
- Na box, `/home/box/ifc/painel/vps_to_canvas.py` lê `painel_ifc` e o usa com prioridade. Interação dos visitantes e totais `homens_fora`/`agenda_fora`/`total_ifc_estimado`: mantidos do último bom (não há fonte na VPS).

## Prompt da rotina 'Atualizar Painel IFC' (modo VPS)

Atualizar Painel IFC (modo VPS, econômico). Não leia nenhuma fonte (WhatsApp, e-mail, Drive, Notion, Todoist, n8n, planilhas) e não rode atualizar_scripts.sh nem consolidar.py. A VPS já coletou e analisou tudo.

1. Rode no Shell: `python3 /home/box/ifc/painel/vps_to_canvas.py`. Leia só o JSON que ele imprime; o mesmo conteúdo fica em vps_teste/relatorio.json.
2. Se o comando falhar, não mexa na Page. Me avise neste chat em uma linha: "Painel IFC: VPS indisponível, painel mantido com os dados de <atualizado_em atual>".
3. Se "modo" começar com "EditCanvas", leia /home/box/ifc/painel/vps_teste/edits.json e chame EditCanvas no canvas_id 71171228-d779-4ce3-a91c-b9ca79c7f788 UMA vez, com o array "edits" exatamente como está no arquivo, sem reescrever nem resumir nada. Se "edits" for 0, não edite.
4. Se "modo" começar com "WriteCanvas", reescreva a Page no mesmo canvas_id com o conteúdo de /home/box/ifc/painel/vps_teste/painel_montado.tsx.txt, sem alterações. Isso só deve acontecer na primeira vez ou quando o template mudar.
5. Se a EditCanvas falhar porque algum trecho não bateu, rode o script de novo uma vez, que recalcula as edições contra a Page atual, e repita o passo 3. Se falhar de novo, use o passo 4.
6. Se "idade_vps_horas" for maior que 4, o script já põe o aviso "Dados da VPS com mais de 4h" em Atenção agora e mantém o último dado bom. Aplique assim mesmo e me avise em uma linha.
7. Não mude o layout, a ordem das seções nem o template. Não envie nada no WhatsApp. Ao final, responda só: "📊 Painel IFC atualizado pela VPS (<vps_atualizado_em>): https://cursor.com/canvas/store-ea3f629f-b3ff-4b52-8b2f-00f5e20767f2/71171228-d779-4ce3-a91c-b9ca79c7f788"

Horários: 07:05, 14:05 e 23:05 (horário de Brasília), logo depois da publicação da VPS.


## 04/11 10:40 — participantes por JID do grupo
- `participantes()` agora identifica cada grupo pelo **JID da Evolution** (`GET /group/participants/{inst}?groupJid=`, só leitura), não pelo nome. Grupos homônimos (ex.: dois "Célula") deixam de compartilhar a lista.
- Formato de cada item em `painel_ifc.participantes[]`:
  `{"id": "<sha256(JID do grupo)[:8]>", "g": "<nome>", "label": "<nome desambiguado, ex. Célula (11 membros)>", "c": "<categoria>", "t": total, "a": admins, "s": sem_nome, "admins": [nomes], "membros": [nomes]}`
  Em erro: `{"id","g","label","c","erro"}`.
- `membros`/`admins` só têm nomes de exibição (contatos do painel > name/pushName); qualquer nome com 8+ dígitos é descartado. Nenhum telefone/JID de pessoa vai para dados.json.
- Verificação: Célula 040a05c9 → 7/7; Célula 5cdf7570 → 11/11; mtime de dados.json preservado.
- Rollback: `cp -p /opt/ifc-painel/mcp/panel_enrich.py.bak-jid-20261104 /opt/ifc-painel/mcp/panel_enrich.py && python3 /opt/ifc-painel/mcp/panel_enrich.py --force`


## 04/11 11:05 — homens_fora / agenda_fora / total_ifc_estimado na VPS
- Antes só no box (`membros_grupos_ifc_v2.xlsx`, gerada uma vez da agenda do celular de 22/10/2026 + lista de homens; sem fonte automática).
- Agora: base estática de chaves de telefone em `mcp/secrets/ifc-base-contatos.json` (0600, não vai para a saída). `base_contatos()` conta ao vivo quem da base não está em nenhum grupo IFC lido nesta rodada.
- Saída: `painel_ifc.base_contatos = {"homens_fora", "agenda_fora", "total_ifc_estimado" (config, 720), "total_ifc_estimado_tipo": "estimado (config)", "base_homens", "base_agenda", "fonte"}`.
- Limite: contatos novos fora da base não aparecem; para renovar, regerar a planilha e recopiar o JSON.
- Rollback: `cp -p mcp/panel_enrich.py.bak-box2vps-20261104 mcp/panel_enrich.py && python3 mcp/panel_enrich.py --force`
