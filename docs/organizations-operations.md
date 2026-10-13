# Organizations and operations

Public entry: https://legalops.dev/openclm/. Signup is free, with a separate organization and private templates/workflow. Existing accounts/contracts are migrated into one legacy organization without changing passwords or token hashes. Invite recipients sign in with the invited address; administrators manage membership and the owner must transfer ownership before removal.

Default limits: 10 owned organizations/account, 100 active members/organization, 5,000 contracts/organization, 20 invitations/organization/hour. These are capacity limits, not paid plans. Roles are admin/editor/reviewer/viewer per organization. API keys remain bound to their original organization when a browser session switches.

## Mail and recovery

Set `MAIL_TRANSPORT=relay`, HTTPS `MAIL_RELAY_URL`, `MAIL_RELAY_SECRET`, and preserve `TOKEN_ENCRYPTION_KEY`. The companion `email-worker/` uses Cloudflare Email Sending from contato@legalops.dev. SMTP is also supported. The application stores encrypted outbox payloads, retries failures, deletes sent payloads and retains metadata for seven days. Provider acceptance is not proof of inbox delivery.

Six one-use recovery codes are displayed on signup/reset and can be rotated after verifying the current password. Store them outside the browser. Reset revokes all sessions, API keys and previous recovery/action tokens. Administrators can use `python -m openclm.cli reset-password --email ...` on the host if necessary. `create-user` and `seed` accept `--organization-id`; legacy organization is the default. Disabling an organization owner requires transferring ownership first.

## Migration and backup

1. Take a SQLite backup using its backup API and stop app writes during the final snapshot. Preserve the old application/configuration for rollback.
2. Upgrade the snapshot and empty PostgreSQL target with `alembic upgrade head`. Set `OPENCLM_MIGRATION_SOURCE` and `OPENCLM_MIGRATION_TARGET`; run `python -m scripts.migrate_sqlite_to_postgres`. It refuses a nonempty target and checks every table's count and fingerprint.
3. Change `DATABASE_URL`, preserve encryption secrets, start the new release and verify HTTPS health, existing login and document access. Keep the old SQLite file untouched. Rollback requires restoring the old release/config; after accepting new writes, reconcile those writes before rollback.
4. `scripts/backup_postgres.sh` makes validated custom-format dumps with 30-day local retention. Optional `OPENCLM_BACKUP_BUCKET`, `OPENCLM_WRANGLER`, and `OPENCLM_CONFIG_FILE` bundle the database and recovery configuration into a private R2 object. Never enable public bucket access: configuration contains secrets. Configure a 30-day lifecycle for the `daily/` prefix.
5. Restore a downloaded backup into an isolated database using `pg_restore --no-owner --no-acl`, compare table counts/fingerprints, then verify application reads. Monitor `scripts/healthcheck.py` through systemd; failures appear in the local journal. No automatic WhatsApp messages.

The public deployment uses PostgreSQL 17 on loopback and a single application process. DocuSign and Salesforce remain disabled pending real provider validation. Tests use mocked providers. Full distributed abuse, performance under sustained production load and accessibility certification are not claimed.
