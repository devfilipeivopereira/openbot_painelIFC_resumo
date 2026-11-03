"""Atomically deploy the IFC artifact exporter without restarting services."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import paramiko

LOCAL = Path(__file__).with_name("daily_artifacts.py")
REMOTE = "/opt/ifc-painel/mcp/daily_artifacts.py"
ENV_PATH = Path(sys.argv[1]).resolve()

env: dict[str, str] = {}
for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

content = LOCAL.read_bytes()
digest = hashlib.sha256(content).hexdigest()
ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(
    env["VPS_HOST"], username=env["VPS_USER"], password=env["VPS_PASSWORD"],
    timeout=10, auth_timeout=10, banner_timeout=10,
    allow_agent=False, look_for_keys=False,
)
try:
    sftp = ssh.open_sftp()
    try:
        attrs = sftp.stat(REMOTE)
        with sftp.file(REMOTE, "rb") as stream:
            previous = stream.read()
        if previous == content:
            print(f"daily_artifacts=already_current sha256={digest[:16]}")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = f"{REMOTE}.before-sender-fields-{stamp}.bak"
            try:
                sftp.stat(backup)
            except OSError:
                pass
            else:
                raise RuntimeError("daily_artifacts_backup_name_collision")
            with sftp.file(backup, "wb") as stream:
                stream.write(previous)
            sftp.chmod(backup, 0o600)
            temporary = f"{REMOTE}.deploy.tmp"
            with sftp.file(temporary, "wb") as stream:
                stream.write(content)
            sftp.chmod(temporary, attrs.st_mode & 0o777)
            if hasattr(sftp, "posix_rename"):
                sftp.posix_rename(temporary, REMOTE)
            else:
                sftp.rename(temporary, REMOTE)
            sftp.chmod(REMOTE, attrs.st_mode & 0o777)
            if hasattr(sftp, "chown"):
                sftp.chown(REMOTE, attrs.st_uid, attrs.st_gid)
            with sftp.file(REMOTE, "rb") as stream:
                deployed = stream.read()
            if hashlib.sha256(deployed).hexdigest() != digest:
                raise RuntimeError("daily_artifacts_deploy_hash_mismatch")
            print(f"daily_artifacts=deployed backup=present sha256={digest[:16]}")
    finally:
        sftp.close()
finally:
    ssh.close()
