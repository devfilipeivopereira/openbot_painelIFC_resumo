# Reproduce on a new VPS: detailed runbook

This guide is a deployment blueprint, not a one-command installer. It deliberately requires a human to create provider connections and approve the source scope on the destination account. Do not copy the existing VPS's secrets or production data into Git.

## 0. Decide target topology

Recommended minimum: a Linux VPS with Docker Engine/Swarm (or an equivalent orchestrator), HTTPS reverse proxy, a private application network, persistent encrypted/permission-restricted storage, UTC-capable scheduler configured explicitly for `America/Sao_Paulo`, and outbound HTTPS. Separate the public-facing panel, collection/curation runner, MCP gateway, transcription gateway and database gateways.

Do not grant OpenBot root on the host. Do not put a general shell or Docker socket in the OpenBot container. If using an orchestrator, do not mount its control socket into the agent.

## 1. Prepare host accounts and directories

Create a dedicated service identity and directories with restrictive permissions. Suggested logical layout (adjust paths to local policy):

```text
/opt/ifc-painel/                 deployment metadata and checked-out source
/opt/ifc-painel/secrets/          root/service-only secret source files (0700/0600)
/var/lib/ifc-job/data/staging/   collector captures (0700; files 0600)
/var/lib/ifc-job/data/curation/  curated records and evidence (0700; files 0600)
/var/lib/ifc-panel/              published snapshot and daily history (0700; files 0600)
/var/log/ifc-painel/              redacted operational logs (0700; files 0600)
```

Use separate service users/containers for HTTP, collection and database access. The web process should be non-root, read-only filesystem, no Linux capabilities, bounded CPU/memory and only read-only mounts to the data it serves. It should not have Composio, Evolution or database credentials.

## 2. Install runtime dependencies

Requirements in the current code include Python 3.12, Docker, and the packages in `requirements-mcp.txt` (`mcp`, `pypdf`, Pillow and yt-dlp). The MCP image also installs Tesseract Portuguese/English data, Poppler, FFmpeg and Node.js. Pin all image/package versions for repeatable builds; do not blindly use moving `main-stable` tags. Configure time-zone data and CA certificates. Install the GitHub source under a fixed release/tag rather than pulling an unreviewed branch on every start.

```bash
python3.12 -m venv /opt/ifc-painel/venv
/opt/ifc-painel/venv/bin/pip install --upgrade pip
/opt/ifc-painel/venv/bin/pip install -r painel-ifc/requirements-mcp.txt
```

The lightweight frontend image is built from `painel-ifc/Dockerfile`. The specialized images have separate Dockerfiles. Build only the components you deploy and scan/pin base images.

## 3. Provision external systems (account owner action)

Create/verify independent connections on the target VPS or Composio workspace. Export only credentials required by the intended collector; do not reuse an OAuth cookie/session from a desktop browser.

| Integration | Provisioning required | Minimum access |
| --- | --- | --- |
| Evolution API | API endpoint, read key, exact instance names and group catalog | read group/chat/media APIs for collection; sending key only in isolated sender, fixed recipient/instance |
| Composio Connect | Consumer API key mounted as secret; connect each approved toolkit/account | read-only Calendar, Drive, Outlook, Todoist, Instagram, YouTube tools. Audit schemas; do not enable writes by default |
| Google Calendar | Connect the secretary calendar account and verify calendar ID | list events only |
| Google Drive | Connect secretary account; identify approved folder IDs | list/read only; document download/OCR only if approved |
| Outlook/mail | Connect mailbox(es); verify mailbox identity | read message/body/attachment without marking read |
| Todoist | Connect correct owner account | read open tasks for dashboard/digest. Writes remain on-demand only |
| n8n visitor table | Authenticated dedicated read-only webhook/token | read only, constrained output schema |
| Supabase | Private network and dedicated read-only role/gateway | aggregate finance query, history insert/select via separate restricted path |
| LLM gateway | Codex Auth/model provider and optional LiteLLM alias configured on target | only minimum model scope; no raw secrets/ATA/phone/JID in prompt |
| YouTube | Confirm public channel identity; separate OAuth only if captions require it | public feed/captions by default; broader Google scopes need explicit consent |

Save account-specific aliases, IDs and recipient details in private config/secret files. The code defaults and IDs in sample files are not a substitute for validating ownership. Never place live data, contact catalogs, legal records, financial data, OAuth refresh tokens, or an API key in a `.json` file committed to Git.

## 4. Create a private configuration set

Create secrets as files readable only by the corresponding service. Use one file per credential, e.g. `composio.key`, `evolution-apikey`, `evolution-global.h`, `n8n.h`, `finance-gateway.token`, `visitors-n8n.token`, `litellm-transcription.token`, and `ifc-mcp-token`. Secret managers/orchestrator secrets are preferred over plain environment variables. Ensure generated staging and manifests are `0600` and parent directories `0700`.

The publisher/collectors read paths supplied by environment variables such as `IFC_JOB_ROOT`, `COMPOSIO_KEY_FILE`, `EVOLUTION_KEY_FILE`, `N8N_HEADER_FILE`, `IFC_FINANCE_TOKEN_FILE` and `IFC_TRANSCRIPTION_TOKEN_FILE`. Confirm each collector's actual constants before deployment; a name in this list does not imply every collector honors the same variable. Keep `sources.json` private if it contains account-specific routes or file mounts.

Provide private configuration files for:

1. WhatsApp group allowlist and classification (IFC groups only).
2. Women's ministry group and correct Evolution instance.
3. Direct-message leader/admin allowlist with explicit IFC affiliation. Do not infer affiliation from a name match.
4. Calendar ID/account, mailbox aliases, Drive folder IDs, n8n workflow/table IDs, financial dashboard schema and YouTube channel ID.
5. Sender recipient and Evolution instance, separate from collection secrets.
6. Model route, allowed backup models, maximum output size and provider rate limits.

The Outlook collector accepts private `IFC_OUTLOOK_MAILBOX_1_LABEL`, `IFC_OUTLOOK_MAILBOX_1_SELECTOR`, `IFC_OUTLOOK_MAILBOX_1_USER_ID`, and corresponding `_2_` environment values. Use the exact Composio connection alias/ID as selector or its mailbox address when supported by the connector. The second mailbox selector may be empty to disable that slot. Keep real mailbox addresses and account aliases out of public code and logs.

Keep each configuration in `/etc/ifc-painel` or a secrets manager with `0700` directory and `0600` files. Avoid command-line arguments that contain tokens.

## 5. Build and install the protected panel

1. Produce a synthetic `dados.json` matching the publisher's output. Do not publish real data during the first install.
2. Build from `painel-ifc/` and run the container as an unprivileged user, read-only, with only the published-data directory and auth secret mounted read-only.
3. Bind the HTTP app to an internal/private network only. Configure reverse proxy routing for the chosen host/path and TLS. Keep proxy and app authentication both understood; do not accidentally add an unauthenticated route.
4. Create a random high-entropy panel password through a secret manager. Do not reuse a chat password. Confirm anonymous requests return 401 and authenticated routes return only intended fields.
5. Confirm `/dados.json`, `/integrantes_ata2026.json` and mounted internal filenames are not directly downloadable. APIs should return `Cache-Control: no-store`.
6. Verify that the legal/leadership reference data is treated according to the owner's access policy and never enters model prompts.

The existing deployment helper is environment-specific and should not be run unchanged against a new host. Review its version, route labels, mount paths, network names and service secrets first.

## 6. Deploy collection/MCP services

1. Create private persistent volumes for staging, curation, cache, delivery ledger, panel output and history. Do not put any on the static web root.
2. Create the fixed DB aggregate gateway with an allowlisted SQL query. A caller must not submit SQL. Use transaction read-only settings and return only aggregate fields.
3. Create the transcription gateway separately. Apply media size/duration limits, timeouts, retry schedule and API usage budget. The collector policy is at most three retrieval attempts for unavailable WhatsApp audio; after that, record the gap and continue without inventing a transcript.
4. Build the MCP gateway with the `Dockerfile.mcp`, then inspect `mcp_gateway.py` tool registration. Provision a dedicated token of at least 32 random bytes. Expose only fixed status/start/curation/panel actions that are actually registered. Never expose arbitrary paths, command strings, SQL, Docker control or generic HTTP fetch.
5. `job_runner.py` currently fails closed when required source collection/curation capabilities are not ready. Do not make it publish by bypassing this check. Add each collector to the fixed registry and test its secret mounts and validation before enabling it.
6. Add service health checks, CPU/memory/PID limits, restart policy, log rotation, network policies and alerting. Keep application images immutable and roll back to the prior digest on failed health checks.

## 7. Connect OpenBot / Remi_2

Register an MCP server in the OpenBot host/client (not merely in Composio). Use Streamable HTTP, the private HTTPS endpoint and a secret header/bearer value stored in OpenBot's secret manager. Set the exact endpoint and tool allowlist. Run the MCP test and inspect discovered tool schemas. Authenticate only from a trusted client and do not paste the token into chat.

Give the agent a memory document with source meanings, schedule, privacy rules, error policy and how to ask for status. The agent should:

- use `status` to inspect, and `start` only for the fixed supported job;
- never use MCP to make arbitrary writes to providers;
- never process the fixed legal/ATA source through a language model;
- make no outbound WhatsApp message except the fixed approved summary to the configured owner, via the guarded sender;
- report source gaps honestly and distinguish queued/accepted/delivered/read;
- perform requested on-demand writes only after interpreting the user's specific instruction and using a service-specific tool.

Do not grant root/sudo. For operations requiring privilege, use a separate reviewed administrator workflow.

## 8. Establish scheduler and state machine

Use systemd timers or cron with an explicit timezone. A robust sequence has separate stages:

1. Preparation slot: acquire an exclusive lock; create an edition marker; run collectors with per-source timeout and structured status; use only current snapshots.
2. Curation: invoke the selected model; require schema validity, exact input snapshot stamps, complete record coverage and evidence for every editorial claim; repair bounded number of times, then fail closed.
3. Panel slots: publish only a fresh validated dataset; atomically replace the JSON; sync snapshot to Supabase; mark the date available only after sync succeeds.
4. WhatsApp slots: build a new edition-specific manifest; recheck data timestamps, PII, size, recipient and Evolution connection; record `attempted` before network POST; after uncertain result do not retry automatically.
5. Verify and alert: record only redacted status codes/metrics. Do not put message contents, API bodies, contact details or credentials in logs.

The currently encoded scheduler has named slots in `digest_schedule.py` and retry windows. Confirm those values and `whatsapp_delivery.py`'s accepted send windows against the owner's current requested local times before enabling. Schedule is local timezone `America/Sao_Paulo`; daylight-saving behavior must still be checked by the operator if local rules change.

Use distinct manifests and ledgers for morning, evening and manual test. Keep an idempotency lock. Never let a failed or expired manifest be sent later as if current.

## 9. First-run checklist

1. Validate DNS, TLS, reverse proxy and secret mounts without reading secret values into logs.
2. Use synthetic fixtures and check schema, PII redaction, output size and failed-source behavior.
3. Exercise every collector with a read-only provider call; record account identity and returned item count only.
4. Test missing provider, expired OAuth, timeout, malformed JSON, stale snapshot and media retrieval failure. The last good dashboard must remain intact.
5. Create a synthetic curation and assert every item maps to one exact source record; ambiguous name/person matching must fail.
6. Validate private panel auth/API; check 401 anonymous and 200 authenticated; inspect response keys for phone/CPF/JID, addresses, secrets and raw finance details.
7. Verify a history snapshot can be inserted/read back by date without exposing the history schema publicly.
8. Check digest renderer using synthetic input with long text/Unicode; assert it stays in configured character limit and preserves critical schedule, direct message, Todoist, finance and source-gap details.
9. Use sender `--check`/dry-run only; confirm recipient/instance from private config. A test send requires an explicit owner request and a separate ledger.
10. Observe at least one scheduled panel run and one scheduled summary end-to-end. Confirm Evolution accepted the correct text and recipient; independently confirm delivery on the device if claiming received.

Do not enable automatic sending until all collector, curation, privacy and delivery checks pass.

## 10. Migration from an existing installation

Transfer only versioned application code, sanitized configuration templates and selected non-sensitive panel history that the owner explicitly authorizes. Do not export the old VPS filesystem, Docker volumes, OpenBot database, Composio session, browser profile, `.env`, logs, raw chats, mail, contact lists or backups wholesale. Recreate OAuth connections on the destination, test ownership and scope, then migrate state using a documented encrypted transfer and restore procedure. Rotate any credential that may have been copied into an untrusted place.

## 11. Cutover and rollback

Run the new VPS in read-only shadow mode. Compare counts/status metadata, not private message content, against the current system for multiple days. Do not send from both systems. At cutover, stop the old sender before enabling the new one, verify exclusive scheduler lock and ledger continuity, then keep the previous image/data snapshot for rollback. Rollback should restore the last known-good image and published JSON; never replay an uncertain WhatsApp POST.
