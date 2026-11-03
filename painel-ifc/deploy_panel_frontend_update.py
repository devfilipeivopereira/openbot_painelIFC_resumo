"""Update the existing IFC panel frontend while preserving its auth secret."""
from pathlib import Path
import sys
import paramiko

ROOT = Path(__file__).resolve().parent
ENV_PATH = Path(sys.argv[1]).resolve()
REMOTE = "/opt/ifc-painel"
VERSION = os.environ.get("IFC_PANEL_IMAGE_VERSION", "20261103-32")
SERVICE = os.environ.get("IFC_PANEL_SERVICE", "ifc-panel")

values = {}
for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect(values["VPS_HOST"], username=values["VPS_USER"], password=values["VPS_PASSWORD"],
            timeout=10, auth_timeout=10, banner_timeout=10, allow_agent=False, look_for_keys=False)

def run(command, timeout=180):
    _stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out, err = stdout.read(), stderr.read()
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f"Remote panel update failed ({code}): {(out + err).decode('utf-8','replace')[-1200:]}")
    return out.decode("utf-8", "replace").strip()

try:
    existing = run(f"docker service inspect {SERVICE} --format '{{{{.Spec.TaskTemplate.ContainerSpec.Image}}}}'")
    if not existing.startswith("ifc-panel:"):
        raise RuntimeError("Existing panel image could not be verified; update stopped.")
    sftp = ssh.open_sftp()
    try:
        for name in ("index.html", "painel.css", "painel.js", "server.py", "Dockerfile"):
            sftp.put(str(ROOT / name), f"{REMOTE}/web/{name}")
            sftp.chmod(f"{REMOTE}/web/{name}", 0o644)
    finally:
        sftp.close()
    run(f"docker build --pull=false -t ifc-panel:{VERSION} {REMOTE}/web", timeout=300)
    run(f"docker service update --image ifc-panel:{VERSION} --update-failure-action rollback {SERVICE}", timeout=180)
    status = run(f"docker service ps {SERVICE} --format '{{{{.CurrentState}}}}' | head -1", timeout=30)
    if not status.startswith("Running"):
        run(f"docker service update --rollback {SERVICE}", timeout=180)
        raise RuntimeError(f"Panel update did not reach Running state; previous image restored (state={status}).")
    print(f"Panel frontend updated: {VERSION}; previous image retained: {existing}")
    print("Existing HTTP Basic authentication secret and route were preserved.")
finally:
    ssh.close()
