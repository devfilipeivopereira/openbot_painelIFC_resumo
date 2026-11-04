# Snapshot do código vivo na VPS — 04/11/2026 ~09:30 BRT

Cópia (sem segredos) dos arquivos modificados em produção. Mapeamento:
- `mcp/` → `/opt/ifc-painel/mcp/` (inclui `job-runner-overlay/`)
- `panel/` → `/opt/ifc-painel/build-panel-20261104-35-ui/` (imagem `ifc-panel:20261104-35-ui`)
- `cron/` → `/etc/cron.d/`
- `logrotate/` → `/etc/logrotate.d/`

Segredos ficam só na VPS (`mcp/secrets/`, `/run/secrets/`) e não estão aqui. Veja `docs/vps/`.
