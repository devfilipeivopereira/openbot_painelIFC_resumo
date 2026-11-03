# Architecture and data flow

## 1. System boundary

The service is a collection of separate components, not a single OpenBot process. OpenBot is an operator/agent client. It can use a narrowly scoped MCP endpoint and ask for status or approved work; scheduled collection, validation, rendering, storage, and sending run in VPS-side processes and containers. A provider account being connected to Composio does not by itself make a collector active.

```text
Evolution ─┐
Composio ──┼─> read-only collectors ─> private staging ─> curation + validation
n8n ───────┤                                             │
Supabase ──┤                                             ├─> dashboard publisher ─> protected JSON
YouTube ───┘                                             │                         ├─> HTML panel
                                                        │                         └─> Supabase history
                                                        └─> digest manifest ─> guarded Evolution sender

OpenBot / Remi_2 ── authenticated closed MCP surface ──> fixed job/status actions
```

## 2. Components and their responsibilities

| Component | Files | Responsibility |
| --- | --- | --- |
| Dashboard UI | `index.html`, `painel.css`, `painel.js` | Render sections, responsive tables/charts, collapsed history, daily archives and source status. No provider calls. |
| Dashboard HTTP server | `server.py` | Basic Auth, authenticated APIs, data sanitization and private static files. |
| Source collectors | `collectors/*.py` | Fetch approved source data and write normalized snapshots to private staging. |
| Attachment/media extraction | `collectors/attachment_text.py`, `evolution_direct.py` | Bounded text extraction, OCR/visual descriptions, audio transcription through the private gateway; unavailable media must remain a visible gap. |
| Curation | `daily_ifc_pipeline.py`, `detailed_curation.py`, `ifc-curation-schema.json` | Build editorial sections, validate source coverage/evidence and prepare the digest. |
| Publisher | `dashboard_publisher.py` | Merge validated staging data, preserve last valid source values, sanitize and atomically replace published JSON. |
| MCP gateway | `mcp_gateway.py`, `job_runner.py`, `Dockerfile.mcp` | Expose a fixed authenticated tool surface. The runner is fail-closed; it is not a general-purpose shell. |
| Scheduled runner | `digest_schedule.py`, `daily_ifc_pipeline.py` | Trigger named slots, use a lock, record state and require fresh preparation before publication. |
| WhatsApp delivery | `daily_artifacts.py`, `whatsapp_delivery.py` | Assemble a bounded message manifest, check freshness/source snapshot/PII, use an idempotency ledger, and send only to a privately configured recipient. |
| Transcription gateway | `transcription_gateway.py`, `Dockerfile.transcription-gateway` | Isolated model gateway for media; tokens and model API keys are secrets, never source files. |
| Private finance gateway | `db_gateway/server.py`, `db_gateway/Dockerfile` | Return only an allowlisted aggregate from the financial dashboard data source. |
| Direct-message DB reader | `evolution_direct_db_reader.js`, `Dockerfile.evolution-direct-reader` | Specialized narrowly-scoped access used by the authorized direct-message reader; must not be exposed as an unrestricted database endpoint. |

## 3. Source of truth and data lifecycle

1. Each collector reads one named source using a least-privilege credential and writes a timestamped JSON file under a private staging directory.
2. The curation step receives sanitized evidence from the source captures and produces typed output. It must represent decisions, facts, requests, risks, opportunities, next steps, and insights with traceable citations.
3. Validation checks freshness, full record coverage, required section shape, citation indices and exact evidence snippets, question/request reviews, PII rules, and source snapshot consistency. Failure must block a new publication or send; the last good panel remains intact.
4. `dashboard_publisher.py` produces the dashboard JSON atomically. The HTTP server reads only published artifacts and re-sanitizes before returning them.
5. The panel snapshot is written to private daily history. When history is backed by Supabase, a date should be visible only after that snapshot has been confirmed synced.
6. The digest is assembled separately from an immutable validated source snapshot. The sender checks age, edition, recipient, instance connectivity, PII and existing ledger before one send attempt.

Never confuse (a) collector present, (b) provider OAuth connected, (c) source read succeeded, (d) curation passed, (e) panel published, and (f) Evolution accepted a message. Each is a distinct state. An API acceptance response is not proof of WhatsApp delivery or read receipt.

## 4. Information represented

The panel model includes attention items, direct conversations, agenda, consolidated worship/preaching/reception schedules, cells and ministries, community, birthdays, baptisms, sermons and live-stream content, curated group highlights, women’s ministry, prayer requests, visitor follow-up, workflow status, finance aggregates, leadership/legal reference material, useful links and source health. A deployed panel may expose only a subset according to data availability and the authentication boundary.

Finance in the panel and digest must be sourced only from the IFC financial dashboard aggregate endpoint. Raw transactions, descriptions, payers, beneficiaries and credentials do not belong in staging output or UI.

## 5. Time and editions

All business schedule logic uses `America/Sao_Paulo`. The documented production design has a morning and evening WhatsApp digest over rolling 24-hour windows and dashboard publication slots. Schedules are configuration, not proof that a cron is installed. Inspect the destination host and observe successful cycles before announcing operation. Never shift times to UTC without converting and verifying cron's timezone.

The service schedule has separate active and historical semantics: incomplete dates are not promoted to active, past dates are archived, and merges require unambiguous date/service matches. A unified visual table does not automatically imply that a single canonical source spreadsheet exists.

## 6. Authentication paths

- Browser: HTTPS reverse proxy + application Basic Auth; API routes use the same auth.
- OpenBot: dedicated MCP bearer token, fixed hostname/transport, closed tools only.
- Collectors: individual files mounted read-only as secrets; provider tokens are not passed through tool arguments.
- Supabase: service network/private gateway, read-only SQL role or fixed aggregate query; never a browser connection string with elevated privileges.
- WhatsApp sender: private fixed recipient and instance files, no user-supplied number/instance arguments.

See [`SECURITY.md`](SECURITY.md) before deploying.
