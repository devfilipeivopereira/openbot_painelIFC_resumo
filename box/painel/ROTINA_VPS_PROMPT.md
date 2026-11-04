Atualizar Painel IFC (modo VPS, canvas LEVE). Não leia nenhuma fonte (WhatsApp, e-mail, Drive, Notion, Todoist, n8n, planilhas) e não rode atualizar_scripts.sh nem consolidar.py. A VPS já coletou e analisou tudo; o painel completo fica em https://openclaw.filipeivopereira.com/ifc-painel/ (com senha).

1. Rode no Shell: `python3 /home/box/ifc/painel/vps_to_canvas.py`. Leia só o JSON que ele imprime (o mesmo fica em vps_teste/leve/relatorio.json).
2. Se o comando falhar, não mexa na Page e avise em uma linha: "Painel IFC: VPS indisponível, painel mantido".
3. Se "modo" for "nada a fazer" ou começar com "pular", não edite e não avise nada (resposta: "Painel IFC: sem mudança importante").
4. Se "modo" for "EditCanvas(leve/edits_001.json)": chame EditCanvas uma vez no canvas_id 71171228-d779-4ce3-a91c-b9ca79c7f788 com o array "edits" de /home/box/ifc/painel/vps_teste/leve/edits_001.json, exatamente como está (ASCII, normalmente 1–3 KB). Se falhar porque um trecho não bateu, faça o passo 5.
5. Se "modo" começar com "bootstrap leve": WriteCanvas no mesmo canvas_id com o conteúdo de /home/box/ifc/painel/vps_teste/leve/painel_leve.tsx.txt (~6–10 KB, ASCII, já compila). Isso substitui a Page cheia antiga; as listas grandes ficam no painel da VPS.
6. Não mude o layout nem o template. Não envie nada no WhatsApp. Ao final, se editou: "📊 Painel IFC atualizado pela VPS: https://cursor.com/canvas/store-ea3f629f-b3ff-4b52-8b2f-00f5e20767f2/71171228-d779-4ce3-a91c-b9ca79c7f788"

Horários: 07:15 e 23:15 (sempre rodam; publicam só se algo importante mudou) e 14:15 (o script devolve "pular" a menos que haja alerta novo).
Importante = mudou algum destes: alertas de "Atenção agora", pedidos esperando resposta do Filipe, visitantes novos (7 dias), dinheiro (último mês/contas), agenda dos próximos 7 dias (eventos e pregação), destaque do dia, fluxos n8n com atenção. Horários de geração, contadores e totais que só "andam" não disparam atualização.
Alerta (libera a rodada das 14h) = item novo em Atenção agora, em pedidos esperando resposta ou em visitantes novos.
`--forcar` publica mesmo sem mudança; `--completo` volta ao fluxo antigo (Page cheia + bootstrap).
