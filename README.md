# SemiBrain
Semiconductor quality investigation and knowledge collaboration platform.

Stage B adds account registration and login, a knowledge workspace, asynchronous
PDF/DOCX/Markdown/CSV ingestion, governed hybrid retrieval, read-only business tools,
streamed Markdown answers and persistent conversation history. The three Python
services and two Vue applications run against real storage and configured model APIs.
Stage C adds bounded single-agent investigations with native tool calling, versioned
runtime prompts, explicit fenced MongoDBSaver checkpoints, shared budgets, cancellation,
fixed statistics over authorized results, optional public Web Search and static-page
snapshots. A reviewer checks investigation drafts before Markdown publication. The
implementation is still under acceptance: earlier full batches and functional checks
are retained. Text roles now default to DeepSeek Flash through its Responses API;
the previous provider credit outage is retained in the historical evaluation records.
The stage is not signed off; final revision quality checks remain pending.
Multi-agent coordination, sandbox execution and advanced administration remain later-stage work.

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

Investigation runs default to 12 reasoning rounds, 20 tool calls, 40,000 tokens and
180 seconds. Unknown provider usage remains reserved and visible as unreconciled;
unknown pricing is never reported as zero. Workers restore only the explicitly committed
checkpoint ID, reconcile immutable model/tool observations, and reject stale writes.
The service coordinator commits checkpoints around LangGraph node transitions rather
than using an unfenced latest-checkpoint lookup. Restarting does not reset the budget.
The normal loop reserves 12,000 tokens and 30 seconds for tool-free synthesis and review.
Independent read-only requests may share one model turn; the executor processes their
native calls sequentially under the same budget. At a soft limit, one closeout attempt
uses registered observations and still requires review. If that cannot finish, a bounded
Markdown fallback preserves validated raw counts, their actual query scope and citations.
Cancellation and lease loss never publish this fallback. Recognized credit exhaustion
is reported separately from transient transport failure and is not automatically retried.

Web access is off by default and can be disabled during a run. Search results are URLs,
not fabricated source excerpts. The static fetcher validates DNS, the connected peer,
and every redirect, accepts no credentials, and stores private immutable snapshots.
It does not execute JavaScript, log into websites, or publish pages into the knowledge base.
Langfuse uses the configured regional endpoint and exports allowlisted identifiers,
status and usage only; business persistence does not depend on the telemetry service.

`.env.example` contains configuration names and public defaults only. Actual credentials,
source documents, local product and development records, acceptance evidence, and data
volumes must remain outside this public repository.

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

The first parser profile covers PDF, Markdown, DOCX and CSV. PDF uses MinerU only with
explicit external-data permission and a configured key, with local WeKnora fallback.
The other paths wrap the pinned WeKnora parser subset with compatibility and source
location adapters. B stores original assets and immutable parsed versions and requires
preview and explicit publication before retrieval. Expanded formats and OCR enrichment
remain later-stage work. A parser subprocess is not the D-stage model code sandbox.

`scripts/verify/checkpoint_probe.py` tests the supported MongoDBSaver API with a private
`SEMIBRAIN_CHECKPOINT_PROBE_URI`. It requires an explicitly selected committed checkpoint,
even when a newer orphan exists. It does not implement durable leases or recovery.

The sealed evaluation generator, oracle and evaluator run offline. Generate holdouts
outside the repository; send only approved inputs and synthetic source tables to the
runtime during acceptance. Never mount oracle files, expected answers or evaluation
verdicts into running services. Evaluation envelopes may contain measurement
metadata; the answer body always remains unrestricted Markdown.

## Provenance

See `THIRD_PARTY_NOTICES.md`, `vendor/weknora-docreader/provenance.json`, package license
inventories and the immutable base image lock. License inventories describe upstream
components; they do not assign a new license to this repository or to dependencies.
