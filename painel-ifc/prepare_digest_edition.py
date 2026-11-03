"""Prepare one authorized IFC WhatsApp digest without publishing or sending it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import paramiko


if len(sys.argv) not in (2, 3):
    raise SystemExit("usage: prepare_digest_edition.py VPS_ENV [matinal|noturno]")
edition = sys.argv[2] if len(sys.argv) == 3 else "matinal"
if edition not in ("matinal", "noturno"):
    raise SystemExit("digest_edition_invalid")

env = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(env["VPS_HOST"], username=env["VPS_USER"], password=env["VPS_PASSWORD"],
            timeout=10, auth_timeout=10, banner_timeout=10, allow_agent=False, look_for_keys=False)
try:
    command = (
        f"IFC_DIGEST_EDITION={edition} IFC_DEFER_PANEL_PUBLICATION=1 "
        "IFC_REUSE_CURRENT_COLLECTION=1 "
        "python3 /opt/ifc-painel/mcp/daily_ifc_pipeline.py"
    )
    _stdin, stdout, _stderr = ssh.exec_command(command, timeout=3600)
    result = stdout.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
finally:
    ssh.close()

if result:
    try:
        print(json.dumps(json.loads(result.splitlines()[-1]), ensure_ascii=False))
    except (json.JSONDecodeError, IndexError):
        print("digest_prepare_output_invalid")
if code:
    raise SystemExit("digest_prepare_failed")
