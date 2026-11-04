#!/usr/bin/env python3
"""Small authenticated read-only web front end for the IFC panel.

Place this application beside the existing panel data directory or set
IFC_PANEL_DATA_DIR. Put it behind an HTTPS reverse proxy with Basic Auth (or
SSO). This server intentionally never returns the Ata source JSON.
"""
from __future__ import annotations

import json
import base64
import hmac
import os
import re
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("IFC_PANEL_DATA_DIR", str(APP_DIR.parent / "data"))).resolve()
HOST = os.environ.get("IFC_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("IFC_PANEL_PORT", "8787"))
DATA_FILE = Path(os.environ.get("IFC_PANEL_JSON", str(DATA_DIR / "dados.json"))).resolve()
ATA_FILE = Path(os.environ.get("IFC_PANEL_ATA_JSON", str(DATA_DIR / "integrantes_ata2026.json"))).resolve()
TODOIST_FILE = Path(os.environ.get("IFC_PANEL_TODOIST_JSON", str(DATA_DIR / "todoist.json"))).resolve()
CURATION_STATUS_FILE = Path(os.environ.get("IFC_PANEL_CURATION_STATUS_JSON", str(DATA_DIR / "curation-status.json"))).resolve()
HISTORY_DIR = Path(os.environ.get("IFC_PANEL_HISTORY_DIR", str(DATA_DIR / "history"))).resolve()
AUTH_FILE = Path(os.environ.get("IFC_PANEL_AUTH_FILE", "/run/secrets/ifc-panel-auth"))
AUTH_USER = os.environ.get("IFC_PANEL_USER", "")
AUTH_PASSWORD = os.environ.get("IFC_PANEL_PASSWORD", "")
if AUTH_FILE.exists():
    for line in AUTH_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == "IFC_PANEL_USER":
                AUTH_USER = value.strip()
            elif key.strip() == "IFC_PANEL_PASSWORD":
                AUTH_PASSWORD = value.strip()

# Secrets stay in environment/configuration, never in a checked-in file.
ATA_KEY = "__ATA2026__"
PHONE_KEY_RE = re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- .]?\d{4}(?!\w)")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
WHATSAPP_ID_RE = re.compile(r"[\w.-]+@(?:s\.whatsapp\.net|g\.us|lid)", re.I)
BLOCKED_KEYS = re.compile(r"telefone|phone|cpf|jid|whatsapp.?id|password|senha|token|secret|api.?key|contribuinte|favorecido|beneficiario|recipient", re.I)
ROSTER_PHONE_RE = re.compile(r"^[0-9]{7,15}$")
ROSTER_EVOLUTION_ID_RE = re.compile(r"^(?:[0-9]{7,20}(?:-[0-9]{5,20})?@(?:s\.whatsapp\.net|lid)|[0-9]{7,15})$", re.I)


def sanitize(value, path=()):
    """Keep only public panel-safe fields; no Ata, phone, CPF, JID or secrets."""
    if len(path) > 12:
        return None
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            roster_contact = len(path) >= 2 and path[:2] == ("participantes_grupos", "contatos")
            roster_sensitive_key = roster_contact and str(key) in {"telefone", "evolution_id"}
            if str(key) == ATA_KEY or (BLOCKED_KEYS.search(str(key)) and not roster_sensitive_key):
                continue
            # Do not publish the church's private attendance/activity workbook.
            if str(key) == "atividade_whatsapp_7d":
                continue
            cleaned = sanitize(item, path + (str(key),))
            if cleaned is not None:
                out[str(key)] = cleaned
        return out
    if isinstance(value, list):
        return [v for item in value if (v := sanitize(item, path)) is not None]
    if isinstance(value, str):
        if len(path) == 3 and path[:2] == ("participantes_grupos", "contatos"):
            if path[2] == "telefone":
                return mask_all(value, "telefone") if ROSTER_PHONE_RE.fullmatch(value) else None
            if path[2] == "evolution_id":
                return None
        value = CPF_RE.sub("[removido]", value)
        value = WHATSAPP_ID_RE.sub("[removido]", value)
        value = PHONE_KEY_RE.sub("[removido]", value)
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return None


EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
CPF_ANY_RE = re.compile(r"(?<!\d)(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})(?!\d)")
PHONE_ANY_RE = re.compile(r"(?<![\w@])(?:\+?55[\s.-]*)?\(?(\d{2})\)?[\s.-]*(9?)\s*(\d{4})[\s.-]?(\d{4})(?![\w@])")
def mask_cpf(v):
    return CPF_ANY_RE.sub(lambda m: f"***.{m.group(2)}.{m.group(3)}-**", v)
def mask_phone(v):
    return PHONE_ANY_RE.sub(lambda m: f"({m.group(1)}) {m.group(2)}****-{m.group(4)}", v)
def mask_email(v):
    return EMAIL_RE.sub(lambda m: (m.group(1)[:2] + "***@" + m.group(2)), v)
def mask_all(value, key=""):
    """Máscara aplicada na hora de servir (fonte intacta): CPF ***.456.789-**, telefone (48) 9****-1234, e-mail ab***@dominio."""
    if isinstance(value, dict):
        return {k: mask_all(v, str(k)) for k, v in value.items() if not re.search(r"evolution_id|jid|whatsapp.?id", str(k), re.I)}
    if isinstance(value, list):
        return [mask_all(v, key) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and re.search(r"cpf|telefone|phone|celular|fone", key, re.I):
        value = str(value)
    if isinstance(value, str):
        v = WHATSAPP_ID_RE.sub("[removido]", value)
        v = mask_email(v)
        if re.search(r"cpf", key, re.I) and re.fullmatch(r"\d{11}", v.strip()):
            v = "***.{}.{}-**".format(v.strip()[3:6], v.strip()[6:9])
        if re.search(r"telefone|phone|celular|fone", key, re.I) and re.fullmatch(r"\+?\d{10,13}", v.strip()):
            d = re.sub(r"\D", "", v); d = d[2:] if d.startswith("55") and len(d) > 11 else d
            return f"({d[:2]}) {d[2:-8] if len(d) > 10 else ''}****-{d[-4:]}"
        v = mask_cpf(v)
        return mask_phone(v)
    return value

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def do_GET(self):
        if not self.authenticated():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Painel IFC", charset="UTF-8"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        route = urlsplit(self.path).path
        if route == "/api/painel":
            return self.api_panel()
        if route == "/api/ata":
            return self.api_ata()
        if route == "/api/todoist":
            return self.api_todoist()
        if route == "/api/curation-status":
            return self.api_curation_status()
        if route == "/api/history":
            return self.api_history_index()
        match = re.fullmatch(r"/api/history/(\d{4}-\d{2}-\d{2})", route)
        if match:
            return self.api_history_day(match.group(1))
        path = urlsplit(self.path).path
        if path in ("/dados.json", "/integrantes_ata2026.json", "/painel_montado.tsx.txt"):
            self.send_error(404)
            return
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "private, no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")
        super().end_headers()

    def authenticated(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic ") or not AUTH_USER or not AUTH_PASSWORD:
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(username, AUTH_USER) and hmac.compare_digest(password, AUTH_PASSWORD)

    def api_panel(self):
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                data = mask_all(sanitize(json.load(f)))
            if not isinstance(data, dict):
                raise ValueError("Painel JSON root must be an object")
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            body = b'{"erro":"dados.json ainda nao foi disponibilizado"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_error(500, "Falha ao carregar os dados do painel")

    def api_ata(self):
        try:
            with ATA_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)  # Ata: dados completos só com Basic Auth (pedido do Filipe 04/11 11:52)
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, "Dados restritos da Ata indisponíveis")
        except Exception:
            self.send_error(500, "Falha ao carregar a seção restrita")

    def api_todoist(self):
        try:
            with TODOIST_FILE.open("r", encoding="utf-8") as stream:
                data = sanitize(json.load(stream))
            if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
                raise ValueError("invalid Todoist data")
            tasks = [
                {"tarefa": str(item.get("content", ""))[:240], "prazo": item.get("due_date") or item.get("deadline_date"),
                 "prazo_tipo": "Prazo" if item.get("due_date") else "Limite final" if item.get("deadline_date") else "",
                 "prazo_descricao": str(item.get("due_description", ""))[:100],
                 "horario": item.get("due_time"), "limite_final": item.get("deadline_date"),
                 "descricao": str(item.get("description", ""))[:1200],
                 "etiquetas": item.get("labels", []) if isinstance(item.get("labels"), list) else [],
                 "recorrente": item.get("is_recurring") is True,
                 "duracao": str(item.get("duration", ""))[:40],
                 "subtarefa": item.get("has_parent") is True,
                 "estado": item.get("status", "sem_prazo"), "prioridade": item.get("priority", 1)}
                for item in data["tasks"] if isinstance(item, dict) and isinstance(item.get("content"), str)
            ]
            return self.json_response(200, {"generated_at": data.get("generated_at"), "tasks": tasks,
                                             "coverage_complete": data.get("coverage_complete") is True,
                                             "total_open_tasks": data.get("total_open_tasks", len(tasks))})
        except FileNotFoundError:
            return self.json_response(503, {"status": "unavailable", "tasks": []})
        except Exception:
            return self.json_response(500, {"status": "unavailable", "tasks": []})

    def api_curation_status(self) -> None:
        try:
            data = json.loads(CURATION_STATUS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("status") not in {"curation_not_ready", "curation_ready", "published"}:
                raise ValueError("invalid curation status")
            checked_at = data.get("checked_at")
            if not isinstance(checked_at, str) or len(checked_at) > 40 or not re.fullmatch(r"[0-9T:+.Z-]+", checked_at):
                checked_at = None
            allowed_reasons = {"curation_stale_or_model_invalid", "sections_missing", "source_snapshot_mismatch",
                               "record_coverage_incomplete", "summary_incomplete", "section_incomplete",
                               "evidence_incomplete", "question_request_review_incomplete", "validation_error",
                               "direct_message_coverage_incomplete", "todoist_coverage_incomplete",
                               "agenda_coverage_incomplete", "family_schedule_incomplete", "prayer_updates_invalid",
                               "digest_validation_failed", "source_snapshot_changed", "model_output_invalid",
                               "curation_validation_failed"}
            reason_code = data.get("reason_code") if data.get("reason_code") in allowed_reasons else None
            return self.json_response(200, {"status": data["status"], "checked_at": checked_at, "reason_code": reason_code})
        except Exception:
            return self.json_response(200, {"status": "unknown", "checked_at": None})

    def json_response(self, status: int, value: object) -> None:
        body = json.dumps(mask_all(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def api_history_index(self) -> None:
        try:
            dates = sorted(
                (path.stem for path in HISTORY_DIR.glob("????-??-??.json")
                 if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
                 and self.history_synced(path)),
                reverse=True,
            )[:730]
            return self.json_response(200, {"dates": dates})
        except OSError:
            return self.json_response(200, {"dates": []})

    def api_history_day(self, day: str) -> None:
        path = (HISTORY_DIR / f"{day}.json").resolve()
        if path.parent != HISTORY_DIR:
            return self.send_error(404)
        if not self.history_synced(path):
            return self.send_error(404, "Snapshot ainda não foi salvo no Supabase")
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = sanitize(json.load(stream))
            if not isinstance(value, dict):
                raise ValueError("invalid history snapshot")
            # Older snapshots carry an incomplete or differently classified
            # group catalog. Use today's verified IFC catalog only to filter
            # the historical participant directory; the snapshot's facts and
            # membership names remain historical.
            self.attach_ifc_catalog_for_history(value)
            return self.json_response(200, value)
        except FileNotFoundError:
            return self.send_error(404, "Snapshot diário não encontrado")
        except Exception:
            return self.send_error(500, "Falha ao carregar o histórico diário")

    @staticmethod
    def normalized_group_name(value: object) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or ""))
        return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold().split())

    @classmethod
    def attach_ifc_catalog_for_history(cls, value: dict) -> None:
        directory = value.get("participantes_grupos")
        if not isinstance(directory, dict):
            return
        try:
            with DATA_FILE.open("r", encoding="utf-8") as stream:
                current = json.load(stream)
            catalog = current.get("grupos", {}).get("catalogo_grupos", [])
            allowed = sorted({
                str(group.get("name") or group.get("group") or "").strip()
                for group in catalog
                if isinstance(group, dict) and group.get("category") == "IFC"
                and isinstance(group.get("name") or group.get("group"), str)
                and str(group.get("name") or group.get("group") or "").strip()
            }) if isinstance(catalog, list) else []
        except (OSError, ValueError, TypeError, AttributeError):
            return
        if not allowed:
            return
        allowed_keys = {cls.normalized_group_name(name) for name in allowed}
        directory["ifc_catalog_names"] = allowed
        legacy_groups = directory.get("grupos")
        if isinstance(legacy_groups, list):
            filtered = []
            seen_groups = set()
            for group in legacy_groups:
                if not isinstance(group, dict) or not isinstance(group.get("g"), str):
                    continue
                key = cls.normalized_group_name(group["g"])
                if key in allowed_keys and key not in seen_groups:
                    filtered.append(group)
                    seen_groups.add(key)
            directory["grupos"] = filtered
            directory.update(
                scope="IFC", scope_category="IFC", status="partial_legacy_names",
                directory_source_snapshot=directory.get("directory_source_snapshot") or value.get("hoje"),
            )
        contacts = directory.get("contatos")
        if isinstance(contacts, list):
            filtered_contacts = []
            seen_contacts = set()
            for person in contacts:
                if not isinstance(person, dict) or not isinstance(person.get("grupos"), list):
                    continue
                groups = [group for group in person["grupos"]
                          if isinstance(group, str) and cls.normalized_group_name(group) in allowed_keys]
                if not groups:
                    continue
                phone = str(person.get("telefone") or "")
                evolution_id = str(person.get("evolution_id") or "").casefold()
                identity = "p:" + phone if phone else "i:" + evolution_id
                if identity in {"p:", "i:"} or identity in seen_contacts:
                    continue
                cleaned = dict(person)
                cleaned["grupos"] = list(dict.fromkeys(groups))
                filtered_contacts.append(cleaned)
                seen_contacts.add(identity)
            directory["contatos"] = filtered_contacts
            directory["unique_contacts"] = len(filtered_contacts)

    @staticmethod
    def history_synced(path: Path) -> bool:
        marker = path.with_suffix(".synced")
        try:
            import hashlib
            expected = marker.read_text(encoding="ascii").strip()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return bool(expected) and hmac.compare_digest(expected, digest)
        except OSError:
            return False

    def log_message(self, fmt, *args):
        # Query strings and remote addresses are not useful for this private view.
        print("painel-ifc:", fmt % args)


if __name__ == "__main__":
    if not AUTH_USER or not AUTH_PASSWORD:
        raise SystemExit("IFC_PANEL_USER and IFC_PANEL_PASSWORD are required")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
