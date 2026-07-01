# Hermes Memory Glyph Hold

Hermes Memory Glyph Hold connects Hermes Agent to a Glyph Hold server for durable memory and managed secrets.

Glyph Hold becomes the active external memory provider for Hermes. Hermes can prefetch relevant memories before a response, store explicit memory writes, and call the same memory and secret tools exposed by the Glyph Hold MCP server.

This plugin is in beta while the Glyph Hold and Hermes integration is tested in real use.

## What This Provides

- Memory search, prefetch, create, update, archive, supersede, delete, and revision restore.
- Secret metadata search, create, update, delete, and explicit reveal tools.
- No hosted AI APIs, embeddings, vector databases, or paid APIs.
- No runtime Python dependencies outside Hermes and the Python standard library.

Secret values are never included in memory prefetch. Hermes only receives secret values when it calls an explicit reveal tool.

## Requirements

- Hermes Agent with memory provider plugin support.
- A running Glyph Hold server.
- A Glyph Hold API key with the scopes you want Hermes to use.

Recommended API key scopes:

- `memories:read`
- `memories:write`
- `secrets:write`
- `secrets:reveal`

Use fewer scopes if you want Hermes to have narrower access.

## Install

Clone this repository into the Hermes memory plugin directory.

```bash
mkdir -p ~/.hermes/plugins/memory
git clone git@github.com:Dosk3n/hermes-memory-glyphhold.git ~/.hermes/plugins/memory/glyphhold
```

Go into the plugin directory:

```bash
cd ~/.hermes/plugins/memory/glyphhold
```

Create the plugin environment file:

```bash
cp .env.example .env
```

Edit `.env` and set your Glyph Hold URL and API key:

```bash
GLYPHHOLD_URL=https://your-glyphhold-server.example.com
GLYPHHOLD_API_KEY=gh_live_your_api_key_here
```

Then enable the memory provider in Hermes:

```bash
hermes memory setup glyphhold
```

If your Hermes install uses a different plugin directory, clone the repository into that directory instead. The final folder name should be `glyphhold`.

## Configuration

Required:

- `GLYPHHOLD_URL`: Base URL of your Glyph Hold server.
- `GLYPHHOLD_API_KEY`: Glyph Hold API key.

Optional:

- `GLYPHHOLD_VERIFY_SSL`: Set to `false` only for a private test server with a certificate chain your machine cannot verify.
- `GLYPHHOLD_CA_BUNDLE`: Path to a custom CA bundle file.
- `GLYPHHOLD_MEMORY_CATEGORY_ID`: Category used when Hermes writes memory through the generic memory-write hook. Defaults to `cat_temporary`.
- `GLYPHHOLD_PREFETCH_MAX_MEMORIES`: Number of memories to prefetch. Defaults to `5`.
- `GLYPHHOLD_PREFETCH_MAX_TOKENS`: Approximate memory context budget. Defaults to `600`.

## Tools

The plugin exposes the Glyph Hold memory and secret surface to Hermes:

- `glyphhold_health`
- `list_categories`
- `list_memories`
- `get_memory`
- `search_memories`
- `prefetch_memories`
- `find_similar_memories`
- `prepare_memory_write`
- `create_memory`
- `update_memory`
- `update_memory_confidence`
- `archive_memory`
- `supersede_memory`
- `list_memory_revisions`
- `restore_memory_revision`
- `delete_memory`
- `search_secrets`
- `get_secret_metadata`
- `create_secret`
- `update_secret`
- `delete_secret`
- `reveal_secret`
- `reveal_secret_env`

Deletion tools require the current memory title or secret name as confirmation.

## Development

Development tooling is optional and is not needed to use the plugin.

```bash
python -m venv .venv
. .venv/bin/activate
pip install pytest ruff
pytest
ruff check .
```
