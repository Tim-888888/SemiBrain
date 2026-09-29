# SemiBrain
Semiconductor quality investigation and knowledge collaboration platform.

The current demonstration implements quick knowledge/web answers, bounded single-agent
investigations, and explicitly selected multi-agent coordination. Final answers remain
ordinary Markdown with authorized citations, images and downloadable artifacts.

Governance features include reviewed Wiki publication, source-backed Neo4j relationships,
versioned document editing and rollback, multi-format ingestion with manual review,
reviewed MCP/Skills, opt-in personal memory, worker/queue controls, immutable agent
configuration and human evaluation records. Report exports produce Markdown, Word or
PDF from the same persisted answer without another model call. Docker is the only
supported code sandbox.

This is an interview/demo project, not production quality certification. Finite live
acceptance found partially completed investigations and some parser/provider limitations;
execution completion and human quality are reported separately. Detailed private Chinese
planning, evaluation data, operational credentials and interview notes are intentionally
not published in this repository. Text roles default to DeepSeek Flash.

## Repository layout

| Directory | Responsibility |
| --- | --- |
| `apps/user-web` | Vue user workspace |
| `apps/admin-web` | Vue administration application |
| `services/conversation-service` | Identity, conversations, and streamed presentation |
| `services/agent-service` | Routing, agent execution, evidence, and Markdown answers |
| `services/business-service` | Governed queries, retrieval, ingestion, assets, and tools |
| `packages/contracts` | Versioned API, event, and tool contracts |
| `packages/common` | Shared transport and configuration utilities |
| `packages/ui` | Shared presentation components |
| `infra` | Deployment definitions and pinned images |

Each service owns its database and accesses other services through authenticated APIs.
Final answers use ordinary Markdown. Typed tool and event contracts do not impose a
fixed report schema on user-facing answers.

Understanding, investigation, review and RCA share the configurable `deepseek-flash`
default; role-specific overrides are available. DeepSeek uses non-thinking mode because
its Responses interface does not provide encrypted reasoning replay. Native tools and
the application Agent loop remain enabled. Plain reasoning is never stored or exposed.
Providers with verified encrypted replay can enable it explicitly. Running checkpoints
with different model profiles are rejected after a model migration; old final answers
remain readable and follow-ups create new runs. Vision, embedding, reranking and public
search keep their independently configured providers.

The shared Markdown renderer supports bracket and dollar math through KaTeX. It
disables trusted TeX commands, bounds macros and expression size, and sanitizes the
rendered HTML/MathML. Copying preserves the original Markdown, including formulas.

Runs retain bounded model/tool/delegation counts, deadlines, cancellation and fenced
checkpoint recovery. There is no default cumulative 200,000-token hard cap; actual
Token receipts and unknown usage remain visible. Per-request context capacity and
output reservation are separate from task-wide usage. Tool-free closeout uses available
authorized evidence when time or call limits approach; lease loss and cancellation do
not publish a successful fallback. Operators should inspect the accepted run's frozen
limits rather than assuming all execution modes share the same bounds.

## Local development

Use Python 3.12.13, uv 0.12.0, Node 24.19.0 and pnpm 11.19.0. Resolved packages are
recorded in `uv.lock` and `pnpm-lock.yaml`.

```shell
uv sync --frozen --all-packages
pnpm install --frozen-lockfile
uv run --all-packages pytest -q
uv run --all-packages ruff check packages services tests scripts
node --test packages/ui/tests/*.test.mjs
pnpm build
uv run --all-packages python scripts/verify/export_contracts.py
uv run --all-packages python scripts/verify/engineering.py
```

Run these service commands in separate terminals:

```shell
uv run --all-packages uvicorn semibrain_conversation.main:app --port 8101
uv run --all-packages uvicorn semibrain_agent.main:app --port 8102
uv run --all-packages uvicorn semibrain_business.main:app --port 8103
pnpm --filter @semibrain/user-web dev
pnpm --filter @semibrain/admin-web dev --port 5174
```

The administration app uses `/admin/`. Complete workflows require the B-stage storage,
per-service environment, workers, and reverse proxy described below; running the API
commands alone only provides process health and API definitions.
Each service exposes `/healthz` and its current `/openapi.json`; generated wire schemas
are in `packages/contracts/schemas`. A `Report` envelope stores a Markdown string and
citation bindings, not a business report schema or fixed answer sections.

## Private Linux foundation

Build `infra/images/backend.Dockerfile` and `web.Dockerfile` using the corresponding
immutable image references in `infra/images/base-images.lock.json`. The lock records
original registry manifests and verified imported image digests; imported references
must exist locally. On a fresh host, pull the original manifest digest from Docker Hub
or import a verified archive, then pass that reference as the build argument.

```shell
docker build -f infra/images/backend.Dockerfile --build-arg PYTHON_IMAGE=<verified-python-reference> -t semibrain/backend:a-stage .
docker build -f infra/images/web.Dockerfile --build-arg NODE_IMAGE=<verified-node-reference> --build-arg NGINX_IMAGE=<verified-nginx-reference> -t semibrain/web:a-stage .
docker compose --env-file /private/foundation-compose.env -f infra/compose/foundation.yaml up -d --wait
```

The private Compose environment must set `BACKEND_IMAGE`, `WEB_IMAGE`, `POSTGRES_IMAGE`,
`FOUNDATION_DATA_DIR`, `FOUNDATION_PG_PASSWORD_FILE` and `FOUNDATION_BUSINESS_ENV`.
Pin application images by digest or local immutable image ID. The password file contains
only the PostgreSQL owner password. The business environment contains a **read-only**
`SEMIBRAIN_WAREHOUSE_READ_URL` and a random `SEMIBRAIN_FOUNDATION_TOKEN`. Never reuse the
owner credentials in the running business API. Create the reader role separately and
grant SELECT only. The SQLAlchemy warehouse metadata creates the initial tables.

The web gateway binds to loopback port 8080 and blocks `/internal/`. A-stage health
pages are not an authenticated public product. The private foundation query endpoint
requires its bearer even within the Docker network; it will be replaced by B-stage
identity and resource authorization adapters.

## Private B-stage platform

Compose `infra/compose/storage.yaml` and `application.yaml` together. Build and pin
the backend/web images first. Populate private `conversation.env`, `agent.env`,
`business.env`, and an offline `bootstrap.env` under `SECRET_DIR`; never give online
services the bootstrap credentials. `.env.example` lists configuration names.
The storage lock is `infra/images/platform-images.lock.json`.

Initialize the Mongo replica set and authentication keyfile before running
`scripts/bootstrap/platform.py` through the `ops` profile. It creates service-specific
Mongo users, the synthetic PostgreSQL warehouse/read role, and scoped storage users.
Redis must start with service-specific ACLs: each service owns `celery_<service>:*`,
conversation alone owns `auth:*` and `delegation:*`; conversation writes `stream:runs`
and reads `stream:agent`, while agent has the reverse permissions. Business owns
`stream:business`. Disable the default Redis user and remote Celery control.

```shell
docker compose --env-file /private/compose.env -f infra/compose/storage.yaml -f infra/compose/application.yaml up -d
```

The web entry listens only on `127.0.0.1:8081`. Forward it to the exact loopback
origin configured in `SEMIBRAIN_PUBLIC_ORIGIN` for private development. APIs do not
expose host ports. Each service has its own Mongo credentials; only business can
reach the isolated PostgreSQL network. Workers use durable Mongo records, leases,
Outbox/Inbox delivery and terminal snapshots, with Redis as transport.

Explicit demo initialization is available through `semibrain_conversation.auth.seed_demo`
only when `SEMIBRAIN_DEMO_MODE=true`. It creates `admin/admin` and `user/user` on first
run and preserves existing records. Registration uses 8–128 character passwords,
Argon2id hashes and browser/purpose-bound, single-attempt captchas. Demo passwords
are intended for the isolated demonstration deployment.

`infra/compose/domain.yaml` adds optional TLS verification on loopback port 8443.
Provide `TLS_DIR` containing `fullchain.pem` and `semibrain.key`; account keys stay
outside the web container. Before public activation, satisfy the host's domain
filing requirements, change the exact browser origin to the canonical HTTPS domain,
check Secure cookies and proxy client-IP handling, and establish certificate renewal.
The repository does not create a renewal account or a DNS credential automatically.

In a Redis-loss recovery, stop consumers, replay each producer's durable Outbox,
reset the corresponding durable cursor using `reset_consumer`, then restart workers.
Inbox deduplication must remain intact. Inspect quarantine before replaying an event
after a schema upgrade. Do not silently remove failed events or mark tasks successful.

## Data and parser validation

`scripts/seed_mock/generate.py --seed <integer> --prefix <namespace> --output <private-path>`
generates reproducible synthetic data. Add `--load` with the private
`SEMIBRAIN_WAREHOUSE_ADMIN_URL` to load a disposable foundation database. A repeated
dataset is hash-checked and idempotent; different seeds should use different namespaces.
The query API computes first or final yield with explicit CP/FT, program version,
cohort time window and ingestion cutoff. Empty cohorts return a null ratio.

`tests/integration/test_postgres.py` requires private owner/reader URLs,
`SEMIBRAIN_FOUNDATION_BASE_URL`, `SEMIBRAIN_FOUNDATION_TOKEN` and the path to the offline
`SEMIBRAIN_ORACLE_MODULE`. It changes and restores one synthetic row to verify the
HTTP endpoint reads committed SQL data. Never point this test at production data.

Ingestion covers PDF, Word, images, Excel, XMind, PowerPoint, EPUB, Markdown, CSV,
JSON, HTML and MHTML. The pinned WeKnora parser subset is wrapped with Python
compatibility, source-location, image and fallback adapters. MinerU and vision/OCR
require explicit permission to send source content externally. Legacy Office conversion
and PDF report rendering use the pinned Docker runtime, never the host shell.
Original assets and immutable parsed versions are retained. Review warnings (including
legacy XLS value-only conversion), unsupported/encrypted/corrupt variants and cancellation
remain explicit; a successful upload does not automatically publish material to retrieval.

`scripts/verify/checkpoint_probe.py` tests the supported MongoDBSaver API with a private
`SEMIBRAIN_CHECKPOINT_PROBE_URI`. It requires an explicitly selected committed checkpoint,
even when a newer orphan exists. It does not implement durable leases or recovery.

The sealed evaluation generator, oracle and evaluator run offline. Generate holdouts
outside the repository; send only approved inputs and synthetic source tables to the
runtime during acceptance. Never mount oracle files, expected answers or evaluation
verdicts into running services. Evaluation envelopes may contain measurement
metadata; the answer body always remains unrestricted Markdown.

## History compaction

New investigation runs freeze `SEMIBRAIN_CONTEXT_COMPACTION_ENABLED` (default true)
and the optional `SEMIBRAIN_COMPACTION_POLICY_JSON` operator configuration. Older
runs without this snapshot retain the previous context path. Near the routed model's
window threshold, older closed tool exchanges are summarized while current goals
and recent original messages remain intact. This does not restore a cumulative
multi-agent Token cap or reset model/tool/time limits; summary requests are accounted
as `context.compact` and cannot consume the dedicated final-call allowance.

Defaults are `threshold_ratio=0.8`, `retain_ratio=0.16`, `headroom_tokens=65536`, and
`summary_tokens=8192`. Configure smaller routes explicitly using `default` settings
and exact model-name entries under `models`; invalid capacity is rejected. Source
bodies remain in the existing evidence/snapshot stores and can be read through
authorized `evidence.read` and `web.read` handles. Summaries are historical data,
not independently citable facts. Native history journals and `context_compactions`
are covered by the existing terminal-run replica cleanup; published reports and
formal source retention policies are unchanged.

The Mongo migration only creates an index. Committed summary pointers are fenced
per run/task/role; unsuccessful or unknown requests do not overwrite history or
automatically resubmit the same source span. See `scripts/verify/context_compaction.py`
for an opt-in, isolated real-Mongo probe with synthetic data and no provider calls.
This adaptation follows the [DSH compaction design](https://github.com/deepseek-ai/deepseek-harness/tree/00102833dfaee1da9f48a3a8eae9d34005a75218/packages/compaction/compaction-basic).

## Provenance

See `THIRD_PARTY_NOTICES.md`, `vendor/weknora-docreader/provenance.json`, package license
inventories and the immutable base image lock. License inventories describe upstream
components; they do not assign a new license to this repository or to dependencies.

## Governance operations

- [Agent configuration and evaluation](docs/agent-configuration.md)
- [MCP service governance](docs/mcp-operations.md)
- [Reviewed Skills](docs/skills-operations.md)
- [Personal memory](docs/memory-operations.md)
- [Markdown, Word and PDF exports](docs/report-export.md)
- [Manual isolated recovery rehearsal](docs/recovery-rehearsal.md)

MongoDB remains authoritative; Milvus and Neo4j are rebuildable projections, and MinIO
stores source and generated assets. E-stage migrations add indexes and collections without
destructive conversion of existing documents. Publish configuration explicitly after review;
new runs pin versions while historical answers retain their original lineage. Application
rollback restores previous pinned images/configuration; do not drop new data or overwrite
live storage with a rehearsal. The cold backup tool does not provide a scheduled daily
backup, off-host protection or an automatic disaster cutover.
