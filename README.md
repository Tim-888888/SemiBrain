# SemiBrain
Semiconductor quality investigation and knowledge collaboration platform.

Stage A provides a runnable engineering foundation: three FastAPI services, two Vue
applications, versioned contracts, a synthetic PostgreSQL warehouse, and bounded
document parser adapters. Login, RAG, investigations and production recovery are
later stages and are not implemented by this foundation.

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

The frontends display real health responses. The administration app uses `/admin/`.
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
location adapters. Parser results remain staged: publication, OCR enrichment and asset
storage are B/E-stage work. A parser subprocess is not the D-stage model code sandbox.

`scripts/verify/checkpoint_probe.py` tests the supported MongoDBSaver API with a private
`SEMIBRAIN_CHECKPOINT_PROBE_URI`. It requires an explicitly selected committed checkpoint,
even when a newer orphan exists. It does not implement durable leases or recovery.

The sealed evaluation generator, oracle and evaluator run offline. Generate holdouts
outside the repository, never mount them into running services, and leave them unused
until the appropriate acceptance stage. Evaluation envelopes may contain measurement
metadata; the answer body always remains unrestricted Markdown.

## Provenance

See `THIRD_PARTY_NOTICES.md`, `vendor/weknora-docreader/provenance.json`, package license
inventories and the immutable base image lock. License inventories describe upstream
components; they do not assign a new license to this repository or to dependencies.
