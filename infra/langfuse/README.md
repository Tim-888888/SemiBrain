# Self-hosted Langfuse

Independent Docker Compose deployment, based on upstream Langfuse v4.50.0
(`ef1075337a463170c83e9d64e3ac09b83e1d3407`). Official images are verified and
pinned in `../images/langfuse-images.lock.json`. PostgreSQL, Redis and MinIO
reuse the already verified image binaries, **not** SemiBrain's live databases.
ClickHouse, PostgreSQL, Redis and object storage have dedicated directories and
credentials. All six services use bounded memory and rotating Docker logs.

## Endpoints and historical runs

| Setting | Purpose |
| --- | --- |
| `LANGFUSE_BASE_URL=http://langfuse-web:3000` | SDK ingestion over the existing core Docker network |
| `SEMIBRAIN_LANGFUSE_UI_URL` | Browser origin: HTTPS, or HTTP only for an explicit loopback SSH tunnel |
| `SEMIBRAIN_LANGFUSE_PROJECT_ID` | New self-hosted project ID |
| `SEMIBRAIN_LANGFUSE_LEGACY_*` | Previous cloud origin/project for pre-snapshot records |
| `NEXTAUTH_URL` | Langfuse login/callback origin; must match the browser origin |

Every newly accepted run saves `telemetry_target` (enabled flag, public origin,
project ID). Changing ingestion configuration cannot redirect its historical
link to an unrelated project. Before switching, drain active runs and snapshot
the old destination onto existing run records; also preserve the legacy fallback.
The old cloud project and credentials are retained privately for rollback.
Cloud traces are not copied automatically. A console link is not proof of
successful delivery: diagnostics continue to return `not_verified` until an
operator checks Langfuse itself.

SDK payloads remain allowlisted metadata, timing and token counts. Self-hosting
does not enable uploading full prompts, document bodies or private tool outputs.
Export failure is best effort and must not fail business requests. The SDK
queues asynchronously with a three-second exporter timeout; do not flush on
each user request.

## Bootstrap

1. Resolve and verify immutable images. Fill `compose.env.example` into a private
   `compose.env` (0600). Do not commit real secrets.
2. Generate distinct secrets and create `runtime.env` from the example, plus
   `postgres.env`, `redis.env`, `redis.conf`, `minio.env`, `clickhouse.env` and
   `init.env` in `LANGFUSE_SECRET_DIR` (0700 parent directory). Redis must use
   authenticated access, AOF and `noeviction`. `REDISCLI_AUTH` is used only by its
   health check. ClickHouse bind mounts must be writable by UID/GID 101.
3. `init.env` uses official `LANGFUSE_INIT_ORG_*`, `LANGFUSE_INIT_PROJECT_*`,
   `LANGFUSE_INIT_PROJECT_PUBLIC_KEY/SECRET_KEY`, and `LANGFUSE_INIT_USER_*`
   variables. This initializes the admin without GitHub OAuth or SMTP.
4. Start with `docker compose --env-file /private/compose.env -f compose.yaml up -d`.
5. Provision the demo user while access is restricted to loopback, then assign
   organization role `VIEWER` through Langfuse's authenticated membership API.
   Do not grant a project-specific Enterprise role or share the admin account.
   Set `AUTH_DISABLE_SIGNUP=true` after bootstrap. Use a dedicated demo
   organization: Viewer can read that organization's project traces.
6. Read back an SDK trace using the project API and verify both logins before
   switching SemiBrain's service environment files and recreating its services.

The default UI is bound to `127.0.0.1:18811`. An SSH tunnel can use:

```sh
ssh -N -L 18811:127.0.0.1:18811 your-ecs-host
```

Open `http://127.0.0.1:18811`. A different computer needs its own authorized
tunnel; loopback is not a public address. For public use, configure DNS, a valid
certificate and the applicable domain filing first. `tls.yaml` and
`nginx.conf.template` provide an optional HTTPS edge on port 9443. Set
`LANGFUSE_TLS_BIND`, `LANGFUSE_SERVER_NAME`, `LANGFUSE_TLS_DIR`, `NEXTAUTH_URL`
and `SEMIBRAIN_LANGFUSE_UI_URL` together. Never expose the database/Redis/MinIO
ports. Media uploads are outside this integration and need a separate reachable,
authenticated object-storage design if introduced later.

## Retention and recovery

Built-in configurable retention is an Enterprise entitlement in this release;
the headless retention variable is ignored without that entitlement. The
community deployment uses `retention.py` and the supported project API to
delete traces older than 30 days. Run a dry-run first; use `--apply` for the daily
job. It validates each timestamp and caps a run at 5,000 traces. Only the project
identified by the credentials is affected. Keep ingestion event objects under a
separate 7-day S3 lifecycle rule after verifying successful ingestion; this does
not delete ClickHouse trace records. No media is uploaded by SemiBrain today.

`backup.py` makes a consistent cold snapshot of all Langfuse data, secrets and
deployment files and restarts the stack in `finally`. It does not stop
SemiBrain. During its short maintenance window trace delivery can be delayed or
lost; it must not affect answers. The example operational schedule is weekly at
04:30 Asia/Shanghai, retaining four generations. Backup archives contain secrets:
restrict them to the administrator (0600), preserve the encryption key, and copy
them to separately controlled off-host storage for protection against disk loss.
An on-host archive alone is not disaster recovery.

Restore first into a separate directory and Compose project with an isolated
network and unused loopback ports. Verify admin membership and a known trace
before replacing live state. Do not run `docker compose down -v` or extract over
live database files. To roll SemiBrain back, restore its saved service env files
and image references; keep per-run target snapshots, since runs emitted to the
self-hosted project still belong there.

Monitor `/api/public/health` on web, `/api/health` on worker, Compose health
status, scheduled job results and data-disk free space. Inspect at least one
real quick-QA, single-agent and multi-agent trace, historical cloud links,
Viewer write rejection and a business request while Langfuse is stopped.

References: [official Compose](https://github.com/langfuse/langfuse/blob/v4.50.0/docker-compose.yml),
[headless initialization](https://langfuse.com/self-hosting/administration/headless-initialization),
[access control](https://langfuse.com/docs/administration/rbac).
