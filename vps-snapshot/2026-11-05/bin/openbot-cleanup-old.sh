#!/bin/sh
# Limpeza única pós-upgrade 0.61 (agendada para 11/11/2026). Mantém backups tgz.
L=/var/log/openbot-cleanup-old.log; exec >>$L 2>&1; echo "== $(date)"
S=$(docker inspect openbot --format "{{.State.Running}} {{.RestartCount}} {{.Config.Image}}" 2>/dev/null)
echo "estado: $S"
case "$S" in "true 0 ghcr.io/nightly-labs/openbot:0.61.0") ;; *) echo "openbot nao saudavel/versao mudou; nada removido"; rm -f /etc/cron.d/openbot-cleanup-old; exit 0;; esac
docker exec openbot openbot status | grep -q online || { echo "servidor nao online; nada removido"; rm -f /etc/cron.d/openbot-cleanup-old; exit 0; }
docker rm openbot-0.54.0-old && echo "container antigo removido"
docker rmi ghcr.io/nightly-labs/openbot:latest ghcr.io/nightly-labs/openbot:0.47.1 && echo "imagens 0.47.1/latest removidas"
echo "mantidos: openbot-rollback:0.54.0 e /root/backups/openbot-upgrade-20261104"
rm -f /etc/cron.d/openbot-cleanup-old
