# IFC Panel and Daily Digest

Source code and reproduction guide for the IFC private dashboard and WhatsApp daily summary system.

> This repository contains software and generic deployment documentation only. It intentionally excludes credentials, personal data, snapshots, source documents, VPS access files, and historical production audit logs. A new deployment needs its own approved provider connections and secrets.

## Start here

- [System architecture](docs/ARCHITECTURE.md)
- [Full reproduction guide](docs/REPRODUCTION.md)
- [Daily operations and troubleshooting](docs/OPERATIONS.md)
- [OpenBot / Remi_2 integration](docs/OPENBOT.md)
- [Security, privacy and backup requirements](docs/SECURITY.md)
- [Known implementation boundaries](docs/STATUS.md)
- [Frontend app README](painel-ifc/README.md)

The directory `painel-ifc/` contains the application, collectors, pipeline, MCP gateway and deployment components. The `collector` code describes integrations, while all account-specific IDs, group catalogs, credentials, OAuth connections and recipient routing must be provisioned privately on the destination VPS.

## Important status note

Reproduction documentation distinguishes code that exists from production behavior that must be proven on the target host. The job runner is deliberately fail-closed and does not mean every source is automatically collected merely because a collector exists. Confirm collector registration, runtime mounts, provider connection scopes, schedules, and a successful live run before enabling publication or message sending. See [implementation boundaries](docs/STATUS.md).

## License and data

No license is asserted for third-party services, provider data, or church records. Treat the system and all derived data as private. Do not add `.env` files, exported chats, contact lists, financial records, OAuth tokens, logs, or backups to Git.
