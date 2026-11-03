# Security and privacy model

## Data classification

| Class | Examples | Handling |
| --- | --- | --- |
| Credentials | VPS SSH password/key, Composio key, Evolution key, OAuth token, MCP bearer, LLM key | Secret manager/orchestrator secret only; rotate; never Git, prompt, command line or logs |
| Highly restricted | ATA/legal records, phone numbers, CPF, WhatsApp JIDs, private chat/email bodies, prayer requests, visitor replies | Private encrypted volume, minimum service access, no public Git, no web root, no LLM unless specifically approved and minimized |
| Sensitive operational | group catalogs, leader allowlist, calendar/mailbox IDs, financial figures, visitor status, workflow IDs | Private configuration/data; output only necessary derived fields |
| Publishable application | HTML/CSS/JS, generic schemas and documentation | Review for embedded accounts, PII, incident history and internal endpoints before public release |

## Threat controls

- **Prompt injection from source content:** treat all external text/attachments as data; delimit and instruct the model; never let source text authorize tools.
- **Credential exposure:** separate secrets per service and scope; do not inject them into the LLM context. Use file mounts with least privileges.
- **Overpowered agent:** no root, generic shell, Docker socket or arbitrary network tool. Expose fixed MCP actions only.
- **Public data leakage:** `server.py` checks authentication, removes sensitive keys/patterns, denies direct access to internal source files and sets no-store/security headers. This filtering is defense in depth, not permission to store sensitive data in public volumes.
- **Partial/stale results:** require timestamps, coverage and explicit source status; preserve prior good snapshots and block publication on required failures.
- **Duplicate messaging:** fixed recipient/instance, time window, exclusive lock and attempt ledger. Ambiguous request outcome means manual review, not retry.
- **Malicious attachment:** bounded size/time, parser without executing content, sandbox OCR/FFmpeg, restricted outbound network.
- **Database misuse:** private gateway with fixed queries and aggregate-only response; no caller-supplied SQL.

## Public GitHub release checklist

Before any `git add`/push:

1. Exclude all `.env`, keys, passwords, cookies, OAuth exports, private keys and browser profiles.
2. Exclude `dados.json`, raw captures, history snapshots, names/phones/CPF/JIDs, source PDFs, exports, screenshots and logs.
3. Exclude VPS-specific audit/history notes; rewrite documentation as reproducible generic procedures.
4. Scan every staged file for high-entropy/API-key patterns and private person data. Inspect the complete staged diff and filenames.
5. Prefer synthetic fixtures with obviously fake records.
6. If a secret has ever been committed, removing it in a later commit is insufficient; rotate/revoke it and rewrite repository history or create a clean repository.

The repository owner's target URL was public when this documentation was prepared. Therefore commits must not contain any operational data or customer content. Do not assume a private repo just because the application itself requires login.

## Access lifecycle

Use unique high-entropy passwords and tokens, MFA for GitHub/provider accounts, pinned SSH host keys, a non-root deployment user, root-only Docker administration and a controlled break-glass procedure. Rotate credentials after suspected exposure and on owner/operator changes. Remove old OAuth connections and OpenBot tools when no longer used.

## Backups and retention

Encrypt backups at rest and in transit. Separate application source from sensitive histories and secrets. Restrict restore permissions. Set retention for raw messages, media, staging and logs according to owner policy; avoid indefinite retention unless explicitly required. Keep the immutable delivery ledger long enough to prevent duplicate sends. Periodically test restoration without printing recovered data.
