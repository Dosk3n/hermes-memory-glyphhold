# Glyph Hold 2 memory provider for Hermes

Version 2.1.1 targets Glyph Hold 2's `/api/v2` contract. Version 0.1.0 targets
Glyph Hold 1 and must be upgraded alongside the service. No API credits or model
calls are required by this provider; optional Advanced Memory runs on the server.

Install this directory as the `glyphhold` memory-provider plugin using Hermes's
supported plugin workflow, select `memory.provider: glyphhold`, and run
`hermes memory setup`. Restart Hermes after upgrading. Configure:

- `GLYPHHOLD_URL`: server base URL.
- `GLYPHHOLD_API_KEY`: API key with memory read/write scopes and explicit space grants.
- `GLYPHHOLD_SPACE_ID`: optional explicit space override. Leave unset to use the
  server's connection default and conversation context switching.
- `GLYPHHOLD_VERIFY_SSL`: true by default.
- `GLYPHHOLD_CA_BUNDLE`: optional CA certificate path.

In GH2 Settings configure each connection's grants and default. Work and Personal
connections may both receive explicit access to General. A bearer key's admin
scope does not automatically grant all spaces.

Hermes profile-scoped secrets are read through `agent.secret_scope.get_secret`.
`initialize(..., hermes_home=...)` can also read that profile's `glyphhold.env`;
this file never changes the process environment. Provider configuration passed
through `config` takes precedence. Native Hermes setup remains the supported
availability check and secrets-storage route.

Before model calls, prefetch supplies bounded context with record IDs, versions,
space, source lineage and freshness. It preserves server ordering and clearly
labels service failure separately from empty results. Explicit broader search
can inspect other permitted spaces without changing subsequent writes. Session
context is isolated by API principal, profile and conversation; `set_memory_context`
changes it deliberately. Construction-time cwd never silently routes memory.
Information already injected into a conversation cannot be retracted by switching
spaces; start a fresh conversation when strong separation is required.

Shared memory writes use tools as the single authority. Create requires a stable
idempotency key; updates, moves, archive and restore require the last-read version.
On HTTP 409, reread and reconcile instead of forcing an overwrite. Native
MEMORY.md/USER.md writes remain local and are deliberately not mirrored. There
is no transcript ingestion and no discarded background prefetch thread. Automatic
write hooks do not save recalled/generated summaries as independent evidence.

Non-primary cron/flush/subagent contexts cannot invoke shared write tools.
Secret tools search metadata separately and reveal individual values only for an
explicit user request or task requiring their use. Built-in and external secret
providers use identical tools. Bulk environment secret reveals are not exposed.

Run local contract tests with `python -m pytest tests`. These tests cover request
shapes, profile/session isolation, evidence preservation, failure visibility,
version/idempotency requirements and read-only contexts. A live Hermes session
still requires validation against the installed Hermes version and your server.

Official host contract:
https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin/

## Hermes 2.1.1 adapter validation (18 October 2026)

Reviewed the current [official provider guide](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin/),
[provider interface](https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_provider.py),
[manager](https://github.com/NousResearch/hermes-agent/blob/main/agent/memory_manager.py),
and [setup implementation](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/memory_setup.py).
The setup implementation supports environment variables for both secret and
non-secret fields; this provider uses that supported env-only configuration route.
Use a directory installation at `$HERMES_HOME/plugins/glyphhold`; do not inject
packages into a Hermes-managed Python environment with pip.

Version 2.1.1 implements the current `turn_author` hook argument, session-switch
handling and the native recall indicator. The indicator resets on empty/failed
recall and session changes. New/branched sessions use the connection default;
resuming an existing session restores that session's server-side context.
Profile home takes precedence over a display identity when namespacing sessions.
Availability checks recognize the active profile's `glyphhold.env` without network
access. Each registration creates a new provider instance.

Prefetch uses synchronous server retrieval with a three-second socket timeout;
it does not launch speculative background jobs. The socket timeout is not a strict
wall-clock deadline for all DNS/transport activity. Hermes controls the outer
prefetch budget. `sync_turn` remains an immediate no-op: user messages, tool output
and author metadata are not automatically ingested. No durable pre-compression
checkpoint is advertised. Server-side data needs a separate Glyph Hold backup;
`hermes backup` alone cannot back up the remote database.

Tests can load the actual official ABC rather than a stand-in:

```sh
HERMES_PROVIDER_SOURCE=/path/to/hermes/agent/memory_provider.py python -m pytest tests -q
```

Validated against current upstream and Pepper's exact installed Hermes revision
`0e427d788d253630d38a3a51cd76849e73cd6925`. This validates the provider interface,
not a complete live gateway conversation. GH2's cross-repository integration test
also exercises create, recall, update, context switching, branching and resuming
against its real FastAPI routes with an isolated test database.

For migration from 0.1.0, keep the old adapter with GH1 until the coordinated
switch. Back up the plugin (including its private `.env`) and GH1 data first.
Use `hermes memory setup` under the intended profile to configure the GH2 URL and
a GH2 key with explicit space grants. A legacy plugin-local `.env` is not loaded
by 2.1.1; migrate its required settings securely, and do not copy unrelated
credentials. Restart Hermes and verify recall and an explicit test write before
accepting the migration. Production deployment and this live check are pending.

Version 2.1.1 preserves legacy secret caller-label constraints through optional
`requesting_agent` and `tool` fields on `reveal_secret`. These labels supplement
authenticated space grants; they do not authenticate a caller. Unmatched legacy
restrictions are denied by GH2 rather than silently discarded during migration.

## Live secret providers (2.1)

With Vaultwarden enabled, search and metadata read all accessible vault entries
directly, including pre-existing items. Built-in secrets remain accessible.
Secret `space_id` is accepted for compatibility but no longer scopes access;
API keys still require secret permissions. Memory spaces are unchanged.

`create_secret` uses the active provider and rejects duplicate names. Optional
`folder_id` chooses a vault folder; omission uses the configured default and an
empty string means unfiled. `list_secret_folders` and `create_secret_folder`
manage optional folders. No memory-to-folder mapping or item linking is needed.

`reveal_secret(field=...)` selects a field from metadata (password, username,
SSH keys, notes, card/identity or custom fields). `update_secret` requires the
current metadata version and preserves other fields. External changes cannot
be made atomically against concurrent edits in other Bitwarden clients.

`migrate_secret` transfers a built-in secret without exposing its value to the
agent. Search first: provide an existing `target_id` and field to verify equal
values, or omit target ID to create only when the name is unused. Conflicts
retain both values for user review; never blindly overwrite or export plaintext.
Provider outages fail explicitly rather than look like an empty vault. Upgrade
the server and adapters together to use these operations.

### 2.1.1 search and migration recovery

`search_secrets(query=...)` also matches saved website addresses and hostnames,
including IP addresses. Metadata adds `websites` (normalised origins only) and
`hostnames`; URL usernames/passwords, paths, query tokens and fragments are
excluded. Multiple accounts stay separate; never select one by hostname alone.
Use `provider_name=builtin` for the remaining migration checklist.

`migrate_secret(dry_run=true)` checks an existing field or previews creation
without changing either provider. Known provider failure codes produce safe,
actionable tool errors; arbitrary error bodies are not echoed. An uncertain
write requires destination inspection before retrying.
