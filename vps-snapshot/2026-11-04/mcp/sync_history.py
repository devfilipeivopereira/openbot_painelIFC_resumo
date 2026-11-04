#!/usr/bin/env python3
"""Persist daily private panel snapshots in Supabase historico, with no data logging."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

HISTORY_DIR = Path("/opt/ifc-painel/published/history")
LOCK_PATH = Path("/run/lock/ifc-panel-history.lock")
BACKFILL_SENTINEL = Path("/opt/ifc-painel/mcp/history-backfill-v1.done")
CONTAINER_SERVICE = "supabase_supabase_db"
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024


def marker_path(snapshot: Path) -> Path:
    return snapshot.with_suffix(".synced")


def persist_marker(path: Path, digest: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".history-sync-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(digest + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chown(path, 65534, 65534)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def database_container() -> str | None:
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={CONTAINER_SERVICE}", "--format", "{{.Names}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    matches = [line.strip() for line in result.stdout.decode("ascii", "ignore").splitlines()
               if re.fullmatch(re.escape(CONTAINER_SERVICE) + r"\.1\.[a-z0-9]+", line.strip())]
    return matches[0] if len(matches) == 1 else None


def push(snapshot: Path) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot.stem):
        return False
    try:
        date.fromisoformat(snapshot.stem)
        raw = snapshot.read_bytes()
        if not raw or len(raw) > MAX_SNAPSHOT_BYTES:
            return False
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("hoje") != snapshot.stem:
            return False
    except (OSError, ValueError, json.JSONDecodeError):
        return False

    digest = hashlib.sha256(raw).hexdigest()
    try:
        if marker_path(snapshot).read_text(encoding="ascii").strip() == digest:
            return True
    except OSError:
        pass

    json_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    sql = (
        "SELECT historico.salvar(" + ",".join((
            sql_literal("ifc"), sql_literal("painel_html"), sql_literal("ifc-panel"),
            "now()", sql_literal(json_text) + "::jsonb", "NULL", "'{}'::jsonb",
        )) + ");\n"
    )
    container = database_container()
    if not container:
        return False
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-X", "-qAt",
             "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"],
            input=sql.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=45, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    response = result.stdout.decode("ascii", "ignore").strip()
    if result.returncode != 0 or not re.fullmatch(r"(?:novo:\d+|igual:\d+|repetido)", response):
        return False
    try:
        persist_marker(marker_path(snapshot), digest)
    except OSError:
        return False
    return True


def psql(sql: str, timeout: int = 90) -> str | None:
    container = database_container()
    if not container:
        return None
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-X", "-qAt",
             "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"],
            input=sql.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else None


def backfill_existing_history() -> bool:
    if BACKFILL_SENTINEL.exists():
        return True
    query = """
WITH days AS (
  SELECT DISTINCT (capturado_em AT TIME ZONE 'America/Sao_Paulo')::date AS day
  FROM historico.painel_snapshots
  WHERE painel='ifc' AND secao LIKE 'dados/%'
), state_by_day AS (
  SELECT d.day, s.secao, s.conteudo
  FROM days d
  CROSS JOIN LATERAL (
    SELECT DISTINCT ON (secao) secao, conteudo
    FROM historico.painel_snapshots
    WHERE painel='ifc' AND secao LIKE 'dados/%'
      AND capturado_em < ((d.day + 1)::timestamp AT TIME ZONE 'America/Sao_Paulo')
    ORDER BY secao, capturado_em DESC, id DESC
  ) s
), grouped AS (
  SELECT day, jsonb_object_agg(substr(secao,7), conteudo) AS data
  FROM state_by_day GROUP BY day
)
SELECT coalesce(json_agg(json_build_object('day',day::text,'data',data) ORDER BY day), '[]'::json)
FROM grouped;
"""
    response = psql(query)
    if response is None:
        return False
    try:
        rows = json.loads(response)
    except json.JSONDecodeError:
        return False
    if not isinstance(rows, list):
        return False
    HISTORY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("day"), str) or not isinstance(row.get("data"), dict):
            return False
        day = row["day"]
        try:
            date.fromisoformat(day)
        except ValueError:
            return False
        path = HISTORY_DIR / f"{day}.json"
        if path.exists():
            continue
        value = row["data"]
        value["hoje"] = day
        value["atualizado_em"] = "Histórico Supabase · " + date.fromisoformat(day).strftime("%d/%m/%Y")
        value["historico_reconstruido"] = True
        value["fontes"] = [{"fonte": "Histórico Supabase", "status": "Estado reconstruído",
                            "detalhe": "Seções recuperadas do histórico privado; podem ter sido capturadas em horários diferentes."}]
        raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".history-backfill-", dir=HISTORY_DIR)
        try:
            os.fchmod(fd, 0o600)
            os.fchown(fd, 65534, 65534)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            digest = hashlib.sha256(raw).hexdigest()
            persist_marker(marker_path(path), digest)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    BACKFILL_SENTINEL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    BACKFILL_SENTINEL.write_text(f"dates={len(rows)}\n", encoding="ascii")
    os.chmod(BACKFILL_SENTINEL, 0o600)
    return True


def main() -> int:
    try:
        import sys as _s; _s.path.insert(0, "/opt/ifc-painel/mcp")
        import panel_humanize; panel_humanize.main()
    except Exception:
        pass
    if not HISTORY_DIR.is_dir():
        return 0
    LOCK_PATH.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with LOCK_PATH.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if not backfill_existing_history():
            print('{"status":"backfill_failed","attempted":0,"failed":1}')
            return 1
        files = sorted(HISTORY_DIR.glob("????-??-??.json"))[-30:]
        attempted = 0
        failures = 0
        for snapshot in files:
            try:
                if marker_path(snapshot).read_text(encoding="ascii").strip() == hashlib.sha256(snapshot.read_bytes()).hexdigest():
                    continue
            except OSError:
                pass
            attempted += 1
            if not push(snapshot):
                failures += 1
        print(json.dumps({"status": "ok" if failures == 0 else "partial", "attempted": attempted, "failed": failures}))
        return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
