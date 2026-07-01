from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("glyphhold_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def get_memory(self, memory_id: str):
        self.calls.append(("get_memory", (memory_id,), {}))
        return {"id": memory_id, "title": "Current title"}

    def delete_memory(self, memory_id: str):
        self.calls.append(("delete_memory", (memory_id,), {}))
        return None

    def get_secret_metadata(self, id_or_name: str):
        self.calls.append(("get_secret_metadata", (id_or_name,), {}))
        return {"id": "sec_1", "name": "CUSTOM_API_KEY"}

    def delete_secret(self, id_or_name: str):
        self.calls.append(("delete_secret", (id_or_name,), {}))
        return None

    def reveal_secret(self, **kwargs):
        self.calls.append(("reveal_secret", (), kwargs))
        return {"name": kwargs["id_or_name"], "value": "secret-value", "expires_in_seconds": 60}

    def reveal_secret_env(self, **kwargs):
        self.calls.append(("reveal_secret_env", (), kwargs))
        return {"format": "env", "values": {"CUSTOM_API_KEY": "secret-value"}}

    def prefetch_memories(self, **kwargs):
        self.calls.append(("prefetch_memories", (), kwargs))
        return {
            "should_inject": True,
            "memories": [
                {
                    "id": "mem_1",
                    "category": "people",
                    "title": "Jess context",
                    "summary": "Important relationship context.",
                }
            ],
        }


def test_provider_availability_uses_env(monkeypatch):
    plugin = load_plugin()
    monkeypatch.delenv("GLYPHHOLD_URL", raising=False)
    monkeypatch.delenv("GLYPHHOLD_API_KEY", raising=False)
    assert plugin.GlyphHoldMemoryProvider().is_available() is False

    monkeypatch.setenv("GLYPHHOLD_URL", "https://glyphhold.example.com")
    monkeypatch.setenv("GLYPHHOLD_API_KEY", "gh_live_example")
    assert plugin.GlyphHoldMemoryProvider().is_available() is True


def test_tool_surface_matches_mcp_tools():
    plugin = load_plugin()
    provider = plugin.GlyphHoldMemoryProvider()
    tool_names = {schema["function"]["name"] for schema in provider.get_tool_schemas()}
    assert tool_names == {
        "glyphhold_health",
        "list_categories",
        "list_memories",
        "get_memory",
        "search_memories",
        "prefetch_memories",
        "find_similar_memories",
        "prepare_memory_write",
        "create_memory",
        "update_memory",
        "update_memory_confidence",
        "archive_memory",
        "supersede_memory",
        "list_memory_revisions",
        "restore_memory_revision",
        "delete_memory",
        "search_secrets",
        "get_secret_metadata",
        "create_secret",
        "update_secret",
        "delete_secret",
        "reveal_secret",
        "reveal_secret_env",
    }


def test_delete_memory_requires_matching_title():
    plugin = load_plugin()
    provider = plugin.GlyphHoldMemoryProvider()
    fake = FakeClient()
    provider.client = fake

    result = json.loads(
        provider.handle_tool_call(
            "delete_memory", {"memory_id": "mem_1", "confirm_title": "Wrong title"}
        )
    )
    assert "error" in result
    assert fake.calls == [("get_memory", ("mem_1",), {})]

    result = json.loads(
        provider.handle_tool_call(
            "delete_memory", {"memory_id": "mem_1", "confirm_title": "Current title"}
        )
    )
    assert result == {"deleted": True}
    assert fake.calls[-1] == ("delete_memory", ("mem_1",), {})


def test_delete_secret_requires_matching_name():
    plugin = load_plugin()
    provider = plugin.GlyphHoldMemoryProvider()
    fake = FakeClient()
    provider.client = fake

    result = json.loads(
        provider.handle_tool_call(
            "delete_secret", {"id_or_name": "sec_1", "confirm_name": "OTHER_SECRET"}
        )
    )
    assert "error" in result
    assert fake.calls == [("get_secret_metadata", ("sec_1",), {})]

    result = json.loads(
        provider.handle_tool_call(
            "delete_secret", {"id_or_name": "sec_1", "confirm_name": "CUSTOM_API_KEY"}
        )
    )
    assert result == {"deleted": True}
    assert fake.calls[-1] == ("delete_secret", ("sec_1",), {})


def test_prefetch_formats_memory_context():
    plugin = load_plugin()
    provider = plugin.GlyphHoldMemoryProvider()
    provider.client = FakeClient()

    context = provider.prefetch("Tell me about Jess")

    assert "# Glyph Hold memory context" in context
    assert "[people] Jess context: Important relationship context." in context


def test_secret_reveal_tools_dispatch_to_client():
    plugin = load_plugin()
    provider = plugin.GlyphHoldMemoryProvider()
    fake = FakeClient()
    provider.client = fake

    reveal = json.loads(
        provider.handle_tool_call(
            "reveal_secret",
            {
                "id_or_name": "CUSTOM_API_KEY",
                "requesting_agent": "hermes",
                "purpose": "Needed for a user-approved API call",
            },
        )
    )
    assert reveal["value"] == "secret-value"
    assert fake.calls[-1][0] == "reveal_secret"
    assert fake.calls[-1][2]["purpose"] == "Needed for a user-approved API call"

    env = json.loads(
        provider.handle_tool_call(
            "reveal_secret_env",
            {"scope": "test", "requesting_agent": "hermes", "purpose": "Run a local command"},
        )
    )
    assert env["values"] == {"CUSTOM_API_KEY": "secret-value"}
    assert fake.calls[-1][0] == "reveal_secret_env"
