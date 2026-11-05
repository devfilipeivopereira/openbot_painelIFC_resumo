#!/bin/sh
cd /opt/atividades && flock -n /tmp/atividades.lock timeout 3000 python3 atividades.py daily >> /var/log/atividades.log 2>&1
