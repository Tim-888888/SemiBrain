# Personal memory

[简体中文](memory-operations.zh-CN.md) · [English](memory-operations.md) · [Project overview](../README.en.md)

Memory is opt-in per user. The agent service owns `memories`, `memory_settings`,
revision hashes and idempotency records. The conversation gateway derives owner
and authorization version from the authenticated session. There is no model write
tool and no automatic extraction from an unverified investigation.

## Optional candidate extraction

The existing master switch gates both recall and extraction. `auto_extract` is a
separate opt-in preference (absent/false for existing accounts). Disabling the
master switch retains stored memories, cancels the extraction epoch, and prevents
late model responses from saving candidates. Re-enabling starts with messages
submitted after that change. A new conversation while disabled receives no
personal memory; existing conversation history is independent of this switch.

The agent-owned `memory-worker` uses a separate Celery queue. It reads completed
runs in batches of at most eight from one conversation, after a 60-second delay,
at most once per owner per 300 seconds. Both values and the approved extraction
model (`deepseek-flash` by default) are deployment settings in `.env.example`.
Run checkpoints, a leased owner epoch and transactional candidate publication
make wakeups disposable and retries safe. Model/parsing failures retry up to three
times, then checkpoint the affected inputs with a metadata-only failure log.
There is no model request on the answer/publication path.

Only explicitly stated lasting preferences and work background are proposed.
Assistant answers, tool outputs and documents are not inputs. Credential-bearing
and oversized pasted user messages are skipped. Each suggestion includes a
verbatim quote and an authorized user-message source hash; source access is
checked again when confirming and using it. Model output is validated and is
never a write tool. Pending proposals cannot enter the answer context.

The **My memory** page shows up to 50 pending suggestions with review/edit/confirm
and dismiss actions. Confirming an update checks the original memory revision;
background extraction cannot overwrite manual changes. Dismissal keeps only a
fingerprint to suppress equivalent normalized suggestions. Candidate writes and
dismissals do not invalidate active runs. Confirmation follows the existing
active-memory revision rules. Disabling auto-extraction alone retains recall;
disabling the master switch stops both. No bulk export, vector search or topic
tracking is introduced. Restore the previous backend/web images and stop the
new worker to roll back; the additive collections and fields are backward compatible.

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

No third-party memory database is required by this implementation.
