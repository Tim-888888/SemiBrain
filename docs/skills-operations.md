# Reviewed skills

Administrators create immutable draft versions in **Skills**. Publishing requires an explicit review confirmation and note. Knowledge uploads never register skills. A skill contains a short catalog description, Markdown instructions, dependencies, permitted account/Agent roles, and optionally reviewed Python code, a parameter schema, fixed export names, and a 1–30 second execution limit.

The catalog exposes only metadata. `skill.load` retrieves instructions and the pinned version; `skill.execute` accepts JSON parameters and authorized input asset/query/answer identifiers, not code. Instructions guide a method but are not fact evidence or a final answer schema. Script results and downloadable artifacts use the existing evidence and authorization pipeline.

Run/role pins in MongoDB prevent a publication from changing in-flight code or silently adding capabilities. Current user roles, Agent roles, dependencies, and the enabled flag are checked again while the Docker sandbox runs. Disabling a skill stops subsequent execution and denies access to dependent artifacts. Ordinary version upgrades preserve access to previously published provenance. Seven-day TTLs clear temporary run pins and load receipts; reviewed versions, command audit records, and user artifacts follow their separate durable lifecycles.

Scripts run in the existing network-disabled, resource-limited Docker sandbox. Only declared exports are registered. Packages must already exist in the approved runtime image; the skill cannot install dependencies on the host. Imports, parameters, and outputs are part of the administrator's review responsibility. Review is an application permission boundary, not a claim that arbitrary code has been formally proven safe.

The supervisor receives a union of role-specific extension metadata; it does not acquire the child's execution permission. The gateway supplies trusted roles, and business-service owns configuration, versions, execution, and revocation. There is no agent-service database access to these records.

Reference: WeKnora `f46c9905677b36a454aa6669a2619f9eecf9fd23`, `internal/agent/skills/loader.go` (progressive disclosure) and `internal/agent/tools/skill_runtime_guard.go` (runtime dependency diagnostics). SemiBrain keeps reviewed scripts in versioned records instead of exposing host skill directories or allowing runtime package installation.
