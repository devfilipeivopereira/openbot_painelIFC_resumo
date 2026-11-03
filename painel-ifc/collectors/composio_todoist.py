#!/usr/bin/env python3
"""Read every open Todoist task through Composio, preserving dates and priority."""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MCP_URL = "https://connect.composio.dev/mcp"
KEY_FILE = Path(os.environ.get("COMPOSIO_KEY_FILE", "/run/secrets/composio.key"))
ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
OUTPUT = ROOT / "staging" / "todoist.json"
WEB_OUTPUT = Path(os.environ.get("IFC_TODOIST_PANEL_OUTPUT", "/run/publish/todoist.json"))
ZONE = ZoneInfo("America/Sao_Paulo")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
JID_RE = re.compile(r"\b[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)\b", re.I)


def rpc(payload: dict, key: str, transport_session: str | None = None) -> tuple[dict, str | None]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "x-consumer-api-key": key}
    if transport_session:
        headers["Mcp-Session-Id"] = transport_session
    request = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(8_000_000)
            content_type = response.headers.get("Content-Type", "")
            session = response.headers.get("Mcp-Session-Id")
    except Exception:
        raise ValueError("composio_request_failed") from None
    text = raw.decode("utf-8", "replace")
    try:
        if "text/event-stream" in content_type:
            lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            if not lines:
                raise ValueError
            return json.loads(lines[-1]), session
        return json.loads(text), session
    except Exception:
        raise ValueError("composio_response_invalid") from None


def text_items(response: dict) -> list[str]:
    content = response.get("result", {}).get("content", [])
    return [x["text"] for x in content if isinstance(x, dict) and isinstance(x.get("text"), str)] if isinstance(content, list) else []


def walk(value):
    if isinstance(value, str):
        try:
            yield from walk(json.loads(value))
        except Exception:
            return
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def objects(response: dict) -> list[dict]:
    found = []
    for text in text_items(response):
        for candidate in (text, text[text.find("{"):] if "{" in text else ""):
            try:
                found.extend(x for x in walk(json.loads(candidate)) if isinstance(x, dict))
                break
            except Exception:
                pass
    structured = response.get("result", {}).get("structuredContent")
    if isinstance(structured, (dict, list)):
        found.extend(x for x in walk(structured) if isinstance(x, dict))
    return found


def workflow_session(response: dict) -> str | None:
    for item in objects(response):
        for key in ("session_id", "sessionId"):
            if isinstance(item.get(key), str):
                return item[key]
        if isinstance(item.get("session"), dict) and isinstance(item["session"].get("id"), str):
            return item["session"]["id"]
    return None


def scrub(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    value = CPF_RE.sub("[removido]", value)
    value = PHONE_RE.sub("[removido]", value)
    value = JID_RE.sub("[removido]", value)
    value = EMAIL_RE.sub("[removido]", value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".todoist-", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def collect() -> dict:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("composio_key_missing")
    init, transport = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ifc-todoist-collector", "version": "1"}}}, key)
    if "error" in init:
        raise ValueError("composio_initialize_failed")
    search, transport = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "COMPOSIO_SEARCH_TOOLS", "arguments": {"queries": [{"use_case": "list all open incomplete Todoist tasks across projects including future tasks and tasks without due dates, read-only"}], "session": {"generate_id": True}, "search_strategy": "auto"}}}, key, transport)
    if search.get("result", {}).get("isError") or "error" in search:
        raise ValueError("composio_search_failed")
    session = workflow_session(search)
    if not session:
        raise ValueError("composio_session_missing")
    connections, transport = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "COMPOSIO_MANAGE_CONNECTIONS", "arguments": {"toolkits": [{"name": "todoist", "action": "list"}], "session_id": session}}}, key, transport)
    if connections.get("result", {}).get("isError") or "error" in connections:
        raise ValueError("composio_connections_failed")
    account_ids = []
    for item in objects(connections):
        status = str(item.get("status", "active")).casefold()
        # The toolkit-scoped connection listing returns account_type=PRIVATE;
        # nested Todoist user/task objects can also have an id, so ignore them.
        account_type = item.get("account_type")
        if isinstance(item.get("id"), str) and isinstance(account_type, str) and status in {"active", "connected", "confirmed"}:
            account_ids.append(item["id"])
    account_ids = list(dict.fromkeys(account_ids))
    if len(account_ids) != 1:
        raise ValueError("composio_todoist_account_ambiguous")
    rows = []
    cursor = None
    seen_cursors = set()
    for page_number in range(100):
        arguments = {"limit": 100, "lang": "pt"}
        if cursor:
            arguments["cursor"] = cursor
        execution = {"tools": [{"tool_slug": "TODOIST_GET_ALL_TASKS", "account": account_ids[0], "arguments": arguments}], "sync_response_to_workbench": False, "current_step": "READING_TODOIST_OPEN_TASKS", "thought": "Read every open Todoist task, including tasks without a due date, read-only.", "session_id": session}
        response, _ = rpc({"jsonrpc": "2.0", "id": 4 + page_number, "method": "tools/call", "params": {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "arguments": execution}}, key, transport)
        if response.get("result", {}).get("isError") or "error" in response:
            raise ValueError("composio_todoist_call_failed")
        page_objects = objects(response)
        rows.extend(page_objects)
        next_cursor = next((item.get("nextCursor") or item.get("next_cursor") for item in page_objects
                            if isinstance(item.get("nextCursor") or item.get("next_cursor"), str)), None)
        if not next_cursor:
            page_task_ids = {str(item["id"]) for item in page_objects
                             if isinstance(item.get("id"), (str, int)) and isinstance(item.get("content"), str)}
            page_task_count = len(page_task_ids)
            # Follow the provider cursor until absent. Its total_count metadata
            # is not a task count in the Composio envelope.
            if page_task_count >= 100:
                raise ValueError("composio_todoist_pagination_incomplete")
            break
        if next_cursor in seen_cursors:
            raise ValueError("composio_todoist_pagination_invalid")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise ValueError("composio_todoist_pagination_limit")
    unique = {}
    today = datetime.now(ZONE).date()
    for item in rows:
        if not isinstance(item.get("id"), (str, int)) or not isinstance(item.get("content"), str):
            continue
        if item.get("checked") in (True, 1, "1") or item.get("is_deleted") in (True, 1, "1"):
            continue
        due = item.get("due") if isinstance(item.get("due"), dict) else {}
        deadline = item.get("deadline") if isinstance(item.get("deadline"), dict) else {}
        due_value = due.get("datetime") or due.get("date") or item.get("due_date")
        due_day = None
        due_time = None
        if due_value:
            try:
                parsed_due = datetime.fromisoformat(str(due_value).replace("Z", "+00:00"))
                if parsed_due.tzinfo is None:
                    parsed_due = parsed_due.replace(tzinfo=ZONE)
                else:
                    parsed_due = parsed_due.astimezone(ZONE)
                due_day = parsed_due.date()
                if "T" in str(due_value):
                    due_time = parsed_due.strftime("%H:%M")
            except ValueError:
                try:
                    due_day = datetime.strptime(str(due_value)[:10], "%Y-%m-%d").date()
                except ValueError:
                    due_day = None
        deadline_day = None
        deadline_value = deadline.get("date")
        if isinstance(deadline_value, str):
            try:
                deadline_day = datetime.strptime(deadline_value[:10], "%Y-%m-%d").date()
            except ValueError:
                deadline_day = None
        effective_day = due_day or deadline_day
        content = scrub(item.get("content"))
        if not content:
            continue
        if effective_day is None:
            due_description = scrub(due.get("string"), 100) if isinstance(due, dict) else ""
            state = "sem_prazo" if not due_description else "prazo_nao_interpretado"
        elif effective_day < today:
            due_description = ""
            state = "atrasada"
        elif effective_day == today:
            due_description = ""
            state = "hoje"
        else:
            due_description = scrub(due.get("string"), 100) if isinstance(due, dict) else ""
            state = "futura"
        duration = item.get("duration") if isinstance(item.get("duration"), dict) else {}
        duration_amount = duration.get("amount")
        duration_unit = duration.get("unit")
        duration_labels = {"minute": "minuto", "minutes": "minutos", "day": "dia", "days": "dias"}
        duration_text = (f"{int(duration_amount)} {duration_labels[duration_unit]}" if str(duration_amount).isdigit()
                         and duration_unit in duration_labels else "")
        labels = item.get("labels") if isinstance(item.get("labels"), list) else []
        unique[str(item["id"])] = {"content": content, "due_date": due_day.isoformat() if due_day else None,
                                   "due_time": due_time, "due_description": due_description,
                                   "deadline_date": deadline_day.isoformat() if deadline_day else None,
                                   "deadline_description": scrub(deadline.get("date"), 50) if deadline_day else "",
                                   "description": scrub(item.get("description"), 1200),
                                   "labels": [scrub(label, 80) for label in labels if isinstance(label, str) and scrub(label, 80)],
                                   "is_recurring": due.get("is_recurring") is True,
                                   "duration": duration_text, "has_parent": bool(item.get("parent_id")),
                                   "status": state,
                                   "priority": int(item.get("priority", 1)) if str(item.get("priority", 1)).isdigit() else 1}
    tasks = sorted(unique.values(), key=lambda x: (x["due_date"] or "9999-12-31", -x["priority"], x["content"].casefold()))
    reported_total = None
    generated = datetime.now(ZONE).replace(microsecond=0).isoformat()
    payload = {"generated_at": generated, "tasks": tasks, "coverage_complete": True, "pages_read": page_number + 1,
               "total_open_tasks": len(tasks), "reported_total": reported_total}
    atomic_write(OUTPUT, payload)
    atomic_write(WEB_OUTPUT, payload)
    return {"status": "ok", "tasks": len(tasks), "pages": page_number + 1, "coverage_complete": True}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    except Exception as error:
        allowed = {"composio_key_missing", "composio_initialize_failed", "composio_search_failed", "composio_session_missing", "composio_connections_failed", "composio_todoist_account_ambiguous", "composio_todoist_call_failed", "composio_todoist_pagination_incomplete", "composio_todoist_pagination_invalid", "composio_todoist_pagination_limit", "composio_request_failed", "composio_response_invalid"}
        reason = str(error)
        print(json.dumps({"status": "failed", "reason": reason if reason in allowed else "composio_todoist_failed"}, ensure_ascii=False))
        raise SystemExit(1)
