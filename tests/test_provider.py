from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("glyphhold_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = False

    def request(self, method, path, payload=None, params=None):
        self.calls.append((method, path, payload, params))
        return {"ok": True}

    def recall(self, **payload):
        self.calls.append(("recall", payload))
        if self.fail:
            raise RuntimeError("backend failed")
        return {
            "context": {"active_space_id": "space_work"},
            "results": [
                {
                    "id": "mem_1",
                    "version": 4,
                    "space_id": "space_work",
                    "title": "Jess",
                    "summary": "Known fact",
                    "record_type": "profile",
                    "source_refs": [{"id": "source", "version": 2}],
                    "stale": False,
                }
            ],
        }


def test_available_has_no_network(monkeypatch):
    plugin = load_plugin()
    monkeypatch.setattr(
        plugin,
        "get_secret",
        lambda key: {"GLYPHHOLD_URL": "http://gh", "GLYPHHOLD_API_KEY": "test"}.get(key),
    )
    assert plugin.GlyphHoldMemoryProvider().is_available()


def test_profile_config_never_mutates_environment(tmp_path, monkeypatch):
    plugin = load_plugin()
    monkeypatch.delenv("GLYPHHOLD_API_KEY", raising=False)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "glyphhold.env").write_text(
        "GLYPHHOLD_URL=http://first\nGLYPHHOLD_API_KEY=first-key\nGLYPHHOLD_SPACE_ID=space_work\n"
    )
    (second / "glyphhold.env").write_text(
        "GLYPHHOLD_URL=http://second\nGLYPHHOLD_API_KEY=second-key\nGLYPHHOLD_SPACE_ID=space_personal\n"
    )
    a = plugin.GlyphHoldMemoryProvider()
    b = plugin.GlyphHoldMemoryProvider()
    a.initialize("same-session", hermes_home=str(first))
    b.initialize("same-session", hermes_home=str(second))
    assert a.client.api_key == "first-key" and b.client.api_key == "second-key"
    assert a._session() != b._session()
    assert plugin.os.getenv("GLYPHHOLD_API_KEY") is None
    assert a.space_id == "space_work" and b.space_id == "space_personal"


def test_prefetch_retains_identity_lineage_and_session():
    plugin = load_plugin()
    p = plugin.GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.session_id = "chat"
    p.profile = "work"
    result = p.prefetch("Jess")
    assert "mem_1" in result and '"version": 4' in result and "space_work" in result
    assert "source_refs" in result
    assert p.client.calls[0][1]["session_id"] == "work:chat"
    count = len(p.client.calls)
    p.queue_prefetch("Jess")
    p.on_memory_write("add", "memory", "fact")
    assert len(p.client.calls) == count


def test_prefetch_failure_distinguished_from_no_matches():
    plugin = load_plugin()
    p = plugin.GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.client.fail = True
    assert "retrieval failed" in p.prefetch("Jess")
    assert p.last_error


def test_tools_require_cas_idempotency_and_allow_deliberate_discovery():
    p = load_plugin().GlyphHoldMemoryProvider()
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in p.get_tool_schemas()}
    assert "expected_version" in schemas["update_memory"]["required"]
    assert "idempotency_key" in schemas["create_memory"]["required"]
    assert "broaden" in schemas["search_memories"]["properties"]
    assert "reveal_secret_env" not in schemas
    assert "delete_memory" not in schemas


def test_dispatch_does_not_mutate_args_and_preserves_versions():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    args = {"memory_id": "mem /1", "body": "new", "expected_version": 3}
    result = json.loads(p.handle_tool_call("update_memory", args))
    assert result == {"ok": True} and args["memory_id"] == "mem /1"
    assert p.client.calls[0][:3] == (
        "PATCH",
        "/api/v2/memories/mem%20%2F1",
        {"body": "new", "expected_version": 3},
    )


def test_nonprimary_writes_blocked():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.write_enabled = False
    result = json.loads(
        p.handle_tool_call("create_memory", {"title": "x", "body": "y", "idempotency_key": "a"})
    )
    assert "disabled" in result["error"] and p.client.calls == []


def test_session_context_uses_current_session():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.session_id = "chat"
    p.profile = "personal"
    p.handle_tool_call("set_memory_context", {"space_id": "space_work"})
    assert p.client.calls[0][2] == {"session_id": "personal:chat", "space_id": "space_work"}


def test_current_hermes_hooks_do_not_ingest_transcripts():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.sync_turn(
        "private user message",
        "assistant response",
        session_id="chat",
        messages=[{"role": "tool", "content": "private output"}],
        turn_author={"id": "user-1", "name": "Dean", "is_bot": False},
    )
    assert not p.client.calls


def test_session_switch_routes_reads_and_writes_to_new_conversation():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.profile = "personal"
    p.session_id = "old"
    p.prefetch("Jess")
    p.on_session_switch("new", parent_session_id="old", reset=True, reason="branch")
    assert p.recall_status() is None
    p.handle_tool_call("create_memory", {"title": "fact", "body": "x", "idempotency_key": "op"})
    assert p.client.calls[-1][2]["session_id"] == "personal:new"
    p.handle_tool_call("get_memory_context", {})
    assert p.client.calls[-1][3]["session_id"] == "personal:new"


def test_recall_indicator_never_reuses_prior_success():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.prefetch("Jess")
    assert p.recall_status().count == 1
    p.prefetch("")
    assert p.recall_status() is None
    p.prefetch("Jess")
    p.client.fail = True
    p.prefetch("Jess")
    assert p.recall_status() is None
    p.shutdown()
    assert p.client is None


def test_availability_uses_active_profile_sidecar(tmp_path, monkeypatch):
    plugin = load_plugin()
    monkeypatch.setattr(plugin, "get_secret", lambda name: None)
    monkeypatch.setattr(plugin, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "glyphhold.env").write_text("GLYPHHOLD_URL=http://local\nGLYPHHOLD_API_KEY=test\n")
    p = plugin.GlyphHoldMemoryProvider()
    assert p.is_available()
    p.initialize("chat", hermes_home=tmp_path)
    assert p.client.base_url == "http://local"


def test_same_identity_in_different_homes_does_not_share_context(tmp_path):
    plugin = load_plugin()
    providers = []
    for name in ("first", "second"):
        p = plugin.GlyphHoldMemoryProvider()
        p.initialize(
            "chat",
            hermes_home=tmp_path / name,
            agent_identity="default",
            config={
                "GLYPHHOLD_URL": "http://gh",
                "GLYPHHOLD_API_KEY": "test",
            },
        )
        providers.append(p)
    assert providers[0]._session() != providers[1]._session()


def test_registration_returns_new_instance_for_each_profile():
    plugin = load_plugin()

    class Context:
        def __init__(self):
            self.providers = []

        def register_memory_provider(self, provider):
            self.providers.append(provider)

    ctx = Context()
    plugin.register(ctx)
    plugin.register(ctx)
    assert ctx.providers[0] is not ctx.providers[1]


def test_official_hermes_base_contract(monkeypatch):
    """Opt-in validation against an unmodified official Hermes source checkout/file."""
    import os
    import sys
    import types

    import pytest

    source = os.getenv("HERMES_PROVIDER_SOURCE")
    if not source:
        pytest.skip("Set HERMES_PROVIDER_SOURCE to official agent/memory_provider.py")
    spec = importlib.util.spec_from_file_location("agent.memory_provider", source)
    base = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", base)
    spec.loader.exec_module(base)
    plugin = load_plugin()
    p = plugin.GlyphHoldMemoryProvider()
    assert isinstance(p, base.MemoryProvider)
    p.initialize("chat", config={"GLYPHHOLD_URL": "http://local", "GLYPHHOLD_API_KEY": "test"})
    p.client = FakeClient()
    p.prefetch("Jess", session_id="chat")
    assert isinstance(p.recall_status(), base.RecallStatus)
    p.sync_turn("user", "assistant", session_id="chat", messages=[], turn_author={"id": "dean"})
    p.on_session_switch("next", parent_session_id="chat", reset=True, rewound=False)
    p.on_turn_start(1, "hello", author_id="dean")
    p.on_session_end([])
    p.on_pre_compress([])
    p.on_delegation("task", "result", child_session_id="child")
    assert p.backup_paths() == []
    assert p.identity_signature() == {}
    p.shutdown()


def test_reveal_preserves_legacy_caller_labels_in_body_not_url():
    p = load_plugin().GlyphHoldMemoryProvider()
    p.client = FakeClient()
    args = {
        "id_or_name": "SSH_PASSWORD",
        "space_id": "space_import_review",
        "requesting_agent": "Melchior",
        "tool": "ssh",
    }
    p.handle_tool_call("reveal_secret", args)
    assert p.client.calls[-1] == (
        "POST",
        "/api/v2/secrets/SSH_PASSWORD/reveal",
        {"requesting_agent": "Melchior", "tool": "ssh"},
        {"space_id": "space_import_review"},
    )
    assert args["id_or_name"] == "SSH_PASSWORD"


def test_global_secret_schema_and_folder_field_transfer_tools():
    plugin = load_plugin()
    p = plugin.GlyphHoldMemoryProvider()
    p.client = FakeClient()
    schemas = {s["function"]["name"]: s["function"]["parameters"] for s in p.get_tool_schemas()}
    assert schemas["create_secret"]["required"] == ["name", "value"]
    assert "folder_id" in schemas["create_secret"]["properties"]
    p.handle_tool_call("list_secret_folders", {})
    p.handle_tool_call(
        "reveal_secret", {"id_or_name": "vw:account:item", "field": "sshKey.privateKey"}
    )
    p.handle_tool_call(
        "migrate_secret",
        {
            "id_or_name": "sec_old",
            "expected_version": 1,
            "target_id": "vw:account:item",
            "field": "login.password",
        },
    )
    assert p.client.calls[0][1] == "/api/v2/secrets/folders"
    assert p.client.calls[1][2] == {"field": "sshKey.privateKey"}
    assert p.client.calls[1][3] == {}
    assert p.client.calls[2][1] == "/api/v2/secrets/sec_old/migrate"


def test_migration_preview_and_safe_provider_error_hint(monkeypatch):
    import io
    from urllib.error import HTTPError

    plugin = load_plugin()
    p = plugin.GlyphHoldMemoryProvider()
    p.client = FakeClient()
    p.handle_tool_call(
        "migrate_secret", {"id_or_name": "sec_test", "expected_version": 1, "dry_run": True}
    )
    assert p.client.calls[-1][2]["dry_run"] is True
    assert p.client.calls[-1][1].endswith("/migrate/preview")

    def fail(*args, **kwargs):
        raise HTTPError(
            "http://gh",
            503,
            "Unavailable",
            {"X-GlyphHold-Error": "vault_get_failed"},
            io.BytesIO(b'{"detail":"private-secret-value"}'),
        )

    monkeypatch.setattr(plugin, "urlopen", fail)
    client = plugin.GlyphHoldClient("http://gh", "test")
    try:
        client.request("POST", "/api/v2/secrets/test/migrate", {})
    except plugin.GlyphHoldError as exc:
        assert "Vault get failed" in str(exc) and "private-secret-value" not in str(exc)
    else:
        raise AssertionError("Expected a provider failure")
