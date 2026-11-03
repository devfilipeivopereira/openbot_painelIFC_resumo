# Implementation status and boundaries

This page prevents an installer or operator from interpreting source files as proof that production is fully automated.

## Present in this source tree

- Responsive HTML/CSS/JavaScript dashboard and authenticated Python server.
- Dockerfiles for frontend, MCP, transcription and specialized database/Evolution readers.
- Collectors for Evolution groups/women/direct messages; Composio calendar, Drive, Outlook, Todoist and Instagram; YouTube public feed/captions; n8n health; finance and visitors gateways.
- Publisher, structured curation pipeline, evidence and source freshness gates, digest artifact builder, scheduler and guarded WhatsApp delivery script.
- Safe deploy, status and audit utilities.

Presence means code exists; it does not prove that its credentials, mounts, schema, source registry or scheduler are configured or that it works against another tenant.

## Important current boundaries

1. `job_runner.py` explicitly says fail-closed and tracks required source capabilities. `sources.json` describes some service secret files and collectors, but that file alone does not prove that all required sources are registered and runnable.
2. Some sources are intentionally excluded or need special treatment (legal/ATA is not model input; Notion needs a confirmed IFC dataset; spreadsheet connectors may be specialized). Do not mark those as successful collection just to unlock publication.
3. `daily_ifc_pipeline.py` performs complex collection/curation logic and expects provider access, private state, model routing and runtime files. Its imports and path assumptions must be reviewed before containerizing on a fresh VPS.
4. `digest_schedule.py` and `whatsapp_delivery.py` encode specific times/host/instance assumptions. Confirm current owner instructions, cron/systemd installation, timezone and paths before enabling.
5. The sender expects private recipient and API-key files and posts to Evolution. It must not be run in a test environment with production secrets or a real user unless the owner explicitly requests a test send.
6. `dashboard_publisher.py` can expose names and sensitive church details in a private panel. HTTP authentication and sanitizer behavior must be tested with synthetic and security review fixtures.
7. The consolidated “Escala dos Cultos” is a presentation merge across inputs unless a separate canonical spreadsheet migration is completed and confirmed. Do not claim there is one shared source spreadsheet based solely on the UI.
8. OpenBot connects to MCP but is not the scheduler. MCP start/status availability must be confirmed separately from the cron's presence.
9. Production acceptance needs a successful new VPS run, a validated panel publication, verified Supabase history insertion and an observed WhatsApp delivery. API acceptance alone is insufficient.

## Items to configure and prove on a new VPS

- Correct account ownership and OAuth scopes for all connected services.
- Exact IFC groups, women instance and IFC leader/admin direct-message allowlist.
- Collector registry and required secret mounts; ensure every expected source has explicit state.
- Model route/fallback and evidence validation on a complete recent source snapshot.
- Finance query matches the financial dashboard and returns aggregate data only.
- Visitor collector reads the intended n8n-backed table and handles no-date records explicitly.
- Media size/attempt limits, audio transcription model and visual-media partial status.
- Morning/evening summary schedule, daily 8:00/13:30 panel publication schedule and 24-hour windows.
- Idempotent Supabase history, retention, restore and duplicate-safe sending.
- Browser authentication, API sanitization, OpenBot tool scopes and public-repo content review.

## Do not treat these as acceptance evidence

- A successful Docker build.
- An MCP “connected” badge.
- A connected Composio workspace.
- A green `status` response that only reports config presence.
- A collector file existing in the repository.
- An Evolution `accepted_by_evolution` response without device-side confirmation.
