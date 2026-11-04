# Painel IFC: página completa na VPS + canvas leve (04/11/2026)

## Página completa (VPS)
- URL: https://openclaw.filipeivopereira.com/ifc-painel/ (Basic Auth; senha no segredo docker `ifc-panel-auth`, nunca no repo).
- Imagem `ifc-panel:20261104-36-extra`, fonte em `/opt/ifc-painel/build-panel-36-extra` (arquivos da 35-ui + `painel-extra.js`).
- Zero tokens: o servidor lê `published/dados.json` a cada acesso; atualiza sozinho a cada publicação (:05) e enriquecimento (cron 5 min).
- `painel-extra.js` acrescenta, recolhidos e no seletor "Ir para uma seção": grupos e participantes (por JID), visitantes e interação, financeiro por categoria e maiores lançamentos, escala do louvor, contatos fora dos grupos, bloco técnico (n8n/coleta).
- Rollback: `docker service update --image ifc-panel:20261104-35-ui ifc-panel`.

## Canvas leve (box)
- `vps_to_canvas.py` agora chama `tools/canvas_leve.py` por padrão (`--completo` = fluxo antigo; backup `vps_to_canvas.v6.bak`).
- Saída em `vps_teste/leve/`: `painel_leve.tsx.txt` (~6 KB, ASCII, compila com tsc strict), `edits_001.json`, `relatorio.json`.
- Conteúdo: números-chave, destaque do dia, o que espera resposta do Filipe, Atenção agora, visitantes novos (7 dias), dinheiro (último mês; ainda não há fonte de contas a vencer), agenda 7 dias, resumos com totais e link para a página da VPS.
- Modos: `bootstrap leve` (WriteCanvas), `EditCanvas(leve/edits_001.json)` (só blocos que mudaram, ~0,5–3 KB), `nada a fazer`, `pular`.
- Importante (impressão digital FP gravada na Page): Atenção agora, pedidos esperando resposta, visitantes novos, dinheiro, agenda 7 dias, destaque, n8n com atenção. Ignora horários de geração e contadores.
- Horários: 07:15 e 23:15 publicam se FP mudou; 14:15 só se houver alerta novo (item novo em Atenção, pendências ou visitantes novos).
