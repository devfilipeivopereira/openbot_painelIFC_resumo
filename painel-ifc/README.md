# Painel IFC — aplicação web

Aplicação web estática com API Python pequena, autenticação HTTP Basic e filtragem adicional de dados. Ela consome snapshots JSON produzidos pelo pipeline; não faz coleta nem envia mensagens.

## Componentes

- `index.html`, `painel.css`, `painel.js`: interface responsiva, navegação por seções, tabelas e gráficos.
- `server.py`: API autenticada para snapshot atual, tarefas, situação da curadoria, Ata e histórico.
- `Dockerfile`: imagem mínima do frontend.
- `deploy_panel_frontend_update.py`: atualização do serviço existente, sem substituir a autenticação.
- `verify_vps.py`: verificação autenticada do serviço implantado.

## Execução local isolada

Use dados sintéticos; não copie snapshots reais para uma máquina compartilhada.

```powershell
$env:IFC_PANEL_DATA_DIR = (Join-Path $PWD 'data-demo')
$env:IFC_PANEL_JSON = (Join-Path $env:IFC_PANEL_DATA_DIR 'dados.json')
$env:IFC_PANEL_AUTH_FILE = (Join-Path $PWD '.local-panel-auth')
$env:IFC_PANEL_USER = 'demo'
$env:IFC_PANEL_PASSWORD = 'use-uma-senha-local-forte'
python server.py
```

Abra `http://127.0.0.1:8787/`. Sem autenticação, as páginas e APIs retornam 401. Em produção, use HTTPS via proxy, segredo montado somente leitura e bind privado; nunca publique a porta da aplicação sem TLS e autenticação.

Os formatos de dados, rotas e política de privacidade estão em [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`docs/SECURITY.md`](../docs/SECURITY.md) e [`docs/REPRODUCTION.md`](../docs/REPRODUCTION.md).
