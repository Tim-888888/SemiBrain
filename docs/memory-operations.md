# Personal memory

Memory is opt-in per user. The agent service owns `memories`, `memory_settings`,
revision hashes and idempotency records. The conversation gateway derives owner
and authorization version from the authenticated session. There is no model write
tool and no automatic extraction from an unverified investigation.

The workspace's **My memory** page supports confirmed preferences, confirmed work
background and confirmed report excerpts. Excerpts must be contiguous text from
the caller's accessible final report; source hashes and lineage remain attached.
The lifetime is explicit, at most one year. Expired entries are not recalled.
Corrections replace the active content and retain revision hashes, not old text.
Deletion leaves only an identity/revision tombstone and removes revision records.
It does not retroactively erase original conversations or already delivered answers.

At most 100 entries belong to a user. A run pins up to five eligible entries;
report excerpts require lexical overlap with the opening question. Preferences
and background have priority. This is bounded recall, not vector memory search.
The message is added after compaction as user data and is excluded from prompt
preview copies. Context metrics include a separate memory category. Understanding
and evidence-review calls do not receive personal content. Current instructions
and backend authorization take precedence over all memory text.

Single-agent, multi-agent and quick-answer generation use the same reader. An
owner revision fence prevents edited, deleted, disabled or expired memory from
continuing through an old run. Affected runs stop and ask for a fresh submission.
Sources are checked before each use and joined into final report lineage, so a
withdrawn source cannot become permanent authorization through memory.

References inspected: WeKnora `f46c9905677b36a454aa6669a2619f9eecf9fd23`
(`internal/agent/tools/search_memory.go`, `internal/handler/memory.go`), and DSH
`4878cdabd87d4041bdaff61d04c966883b9fd07a` (`docs/user/guide/mcp-memory.md`).
WeKnora informed bounded recall and identity ownership. DSH's optional third-party
MCP examples do not implement this project's lifecycle or conflict policy; no
third-party memory database is required by this adaptation.
