# SemiBrain
Semiconductor quality investigation and knowledge collaboration platform.

This repository is being initialized. It does not yet contain a runnable product.

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
volumes must remain outside this public repository. The configuration loader, application
services, dependency locks, and deployment definitions are still to be implemented.
