# MCP operations

The business service owns MCP configuration, version history, discovery handles and durable tool jobs. The gateway only proxies administrator actions and supplies current identity/Agent-role claims. Single and multi-Agent execution use the same `mcp.discover` and `mcp.call` tools, filtered by role and current scope.

## Deployment and activation

Set `SEMIBRAIN_MCP_ENDPOINTS_JSON` in the **business service and worker** environment. Its value is a deployment-owned JSON object keyed by endpoint reference. Each endpoint has `url`, optional `secret_ref`, and `data_origin` (`public`, `authorized_business`, or `synthetic`). Credentials are read from a named `SEMIBRAIN_MCP_*_TOKEN` environment variable; never put credentials in the URL or browser configuration. Plain HTTP requires explicit `allow_private: true` for an approved internal endpoint. No arbitrary model-supplied URL, stdio, remote shell, client roots, sampling or elicitation is supported.

The optional `infra/compose/extensions.yaml` starts `mcp-measurement`, a stateless length conversion example with no mounted data, secrets or published ports. Register its internal URL `http://mcp-measurement:8000/mcp` with `allow_private: true` and `data_origin: authorized_business`. This is a real MCP transport example, not a production connector or a source of process specifications.

In the administrator's MCP page: add a service referencing that endpoint; save; refresh the remote catalog; select exact tool names and allowed account/Agent roles; enable it and save. Default scope is `none`. `all` includes only enabled/authorized services; `selected` adds an explicit service intersection. A selected-document-only task does not use external MCP data.

## Execution and failure behavior

Only descriptions are exposed during listing. `describe` returns the full parameter schema and an opaque handle bound to principal/run/role/service/configuration/schema. The call path validates arguments locally and checks the live remote schema immediately before execution. Duplicate tool names across services remain distinct. A changed schema requires catalog refresh and rediscovery. A disabled service or revoked role blocks new calls and result delivery, including retained result authorization.

New permissions apply to subsequent runs; a run's original scope cannot grow. Execution handles expire after seven days; durable verified result provenance is retained with its job. MCP metadata and tool results remain untrusted content, never system messages or asset/lineage authority. Oversized, unsupported, or malformed results produce explicit errors instead of silently losing text. Resource links/images are not automatically fetched.

Each call uses the existing durable job and correlation identifiers. The complete remote exchange has a bounded timeout, response size cap and cancellation checks. Worker loss does not automatically retry an uncertain external call. Cancellation stops the local request; remote server side effects cannot be guaranteed reversible. Administrators must review each allowed tool's behavior before enabling it.

Configuration collections: `mcp_settings`, `mcp_config_history`, `mcp_catalogs`; ephemeral `mcp_tool_refs` and `mcp_run_policies` have TTL indexes. No cross-service database access or destructive migration is added. Rollback disables the policy and restores prior application images/configuration; previously accepted calls keep their audit records.

Design reference: WeKnora `f46c9905677b36a454aa6669a2619f9eecf9fd23`, `internal/agent/tools/mcp_catalog.go` and `mcp_exposure.go`. The implementation uses the official Python MCP SDK, locked by `uv.lock`, and adapts progressive discovery to SemiBrain's service and authorization boundaries.
