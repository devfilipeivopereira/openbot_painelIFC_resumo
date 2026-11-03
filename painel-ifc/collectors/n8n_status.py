#!/usr/bin/env python3
"""Read-only, metadata-only health collector for the fixed IFC workflows."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = os.environ.get("N8N_API_BASE", "").rstrip("/")
WORKFLOWS = tuple(item.strip() for item in os.environ.get("IFC_N8N_WORKFLOW_IDS", "").split(",") if item.strip())
if not API or not WORKFLOWS:
    raise SystemExit("n8n_configuration_missing")
HEADER_FILE = Path(os.environ.get("N8N_HEADER_FILE", "/run/secrets/n8n.h"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUT = ROOT / "staging" / "n8n.json"
MAX_PER_WORKFLOW = 100


def request_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("http_error")
        raw = response.read(2_000_000)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid_response")
    return value


def save_private(value: dict) -> None:
    OUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".n8n.", dir=OUT.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OUT)
        os.chmod(OUT, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    try:
        lines = HEADER_FILE.read_text(encoding="utf-8").splitlines()
        header = next((line for line in lines if line.lower().startswith("x-n8n-api-key:")), None)
        if not header:
            return 2
        api_key = header.split(":", 1)[1].strip()
        if not api_key:
            return 2
        headers = {"X-N8N-API-KEY": api_key, "Accept": "application/json"}
        since = datetime.now(timezone.utc) - timedelta(days=7)
        results = []
        for workflow_id in WORKFLOWS:
            workflow = request_json(f"{API}/workflows/{workflow_id}", headers)
            query = urllib.parse.urlencode({"workflowId": workflow_id, "limit": MAX_PER_WORKFLOW})
            execution_data = request_json(f"{API}/executions?{query}", headers)
            executions = execution_data.get("data", [])
            if not isinstance(executions, list):
                raise ValueError("invalid_execution_list")
            recent = []
            for row in executions:
                if not isinstance(row, dict):
                    continue
                started = row.get("startedAt")
                try:
                    stamp = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                if stamp >= since:
                    recent.append((stamp, str(row.get("status", "unknown")).lower()))
            recent.sort(reverse=True)
            statuses = [status for _, status in recent]
            last_status = statuses[0] if statuses else "unknown"
            failures = sum(1 for status in statuses if status in {"error", "failed", "crashed"})
            successes = sum(1 for status in statuses if status in {"success", "succeeded"})
            active = bool(workflow.get("active", False))
            if not active:
                health = "inativo"
            elif last_status in {"error", "failed", "crashed"}:
                health = "falha"
            elif failures:
                health = "atencao"
            elif last_status in {"success", "succeeded"}:
                health = "ok"
            else:
                health = "nao_verificado"
            results.append({
                "workflow_id": workflow_id,
                "active": active,
                "health": health,
                "last_status": last_status,
                "runs_7d": len(recent),
                "errors_7d": failures,
                "successes_7d": successes,
                "last_started_at": recent[0][0].isoformat(timespec="seconds") if recent else None,
            })
        output = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "workflows": results}
        save_private(output)
        print(json.dumps({"status": "ok", "workflows": len(results), "errors": sum(x["errors_7d"] for x in results)}))
        return 0
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError, RuntimeError):
        print('{"status":"failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
