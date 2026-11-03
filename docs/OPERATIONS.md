# Operations, schedules, and recovery

## Daily sequence

1. The named preparation slot begins the edition and acquires an exclusive lock.
2. Collectors query approved sources and write per-source timestamped snapshots. A failed source is marked failed; stale data is not represented as current.
3. Media handling retrieves/transcribes within bounded retries. For audio, after three retrieval attempts the missing item is skipped and reported as a coverage gap.
4. Curator generates structured summaries with exact evidence references. Validators compare the source capture hashes/timestamps, per-source record indices, section schema, question/request review and PII scans.
5. Panel publication merges only validated captures, writes atomically and archives the daily JSON. Supabase history synchronization has its own success state.
6. The separate WhatsApp delivery slot reads a current edition manifest, checks its source snapshot and one-send ledger, then makes a single request to the fixed Evolution instance/recipient.

The implementation's normal intended schedule is configured in code and production scheduler. Confirm live state using systemd/cron and `digest_schedule.py`; documentation alone is not evidence the timer is running. Local wall clock is `America/Sao_Paulo`.

## Status vocabulary

| State | Meaning | Operator action |
| --- | --- | --- |
| `current` | Collector produced a fresh validated snapshot | Continue to curation |
| `failed` | Source request or validation failed | Preserve last good panel; diagnose provider/secret/schema |
| `stale` | Data exceeds configured age | Do not describe as today's collection |
| `curation_not_ready` | Curator output or any gate failed | Do not publish/send; inspect reason code only |
| `curation_ready` | Curation passed its configured checks | Publish only through the fixed publisher |
| `published` | New panel snapshot replaced atomically | Confirm history sync separately |
| `prepared_not_sent` | Digest manifest passed validation | Sender may evaluate it only in delivery window |
| `accepted_by_evolution` | Evolution API returned an accepted message ID | Does not prove recipient delivery/read |
| `uncertain_requires_manual_review` | Request outcome cannot be trusted | Never retry automatically; inspect Evolution/device first |

## Checks before diagnosing

- Check host clock and timezone.
- Check which exact edition/date marker is present and the attempt count.
- Check container health, exit code, lock holder and disk space.
- Compare only file timestamps, hashes, schema states and counts; do not dump raw chats or environment.
- Confirm all required secrets are mounted and unreadable to unrelated containers. Do not print their contents.
- Confirm provider connection is the intended account and still authorized.
- Check per-source status in the panel before diagnosing a missing section.
- Confirm published JSON is owned by the publisher service and `0600`; history directory `0700`.
- Confirm the public host still returns 401 when anonymous.

## Failure recovery

### Source unavailable

Mark it failed, keep the previous valid snapshot as historical/stale, and show source age/gap. Retry only according to the collector's bounded backoff. Do not silently replace missing values with an empty success response.

### Audio unavailable

Retry at most three times. On third failure, stop retries for that item, record its media gap and continue processing other records. Never infer speech from a filename, sender or surrounding text.

### Curation rejected

Do not publish or send. Inspect the allowlisted reason code and source coverage. Fix the schema/evidence at its source; do not relax a gate just to make the run green. Preserve raw evidence privately and never log it.

### Panel publish fails

Keep the previous panel intact. Check temporary file creation, volume permissions, JSON validation and health check. Deploy frontend/container changes with an immutable version and retain the prior image for rollback.

### History sync fails

Do not mark the date as archived/available until Supabase confirms the write. Use idempotent upsert keyed by local date and content hash, then retry the history stage without recollecting/sending.

### WhatsApp request times out or response is malformed

The sender writes the attempted ledger before posting. Treat any uncertain outcome as no-retry. Verify the message in Evolution/device history first. A duplicate could be disruptive. A message ID accepted by Evolution is not a delivery receipt.

### Restore

Restore the previous immutable container image and last known-good published JSON from a verified encrypted backup. Restore secrets from the secret manager, not a Git revision. Confirm 401/200 behavior, UI assets, date history and all service statuses. Do not replay old manifests.

## Monitoring and log policy

Track run ID, edition, duration, exit code, source status counts, curation reason code, snapshot hash, publication outcome, history outcome, sender ledger status, container image digest, CPU/memory and disk. Redact message body, email, phone, CPF, WhatsApp JID, account identifiers and tokens. Restrict logs and set bounded retention. Alert on repeated source failure, failed curation, failed history sync, skipped schedule or uncertain message status.
