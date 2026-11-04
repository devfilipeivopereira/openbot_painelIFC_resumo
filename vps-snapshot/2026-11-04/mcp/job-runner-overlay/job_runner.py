#!/usr/bin/env python3
"""Fail-closed IFC job entrypoint; no shell, source, or arguments are accepted."""
from __future__ import annotations

import unicodedata
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("IFC_JOB_ROOT", "/var/lib/ifc-job/data"))
STATE = Path(os.environ.get("IFC_JOB_STATE", "/var/lib/ifc-job/state.json"))
LOCK = Path(os.environ.get("IFC_JOB_LOCK", "/var/lib/ifc-job/run.lock"))
PANEL_OUTPUT = Path(os.environ.get("IFC_PANEL_OUTPUT", "/run/publish/dados.json"))
PANEL_CURATION_STATUS = PANEL_OUTPUT.parent / "curation-status.json"

# Every source remains unavailable until it has a dedicated, reviewed collector
# and a corresponding least-privilege credential mount. Configuration is
# described by filenames only; secret values are never parsed here.
REQUIRED_SOURCES = (
    "evolution_grupos", "evolution_mulheres", "mensagens_diretas", "agenda",
    "email_secretaria", "todoist", "notion", "instagram", "youtube", "visitantes",
    "n8n", "financeiro", "drive_secretaria", "planilhas", "ata",
)
CONFIG_FILE = Path(os.environ.get("IFC_JOB_CONFIG", "/etc/ifc-job/sources.json"))
READ_ONLY_SOURCES = (
    "evolution_grupos", "evolution_mulheres", "mensagens_diretas", "agenda",
    "email_secretaria", "todoist", "instagram", "youtube", "visitantes",
    "n8n", "financeiro",
)
EXCLUDED_SOURCES = {
    "ata": "restricted_data_not_for_agent_processing",
    "planilhas": "covered_by_specialized_collectors",
    # Keep the gap visible, but do not block daily IFC sources until an IFC
    # Notion database and a reviewed read-only collector are confirmed.
    "notion": "no_ifc_database_confirmed",
}
SAFE_REASON_PATTERNS = {
    "evolution_grupos": re.compile(r"^evolution_[a-z0-9_]{1,72}$"),
    "evolution_mulheres": re.compile(r"^evolution_[a-z0-9_]{1,72}$"),
    "agenda": re.compile(r"^composio_[a-z0-9_]{1,72}$"),
    "email_secretaria": re.compile(r"^composio_[a-z0-9_]{1,72}$"),
    "todoist": re.compile(r"^composio_[a-z0-9_]{1,72}$"),
    "instagram": re.compile(r"^(?:composio|instagram)_[a-z0-9_]{1,72}$"),
    "youtube": re.compile(r"^youtube_[a-z0-9_]{1,72}$"),
}
IMPLEMENTED_COLLECTORS = {
    "youtube": "/app/collectors/youtube_feed.py",
    "instagram": "/app/collectors/composio_instagram.py",
    "visitantes": "/app/collectors/supabase_visitors.py",
    "drive_secretaria": "/app/collectors/composio_drive.py",
    "evolution_grupos": "/app/collectors/evolution_groups.py",
    "evolution_mulheres": "/app/collectors/evolution_women.py",
    "mensagens_diretas": "/app/collectors/evolution_direct.py",
    "agenda": "/app/collectors/composio_calendar.py",
    "email_secretaria": "/app/collectors/composio_outlook.py",
    "todoist": "/app/collectors/composio_todoist.py",
    "n8n": "/app/collectors/n8n_status.py",
    "financeiro": "/app/collectors/supabase_finance.py",
}
PUBLISHER_CONFIGURED = Path("/app/dashboard_publisher.py").is_file() and PANEL_OUTPUT.parent.is_dir()
CURATION_FILE = Path(os.environ.get("IFC_CURATION_FILE", "/var/lib/ifc-job/data/curation/ifc-curation.json"))

CURATION_REQUIRED_SECTIONS = {"drive_secretaria": "drive_secretaria", "instagram": "instagram", "evolution_grupos": "grupos", "evolution_mulheres": "mulheres", "mensagens_diretas": "mensagens_diretas", "email_secretaria": "email_secretaria"}
CURATION_MODELS = {"gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra", "gpt-6-sol", "tudo", "freeai/tudo"}
CURATION_FIELDS = ("temas", "fatos", "decisoes", "pedidos", "riscos", "oportunidades", "proximos_passos", "insights")
QUESTION_SOURCE_FILES = {"grupos": "evolution_grupos.json", "mulheres": "evolution_mulheres.json",
                        "mensagens_diretas": "mensagens_diretas.json", "email_secretaria": "email_secretaria.json"}
QUESTION_REVIEW_FIELDS = {"actionable_open": {"pedidos", "proximos_passos"}, "answered": {"fatos", "decisoes"},
                          "informational": {"temas", "fatos"}, "rhetorical": {"temas", "fatos"}}
REQUEST_REVIEW_FIELDS = {"actionable_open": {"pedidos", "proximos_passos"}, "answered": {"fatos", "decisoes"},
                         "informational": {"temas", "fatos"}, "not_a_request": {"temas", "fatos"}}
REQUEST_CUES = re.compile(
    r"\b(?:por favor|preciso\s+(?:de|que)|precisamos\s+(?:de|que)|poderia(?:m)?|solicito|"
    r"favor\s+(?:enviar|fazer|confirmar|verificar|avisar|mandar|preparar|organizar|compartilhar|"
    r"encaminhar|atualizar|incluir|colocar|registrar|responder)|tem como|tem(?:os)?\s+(?:que|de)|"
    r"gostaria\s+(?:de|que)|alguem pode|quem pode|me ajuda|pode me (?:enviar|ajudar|confirmar|avisar|mandar|"
    r"encaminhar|compartilhar|responder)|nao esqueca(?:m)?|lembre-se|lembrem-se|nao deixe de|"
    r"envie|enviem|manda|mandem|compartilhe|compartilhem|encaminhe|encaminhem|confirme|confirmem|"
    r"avise|avisem|verifique|verifiquem|prepare|preparem|organize|organizem|reserve|reservem|"
    r"agende|agendem|registre|registrem|inclua|incluam|coloque|coloquem|atualize|atualizem|"
    r"responda|respondam|ligue|liguem|faca|facam)\b", re.IGNORECASE)
REVIEW_PII_PATTERNS = (
    re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)"),
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)", re.I),
    re.compile(r"(?<!\w)@\d{10,}(?!\d)"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
)


def curation_evidence_complete(payload: dict, required: set[str]) -> bool:
    evidence = payload.get("evidence")
    sections = payload.get("sections")
    coverage = payload.get("record_coverage")
    if not isinstance(evidence, list) or not isinstance(sections, dict) or not isinstance(coverage, dict):
        return False
    expected = set()
    for source in required:
        section = sections.get(source)
        if not isinstance(section, dict):
            return False
        for field in CURATION_FIELDS:
            items = section.get(field)
            if not isinstance(items, list):
                return False
            expected.update((source, field, index) for index in range(len(items)))
    seen = set()
    for item in evidence:
        required_keys = {"source", "field", "item_index", "record_index", "quote"}
        if (not isinstance(item, dict) or not required_keys <= set(item)
                or set(item) - required_keys - {"context"}
                or ("context" in item and not isinstance(item["context"], dict))):
            return False
        source, field = item.get("source"), item.get("field")
        item_index, record_index, quote = item.get("item_index"), item.get("record_index"), item.get("quote")
        if (source not in required or field not in CURATION_FIELDS or type(item_index) is not int
                or type(record_index) is not int or not isinstance(quote, str) or not 6 <= len(quote) <= 300):
            return False
        source_coverage = coverage.get(source)
        if not isinstance(source_coverage, dict):
            return False
        total = source_coverage.get("total")
        target = sections[source].get(field)
        if (type(total) is not int or total < 0 or not 0 <= record_index < total
                or not isinstance(target, list) or not 0 <= item_index < len(target)):
            return False
        marker = (source, field, item_index)
        if marker in seen:
            return False
        seen.add(marker)
    return seen == expected


def curation_reviews_complete(payload: dict, required: set[str]) -> bool:
    questions = payload.get("question_review")
    requests = payload.get("request_review")
    if not isinstance(questions, list) or not isinstance(requests, list):
        return False
    required_sources = set(QUESTION_SOURCE_FILES) & required
    expected_questions = 0
    expected_requests = 0
    for source in required_sources:
        try:
            staged = json.loads((ROOT / "staging" / QUESTION_SOURCE_FILES[source]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        records = staged.get("messages") if isinstance(staged, dict) else None
        if not isinstance(records, list):
            return False
        for record in records:
            text = record.get("text", "") if isinstance(record, dict) else ""
            if not isinstance(text, str):
                continue
            expected_questions += text.count("?")
            normalized = unicodedata.normalize("NFKD", text.replace("—", "-").replace("–", "-").replace("’", "'").casefold())
            normalized = "".join(char for char in normalized if not unicodedata.combining(char))
            expected_requests += len(REQUEST_CUES.findall(normalized))
    if len(questions) != expected_questions or len(requests) != expected_requests:
        return False

    sections = payload.get("sections")
    coverage = payload.get("record_coverage")
    if not isinstance(sections, dict) or not isinstance(coverage, dict):
        return False
    for collection, index_name, allowed in ((questions, "question_index", QUESTION_REVIEW_FIELDS),
                                             (requests, "request_index", REQUEST_REVIEW_FIELDS)):
        seen = set()
        for review in collection:
            expected_keys = {"source", "record_index", index_name, "disposition", "field", "item_index", "quote"}
            if not isinstance(review, dict) or set(review) != expected_keys:
                return False
            source, record_index = review.get("source"), review.get("record_index")
            review_index, disposition = review.get(index_name), review.get("disposition")
            field, item_index, quote = review.get("field"), review.get("item_index"), review.get("quote")
            source_coverage = coverage.get(source) if isinstance(source, str) else None
            section = sections.get(source) if isinstance(sections, dict) and isinstance(source, str) else None
            if (source not in required_sources or type(record_index) is not int or record_index < 0
                    or type(review_index) is not int or review_index < 0 or type(item_index) is not int
                    or not isinstance(source_coverage, dict) or type(source_coverage.get("total")) is not int
                    or record_index >= source_coverage["total"] or not isinstance(disposition, str)
                    or field not in allowed.get(disposition, set()) or not isinstance(quote, str) or not quote
                    or len(quote) > 300 or any(pattern.search(quote) for pattern in REVIEW_PII_PATTERNS)
                    or not isinstance(section, dict) or not isinstance(section.get(field), list)
                    or not 0 <= item_index < len(section[field]) or not isinstance(section[field][item_index], str)
                    or not section[field][item_index].strip()):
                return False
            identity = (source, record_index, review_index)
            if identity in seen:
                return False
            seen.add(identity)
    return True


def curation_readiness_issue() -> str | None:
    """Return a safe, non-content reason when the current model output is not ready."""
    try:
        payload = json.loads(CURATION_FILE.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
        if not 0 <= age <= 24 * 60 * 60 or payload.get("model_claimed_by_agent") not in CURATION_MODELS or payload.get("reasoning_claimed_by_agent") not in {"high", "xhigh"}:
            return "curation_stale_or_model_invalid"
        sections = payload.get("sections")
        if not isinstance(sections, dict):
            return "sections_missing"
        required = {section for source, section in CURATION_REQUIRED_SECTIONS.items()
                    if stage_is_current(load_stage_for_runner(source), datetime.now().astimezone())}
        optional_youtube=stage_is_current(load_stage_for_runner("youtube"),datetime.now().astimezone())
        if optional_youtube:required.add("youtube")
        expected_timestamps = {source: load_stage_for_runner(source).get("generated_at", load_stage_for_runner(source).get("checked_at"))
                               for source in CURATION_REQUIRED_SECTIONS if stage_is_current(load_stage_for_runner(source), datetime.now().astimezone())}
        if optional_youtube:expected_timestamps["youtube"]=load_stage_for_runner("youtube").get("generated_at",load_stage_for_runner("youtube").get("checked_at"))
        if payload.get("source_timestamps") != expected_timestamps:
            return "source_snapshot_mismatch"
        coverage = payload.get("record_coverage")
        if not isinstance(coverage, dict) or set(coverage) != required:
            return "record_coverage_incomplete"
        for name in required:
            item = coverage.get(name)
            if (not isinstance(item, dict) or type(item.get("covered")) is not int
                    or type(item.get("total")) is not int or item["covered"] != item["total"]):
                return "record_coverage_incomplete"
        if not required or any(not isinstance(sections.get(name), dict) for name in required):
            return "sections_missing"
        for name in required:
            content = sections[name]
            if not isinstance(content.get("sintese"), str) or not content["sintese"].strip():
                return "summary_incomplete"
            for field in CURATION_FIELDS:
                if not isinstance(content.get(field), list):
                    return "section_incomplete"
        if not curation_evidence_complete(payload, required):
            return "evidence_incomplete"
        if not curation_reviews_complete(payload, required):
            return "question_request_review_incomplete"
        return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "validation_error"


def curation_ready() -> bool:
    """Accept only a sanitized, current model output with every editorial source."""
    return curation_readiness_issue() is None


def load_stage_for_runner(source: str) -> dict | None:
    filename = {"drive_secretaria": "drive_secretaria.json", "instagram": "instagram.json", "evolution_grupos": "evolution_grupos.json", "evolution_mulheres": "evolution_mulheres.json",
                "mensagens_diretas": "mensagens_diretas.json", "email_secretaria": "email_secretaria.json"}.get(source)
    if source=="youtube":filename="youtube.json"
    if not filename:
        return None
    try:
        value = json.loads((ROOT / "staging" / filename).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def stage_is_current(value: dict | None, now_local: datetime) -> bool:
    if not isinstance(value, dict):
        return False
    stamp = value.get("generated_at", value.get("checked_at"))
    if not isinstance(stamp, str):
        return False
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return -300 <= age <= 24 * 60 * 60
    except ValueError:
        return False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_panel_curation_status(status_value: str, reason_code: str | None = None) -> None:
    """Publish a tiny private curation-state marker beside the panel JSON."""
    if status_value not in {"curation_not_ready", "curation_ready", "published"}:
        return
    try:
        PANEL_CURATION_STATUS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = PANEL_CURATION_STATUS.with_name(f".{PANEL_CURATION_STATUS.name}.{os.getpid()}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                status = {"status": status_value, "checked_at": now()}
                if status_value == "curation_not_ready":
                    if reason_code not in {"curation_stale_or_model_invalid", "sections_missing", "source_snapshot_mismatch",
                                           "record_coverage_incomplete", "summary_incomplete", "section_incomplete",
                                           "evidence_incomplete", "question_request_review_incomplete", "validation_error"}:
                        try:
                            previous = json.loads(PANEL_CURATION_STATUS.read_text(encoding="utf-8"))
                            reason_code = previous.get("reason_code") if previous.get("status") == status_value else None
                        except (OSError, ValueError, TypeError):
                            reason_code = None
                    if reason_code:
                        status["reason_code"] = reason_code
                json.dump(status, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, PANEL_CURATION_STATUS)
            os.chmod(PANEL_CURATION_STATUS, 0o600)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
    except OSError:
        return


def sync_panel_curation_status() -> dict:
    current = status()
    if current.get("ai_curation_ready") is not True:
        state_value = "curation_not_ready"
        reason_code = curation_readiness_issue()
    elif current.get("published") is True:
        state_value = "published"
        reason_code = None
    else:
        state_value = "curation_ready"
        reason_code = None
    write_panel_curation_status(state_value, reason_code)
    return {"status": state_value, "ai_curation_ready": current.get("ai_curation_ready") is True}


def unavailable_sources(reason: str = "not_configured") -> dict[str, str]:
    return {
        name: EXCLUDED_SOURCES.get(name, reason)
        for name in REQUIRED_SOURCES
    }


def load_state() -> dict:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "never_run", "steps": {}}


def configuration() -> tuple[list[str], dict[str, str]]:
    """Return enabled source names and generic failures, never config values."""
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], unavailable_sources()
    except (json.JSONDecodeError, OSError):
        return [], unavailable_sources("invalid_configuration")
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return [], unavailable_sources("invalid_configuration")
    sources = raw.get("sources")
    if not isinstance(sources, dict):
        return [], unavailable_sources("invalid_configuration")
    enabled: list[str] = []
    issues: dict[str, str] = {}
    for name in REQUIRED_SOURCES:
        if name in EXCLUDED_SOURCES:
            issues[name] = EXCLUDED_SOURCES[name]
            continue
        entry = sources.get(name)
        if not isinstance(entry, dict) or entry.get("enabled") is not True:
            issues[name] = "not_configured"
            continue
        # The config must only reference absolute secret-file paths; this is
        # presence validation, not secret loading or collector execution.
        secret_files = entry.get("secret_files", [])
        if not isinstance(secret_files, list) or any(
            not isinstance(p, str) or not p.startswith("/run/secrets/")
            or not Path(p).is_file() for p in secret_files
        ):
            issues[name] = "missing_secret_mount"
            continue
        collector = entry.get("collector")
        expected_collector = IMPLEMENTED_COLLECTORS.get(name)
        if expected_collector is None:
            issues[name] = "executor_not_implemented"
            continue
        if not isinstance(collector, str) or collector != expected_collector:
            issues[name] = "collector_not_allowlisted"
            continue
        if not Path(collector).is_file():
            issues[name] = "collector_missing"
            continue
        enabled.append(name)
        issues[name] = "configured"
    return enabled, issues


def optional_capabilities() -> dict[str, dict[str, dict]]:
    """Report optional source features without disabling the base collector."""
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        sources = raw.get("sources", {}) if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, dict[str, dict]] = {}
    root = ROOT.resolve()
    for source, entry in sources.items() if isinstance(sources, dict) else ():
        capabilities = entry.get("optional_capabilities", {}) if isinstance(entry, dict) else {}
        if not isinstance(capabilities, dict):
            continue
        for capability, spec in capabilities.items():
            if not isinstance(spec, dict):
                continue
            missing: list[str] = []
            secret_files = spec.get("secret_files", [])
            data_files = spec.get("data_files", [])
            if not isinstance(secret_files, list):
                missing.append("secret_configuration_invalid")
                secret_files = []
            for path in secret_files:
                if (not isinstance(path, str) or not path.startswith("/run/secrets/")
                        or not Path(path).is_file() or not os.access(path, os.R_OK)):
                    missing.append("secret:" + (Path(path).name if isinstance(path, str) else "invalid"))
            if not isinstance(data_files, list):
                missing.append("data_configuration_invalid")
                data_files = []
            for relative in data_files:
                if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    missing.append("data:invalid")
                    continue
                target = (root / relative).resolve()
                if not target.is_relative_to(root) or not target.is_file() or not os.access(target, os.R_OK):
                    missing.append("data:" + str(relative))
            paused = False
            cooldown_file = spec.get("cooldown_file")
            if isinstance(cooldown_file, str) and not Path(cooldown_file).is_absolute() and ".." not in Path(cooldown_file).parts:
                cooldown = (root / cooldown_file).resolve()
                if cooldown.is_relative_to(root) and cooldown.is_file():
                    try:
                        value = json.loads(cooldown.read_text(encoding="utf-8"))
                        until = datetime.fromisoformat(str(value.get("retry_after", "") if isinstance(value, dict) else "").replace("Z", "+00:00"))
                        paused = until.tzinfo is not None and until > datetime.now(timezone.utc)
                    except (OSError, json.JSONDecodeError, TypeError, ValueError):
                        pass
            result.setdefault(str(source), {})[str(capability)] = {
                "ready": not missing, "missing_dependencies": missing, "temporarily_paused": paused,
            }
    return result


def atomic_state(data: dict) -> None:
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, STATE)
        os.chmod(STATE, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def safe_failure_detail(source: str, summary: object) -> dict:
    """Persist only allowlisted reason codes and numeric HTTP statuses."""
    detail = {}
    if not isinstance(summary, dict):
        return detail
    reason = summary.get("reason")
    pattern = SAFE_REASON_PATTERNS.get(source)
    if isinstance(reason, str) and pattern and pattern.fullmatch(reason):
        detail["failure_code"] = reason
    elif source == "mensagens_diretas" and isinstance(reason, str):
        if reason == "Evolution direct-message collector failed":
            detail["failure_code"] = "evolution_direct_collector_failed"
        elif reason.startswith("Evolution direct reader returned HTTP "):
            status = reason.removeprefix("Evolution direct reader returned HTTP ")
            if status.isdigit() and 400 <= int(status) <= 599:
                detail["failure_code"] = "evolution_direct_reader_http_error"
                detail["http_status"] = int(status)
    elif source == "mensagens_diretas" and reason == "Evolution audio recovery failed after 3 attempts":
        detail["failure_code"] = "evolution_audio_unavailable_after_3_attempts"
    http_status = summary.get("http_status")
    if "http_status" not in detail and type(http_status) is int and 400 <= http_status <= 599:
        detail["http_status"] = http_status
    return detail


def status() -> dict:
    state = load_state()
    enabled, issues = configuration()
    state.setdefault("status", "blocked_missing_sources")
    last_steps = state.get("steps", {}) if isinstance(state.get("steps"), dict) else {}
    current_steps = {}
    for name in REQUIRED_SOURCES:
        prior = last_steps.get(name)
        if name in enabled:
            prior_status = prior.get("status") if isinstance(prior, dict) else None
            current_steps[name] = {"status": prior_status if prior_status in {"succeeded", "failed", "running"} else "configured"}
            if isinstance(prior, dict) and current_steps[name]["status"] == prior_status:
                for metric in ("groups", "messages", "workflows", "errors", "events", "emails", "tasks", "participants", "audio_skipped_after_retries"):
                    if isinstance(prior.get(metric), int):
                        current_steps[name][metric] = prior[metric]
                if prior.get("participants_status") in {"current", "unavailable"}:
                    current_steps[name]["participants_status"] = prior["participants_status"]
                if isinstance(prior.get("failure_code"), str):
                    current_steps[name]["failure_code"] = prior["failure_code"]
                if type(prior.get("http_status")) is int:
                    current_steps[name]["http_status"] = prior["http_status"]
        else:
            current_steps[name] = {"status": issues.get(name, "not_configured")}
    state["steps"] = current_steps
    state["available_sources"] = enabled
    state["optional_capabilities"] = optional_capabilities()
    state["missing_sources"] = [name for name in READ_ONLY_SOURCES if name not in enabled]
    state["unconfigured_sources"] = {
        name: issues.get(name, "executor_not_implemented")
        for name in REQUIRED_SOURCES if name not in enabled
    }
    state["runner_configured"] = all(name in enabled for name in READ_ONLY_SOURCES) and PUBLISHER_CONFIGURED
    state["publisher_configured"] = PUBLISHER_CONFIGURED
    state["ai_curation_configured"] = Path("/app/mcp_gateway.py").is_file()
    state["ai_curation_ready"] = curation_ready()
    if not state["ai_curation_ready"]:
        state["published"] = False
        if state.get("status") in {"complete", "partial_success"}:
            state["status"] = "curation_not_ready"
    state["read_only_sources"] = list(READ_ONLY_SOURCES)
    state["excluded_sources"] = EXCLUDED_SOURCES
    if not state["runner_configured"] and state.get("status") in {"never_run", "complete", "blocked_missing_sources"}:
        state["status"] = "blocked_missing_sources"
    finished_at=state.get("finished_at")
    try:
        finished=datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        if finished.tzinfo is None:finished=finished.replace(tzinfo=timezone.utc)
        recent_run=0 <= (datetime.now(timezone.utc)-finished.astimezone(timezone.utc)).total_seconds() <= 24*60*60
    except (TypeError,ValueError):
        recent_run=False
    required_steps_succeeded=all(current_steps.get(name,{}).get("status")=="succeeded" for name in READ_ONLY_SOURCES)
    if (recent_run and state.get("runner_configured") and required_steps_succeeded
            and state.get("published") is True and state.get("ai_curation_ready") is True):
        state["status"]="complete"
    return state


def start() -> tuple[dict, int]:
    enabled, issues = configuration()
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running"}, 0
        started = now()
        step_results = {name: {"status": issues.get(name, "executor_not_implemented")}
                        for name in REQUIRED_SOURCES}
        if not enabled:
            result = {
                "status": "blocked_missing_sources", "started_at": started,
                "finished_at": now(), "published": False, "steps": step_results,
                "reason": "Nenhum coletor revisado está configurado; os dados publicados foram preservados.",
            }
            atomic_state(result)
            return result, 4

        failures = []
        for name in enabled:
            collector = IMPLEMENTED_COLLECTORS[name]
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TZ": "America/Sao_Paulo",
                "IFC_JOB_ROOT": str(ROOT),
                "EVOLUTION_KEY_FILE": "/run/secrets/evolution-apikey",
                "EVOLUTION_GROUPS_FILE": "/run/secrets/evolution-groups.json",
                "EVOLUTION_GLOBAL_HEADER_FILE": "/run/secrets/evolution-global.h",
                "EVOLUTION_WOMEN_FILE": "/run/secrets/evolution-women.json",
                "IFC_DIRECT_CONTACTS_FILE": "/run/secrets/ifc-direct-contacts.json",
                "IFC_DIRECT_READER_TOKEN_FILE": "/run/secrets/ifc-direct-reader.token",
                "IFC_DIRECT_READER_URL": "http://ifc-direct-db-reader:8790/v1/direct-messages",
                "IFC_GROUP_READER_TOKEN_FILE": "/run/secrets/ifc-direct-reader.token",
                "IFC_GROUP_READER_URL": "http://ifc-direct-db-reader:8790/v1/group-messages",
                "IFC_GROUP_PARTICIPANT_NAMES_URL": "http://ifc-direct-db-reader:8790/v1/group-participant-names",
                "N8N_HEADER_FILE": "/run/secrets/n8n.h",
                "COMPOSIO_KEY_FILE": "/run/secrets/composio.key",
                "IFC_TODOIST_PANEL_OUTPUT": "/run/publish/todoist.json",
                "IFC_FINANCE_URL": "http://ifc-db-gateway:8788/v1/finance/summary",
                "IFC_FINANCE_TOKEN_FILE": "/run/secrets/finance-gateway.token",
            }
            try:
                proc = subprocess.run(
                    [sys.executable, collector], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=1800 if name in {"evolution_grupos", "evolution_mulheres", "mensagens_diretas"} else 600,
                    check=False, env=env, cwd="/",
                )
                summary = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
                audio_untranscribed = int(summary.get("audio_untranscribed", 0)) if isinstance(summary, dict) else 0
                if not isinstance(summary, dict) or proc.returncode != 0 or summary.get("status") != "ok":
                    step_results[name] = {"status": "failed", **safe_failure_detail(name, summary)}
                    failures.append(name)
                else:
                    step_results[name] = {
                        "status": "succeeded",
                        "groups": int(summary.get("groups", 0)),
                        "files": int(summary.get("files", 0)),
                        "metadata_only": bool(summary.get("metadata_only", False)),
                        "messages": int(summary.get("messages", 0)),
                        "workflows": int(summary.get("workflows", 0)),
                        "errors": int(summary.get("errors", 0)),
                        "events": int(summary.get("events", 0)),
                        "emails": int(summary.get("emails", 0)),
                "tasks": int(summary.get("tasks", 0)),
                        "participants": int(summary.get("participants", 0)),
                        "participants_status": summary.get("participants_status") if summary.get("participants_status") in {"current", "unavailable"} else "unavailable",
                        "women_messages": int(summary.get("women_messages", 0)),
                        "audio_transcribed": int(summary.get("audio_transcribed", 0)),
                        "audio_untranscribed": int(summary.get("audio_untranscribed", 0)),
                    }
                    if name in {"evolution_grupos", "evolution_mulheres", "mensagens_diretas"} and audio_untranscribed > 0:
                        step_results[name]["audio_skipped_after_retries"] = audio_untranscribed
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
                step_results[name] = {"status": "failed"}
                failures.append(name)

        missing = [name for name in READ_ONLY_SOURCES if name not in enabled]
        publish_result = {"status": "not_configured", "published": False}
        ai_curation_ok = curation_ready()
        # The IFC guides say to preserve the last known data and mark a failed
        # or unavailable source. Do not freeze the whole daily panel because a
        # non-editorial collector is missing; only require current curation and
        # let the publisher mark individual source freshness.
        if PUBLISHER_CONFIGURED and ai_curation_ok and os.environ.get("IFC_DEFER_PANEL_PUBLICATION") != "1":
            try:
                proc = subprocess.run(
                    [sys.executable, "/app/dashboard_publisher.py"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=30, check=False,
                    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                         "TZ": "America/Sao_Paulo", "IFC_JOB_ROOT": str(ROOT),
                         "IFC_PANEL_OUTPUT": str(PANEL_OUTPUT)}, cwd="/",
                )
                publish_result = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
                if proc.returncode != 0 or publish_result.get("status") != "ok" or publish_result.get("published") is not True:
                    publish_result = {"status": "failed", "published": False}
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
                publish_result = {"status": "failed", "published": False}
        step_results["ai_curation"] = {"status": "succeeded" if ai_curation_ok else "pending_high_capability_model"}
        step_results["publisher"] = {"status": "succeeded" if publish_result.get("published") else ("blocked_incomplete_sources_or_curation" if publish_result.get("status") == "not_configured" else publish_result.get("status", "failed"))}
        result = {
            "status": "source_collection_failed" if failures else ("partial_success" if missing else "complete"),
            "started_at": started,
            "finished_at": now(),
            "published": bool(publish_result.get("published")),
            "steps": step_results,
            "available_sources": enabled,
            "optional_capabilities": optional_capabilities(),
            "missing_sources": missing,
            "publisher": publish_result,
            "ai_curation": {"status": "verified" if ai_curation_ok else "pending_high_capability_model"},
            "runner_configured": all(name in enabled for name in READ_ONLY_SOURCES) and PUBLISHER_CONFIGURED,
            "publisher_configured": PUBLISHER_CONFIGURED,
            "ai_curation_configured": Path("/app/mcp_gateway.py").is_file(),
            "ai_curation_ready": curation_ready(),
            "read_only_sources": list(READ_ONLY_SOURCES),
            "excluded_sources": EXCLUDED_SOURCES,
            "reason": ("Atualização parcial publicada; fontes ausentes ou com falha estão identificadas no painel." if publish_result.get("published") and (missing or failures)
                       else "Atualização completa publicada." if publish_result.get("published")
                       else "Publicação bloqueada até a curadoria detalhada por modelo de alta capacidade."),
        }
        atomic_state(result)
        issue = curation_readiness_issue() if result.get("ai_curation_ready") is not True else None
        write_panel_curation_status(
            "published" if result.get("published") and result.get("ai_curation_ready") else
            "curation_ready" if result.get("ai_curation_ready") else "curation_not_ready",
            issue
        )
        return result, 0 if not failures else 5
    finally:
        os.close(fd)


def publish_curated() -> tuple[dict, int]:
    if not curation_ready():
        write_panel_curation_status("curation_not_ready", curation_readiness_issue())
        return {"status": "blocked_missing_fresh_model_curation", "published": False}, 6
    proc = subprocess.run(
        [sys.executable, "/app/dashboard_publisher.py"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40, check=False,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
             "TZ": "America/Sao_Paulo", "IFC_JOB_ROOT": str(ROOT), "IFC_PANEL_OUTPUT": str(PANEL_OUTPUT)}, cwd="/",
    )
    try:
        result = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        result = {}
    if proc.returncode or result.get("published") is not True:
        return {"status": "publish_failed", "published": False}, 7
    state = load_state()
    state["published"] = True
    state["ai_curation"] = {"status": "submitted_by_openbot_model", "verified": False}
    state["finished_at"] = now()
    atomic_state(state)
    write_panel_curation_status("published")
    return {"status": "ok", "published": True, "history_saved": bool(result.get("history_saved"))}, 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"status", "start", "publish", "sync-status"}:
        print(json.dumps({"error": "usage: ifc-job status|start|publish|sync-status"}, ensure_ascii=False))
        return 2
    if sys.argv[1] == "sync-status":
        print(json.dumps(sync_panel_curation_status(), ensure_ascii=False))
        return 0
    if sys.argv[1] == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    if sys.argv[1] == "publish":
        result, code = publish_curated()
        print(json.dumps(result, ensure_ascii=False))
        return code
    result, code = start()
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
