from __future__ import annotations

import json
import os
import ssl
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from agent.memory_provider import MemoryProvider
except Exception:  # pragma: no cover - used by local tests outside Hermes.
    class MemoryProvider:  # type: ignore[no-redef]
        pass

try:
    from tools.registry import tool_error
except Exception:  # pragma: no cover - used by local tests outside Hermes.
    def tool_error(message: str) -> str:
        return json.dumps({"error": message})


VALID_PREFETCH_LEVELS = {"never", "low", "normal", "high", "pinned"}
VALID_SECRET_TYPES = {"text", "api_key", "password", "token", "webhook_url", "username", "json"}


class GlyphHoldError(RuntimeError):
    pass


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _json_response(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


class GlyphHoldClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            raise GlyphHoldError("GLYPHHOLD_URL is required")
        if not api_key:
            raise GlyphHoldError("GLYPHHOLD_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.ssl_context = self._build_ssl_context(verify_ssl, ca_bundle)

    @staticmethod
    def _build_ssl_context(verify_ssl: bool, ca_bundle: str | None) -> ssl.SSLContext | None:
        if not verify_ssl:
            return ssl._create_unverified_context()
        if ca_bundle:
            return ssl.create_default_context(cafile=ca_bundle)
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            query = urlencode(_drop_none(params), doseq=True)
            if query:
                url = f"{url}?{query}"

        body = None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "hermes-memory-glyphhold/0.1.0",
        }
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                raw = response.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GlyphHoldError(f"Glyph Hold request failed: HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise GlyphHoldError(f"Glyph Hold request failed: {exc.reason}") from exc

    def health(self) -> Any:
        return self._request("GET", "/api/v1/health")

    def list_categories(self) -> Any:
        return self._request("GET", "/api/v1/categories")

    def list_memories(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Any:
        return self._request(
            "GET",
            "/api/v1/memories",
            params={
                "category": category,
                "tag": tag,
                "include_archived": include_archived,
                "limit": limit,
                "offset": offset,
            },
        )

    def get_memory(self, memory_id: str) -> Any:
        return self._request("GET", f"/api/v1/memories/{memory_id}")

    def search_memories(
        self,
        *,
        query: str = "",
        category: str | None = None,
        limit: int = 10,
        include_archived: bool = False,
        mode: str = "explicit",
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/memories/search",
            json_body={
                "query": query,
                "category": category,
                "limit": limit,
                "include_archived": include_archived,
                "mode": mode,
            },
        )

    def prefetch_memories(
        self,
        *,
        message: str,
        agent: str | None = "hermes",
        max_memories: int = 5,
        max_chars: int = 2400,
        max_tokens: int = 600,
        summaries_only: bool = True,
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/agent/prefetch",
            json_body={
                "agent": agent,
                "message": message,
                "max_memories": max_memories,
                "max_chars": max_chars,
                "max_tokens": max_tokens,
                "summaries_only": summaries_only,
            },
        )

    def find_similar_memories(
        self,
        *,
        title: str,
        body: str,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/memories/find-similar",
            json_body={
                "category": category,
                "title": title,
                "body": body,
                "tags": tags or [],
                "limit": limit,
            },
        )

    def prepare_memory_write(
        self,
        *,
        title: str,
        body: str,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/memories/prepare-write",
            json_body={
                "category": category,
                "title": title,
                "body": body,
                "tags": tags or [],
                "limit": limit,
            },
        )

    def create_memory(
        self,
        *,
        category_id: str,
        title: str,
        body: str,
        summary: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
        confidence: int = 3,
        auto_prefetch_level: str = "normal",
    ) -> Any:
        if auto_prefetch_level not in VALID_PREFETCH_LEVELS:
            raise GlyphHoldError("auto_prefetch_level must be never, low, normal, high, or pinned")
        return self._request(
            "POST",
            "/api/v1/memories",
            json_body={
                "category_id": category_id,
                "title": title,
                "summary": summary,
                "body": body,
                "tags": tags or [],
                "metadata": metadata or {},
                "source": source,
                "confidence": confidence,
                "auto_prefetch_level": auto_prefetch_level,
            },
        )

    def update_memory(self, memory_id: str, **fields: Any) -> Any:
        return self._request("PATCH", f"/api/v1/memories/{memory_id}", json_body=_drop_none(fields))

    def update_memory_confidence(
        self, memory_id: str, *, confidence: int, change_reason: str | None = None
    ) -> Any:
        return self._request(
            "POST",
            f"/api/v1/memories/{memory_id}/confidence",
            json_body={"confidence": confidence, "change_reason": change_reason},
        )

    def archive_memory(self, memory_id: str) -> Any:
        return self._request("POST", f"/api/v1/memories/{memory_id}/archive", json_body={})

    def supersede_memory(self, memory_id: str, *, superseded_by: str) -> Any:
        return self._request(
            "POST",
            f"/api/v1/memories/{memory_id}/supersede",
            json_body={"superseded_by": superseded_by},
        )

    def list_memory_revisions(self, memory_id: str) -> Any:
        return self._request("GET", f"/api/v1/memories/{memory_id}/revisions")

    def restore_memory_revision(
        self, memory_id: str, revision_id: str, *, change_reason: str | None = None
    ) -> Any:
        return self._request(
            "POST",
            f"/api/v1/memories/{memory_id}/revisions/{revision_id}/restore",
            json_body={"change_reason": change_reason},
        )

    def delete_memory(self, memory_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/memories/{memory_id}")

    def search_secrets(
        self,
        *,
        query: str | None = None,
        service: str | None = None,
        host: str | None = None,
        scope: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Any:
        return self._request(
            "GET",
            "/api/v1/secrets",
            params={
                "query": query,
                "service": service,
                "host": host,
                "scope": scope,
                "limit": limit,
                "offset": offset,
            },
        )

    def get_secret_metadata(self, id_or_name: str) -> Any:
        return self._request("GET", f"/api/v1/secrets/{id_or_name}")

    def create_secret(
        self,
        *,
        name: str,
        value: str,
        description: str | None = None,
        value_type: str = "text",
        service: str | None = None,
        host: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        allowed_agents: list[str] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> Any:
        if value_type not in VALID_SECRET_TYPES:
            raise GlyphHoldError("value_type must be text, api_key, password, token, webhook_url, username, or json")
        return self._request(
            "POST",
            "/api/v1/secrets",
            json_body={
                "name": name,
                "value": value,
                "description": description,
                "value_type": value_type,
                "service": service,
                "host": host,
                "scope": scope,
                "tags": tags or [],
                "allowed_agents": allowed_agents or [],
                "allowed_tools": allowed_tools or [],
            },
        )

    def update_secret(self, id_or_name: str, **fields: Any) -> Any:
        return self._request("PATCH", f"/api/v1/secrets/{id_or_name}", json_body=_drop_none(fields))

    def delete_secret(self, id_or_name: str) -> Any:
        return self._request("DELETE", f"/api/v1/secrets/{id_or_name}")

    def reveal_secret(
        self,
        id_or_name: str,
        *,
        requesting_agent: str = "hermes",
        tool: str | None = None,
        purpose: str = "Hermes reveal requested by user",
    ) -> Any:
        return self._request(
            "POST",
            f"/api/v1/secrets/{id_or_name}/reveal",
            json_body={"requesting_agent": requesting_agent, "tool": tool, "purpose": purpose},
        )

    def reveal_secret_env(
        self,
        *,
        scope: str | None = None,
        requesting_agent: str = "hermes",
        purpose: str = "Hermes env reveal requested by user",
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/secrets/env",
            json_body={"scope": scope, "requesting_agent": requesting_agent, "purpose": purpose},
        )


class GlyphHoldMemoryProvider(MemoryProvider):
    name = "glyphhold"

    def __init__(self) -> None:
        self.client: GlyphHoldClient | None = None
        self.session_id = ""
        self.agent_identity = "hermes"
        self.write_enabled = True
        self.memory_category_id = "cat_temporary"
        self.prefetch_max_memories = 5
        self.prefetch_max_tokens = 600

    def is_available(self) -> bool:
        _load_env_file(Path(__file__).with_name(".env"))
        return bool(os.getenv("GLYPHHOLD_URL") and os.getenv("GLYPHHOLD_API_KEY"))

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "fields": [
                {
                    "name": "GLYPHHOLD_URL",
                    "label": "Glyph Hold URL",
                    "type": "string",
                    "required": True,
                    "env_var": "GLYPHHOLD_URL",
                },
                {
                    "name": "GLYPHHOLD_API_KEY",
                    "label": "Glyph Hold API key",
                    "type": "string",
                    "required": True,
                    "secret": True,
                    "env_var": "GLYPHHOLD_API_KEY",
                },
                {
                    "name": "GLYPHHOLD_VERIFY_SSL",
                    "label": "Verify SSL certificates",
                    "type": "boolean",
                    "required": False,
                    "env_var": "GLYPHHOLD_VERIFY_SSL",
                    "default": True,
                },
                {
                    "name": "GLYPHHOLD_CA_BUNDLE",
                    "label": "Custom CA bundle path",
                    "type": "string",
                    "required": False,
                    "env_var": "GLYPHHOLD_CA_BUNDLE",
                },
            ]
        }

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        _load_env_file(Path(__file__).with_name(".env"))
        self.session_id = session_id
        self.agent_identity = kwargs.get("agent_identity") or "hermes"
        agent_context = kwargs.get("agent_context") or ""
        self.write_enabled = agent_context not in {"cron", "flush", "subagent"}
        self.memory_category_id = os.getenv("GLYPHHOLD_MEMORY_CATEGORY_ID", "cat_temporary")
        self.prefetch_max_memories = int(os.getenv("GLYPHHOLD_PREFETCH_MAX_MEMORIES", "5"))
        self.prefetch_max_tokens = int(os.getenv("GLYPHHOLD_PREFETCH_MAX_TOKENS", "600"))
        self.client = GlyphHoldClient(
            os.getenv("GLYPHHOLD_URL", ""),
            os.getenv("GLYPHHOLD_API_KEY", ""),
            verify_ssl=_as_bool(os.getenv("GLYPHHOLD_VERIFY_SSL"), True),
            ca_bundle=os.getenv("GLYPHHOLD_CA_BUNDLE") or None,
        )

    def system_prompt_block(self) -> str:
        return (
            "Glyph Hold is available as the active external memory provider. "
            "Use it for durable memories, memory search, revision management, and managed secrets. "
            "Secret values are only available through explicit reveal tools and must not be requested "
            "or revealed unless the user asks for a task that requires the value."
        )

    def prefetch(self, query: str, session_id: str = "") -> str:
        if not query.strip():
            return ""
        try:
            data = self._client().prefetch_memories(
                message=query,
                agent=self.agent_identity,
                max_memories=self.prefetch_max_memories,
                max_tokens=self.prefetch_max_tokens,
            )
        except Exception:
            return ""
        if not data or not data.get("should_inject"):
            return ""
        lines = ["# Glyph Hold memory context"]
        for memory in data.get("memories", []):
            category = memory.get("category") or "uncategorized"
            title = memory.get("title") or "Untitled memory"
            summary = memory.get("summary") or ""
            lines.append(f"- [{category}] {title}: {summary}".rstrip())
        return "\n".join(lines)

    def queue_prefetch(self, query: str, session_id: str = "") -> None:
        thread = threading.Thread(target=self.prefetch, args=(query, session_id), daemon=True)
        thread.start()

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        return None

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.write_enabled or action != "add" or not content.strip():
            return
        title = content.strip().splitlines()[0][:120] or "Hermes memory"
        body = content.strip()
        summary = body[:240]
        try:
            self._client().create_memory(
                category_id=self.memory_category_id,
                title=title,
                summary=summary,
                body=body,
                tags=["hermes", "memory-write", target],
                metadata={"hermes_action": action, "hermes_target": target, **(metadata or {})},
                source="hermes",
                confidence=3,
                auto_prefetch_level="normal",
            )
        except Exception:
            return

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        str_prop = {"type": "string"}
        opt_str = {"type": "string"}
        int_prop = {"type": "integer"}
        bool_prop = {"type": "boolean"}
        tags_prop = {"type": "array", "items": {"type": "string"}}
        metadata_prop = {"type": "object"}
        return [
            _schema("glyphhold_health", "Check Glyph Hold server health."),
            _schema("list_categories", "List Glyph Hold memory categories."),
            _schema(
                "list_memories",
                "List Glyph Hold memories.",
                {
                    "category": opt_str,
                    "tag": opt_str,
                    "include_archived": bool_prop,
                    "limit": int_prop,
                    "offset": int_prop,
                },
            ),
            _schema("get_memory", "Get a memory by id.", {"memory_id": str_prop}, ["memory_id"]),
            _schema(
                "search_memories",
                "Search memories.",
                {
                    "query": opt_str,
                    "category": opt_str,
                    "limit": int_prop,
                    "include_archived": bool_prop,
                    "mode": opt_str,
                },
            ),
            _schema(
                "prefetch_memories",
                "Fetch concise memory context for a message.",
                {
                    "message": str_prop,
                    "agent": opt_str,
                    "max_memories": int_prop,
                    "max_chars": int_prop,
                    "max_tokens": int_prop,
                    "summaries_only": bool_prop,
                },
                ["message"],
            ),
            _schema(
                "find_similar_memories",
                "Find memories similar to proposed content.",
                {
                    "title": str_prop,
                    "body": str_prop,
                    "category": opt_str,
                    "tags": tags_prop,
                    "limit": int_prop,
                },
                ["title", "body"],
            ),
            _schema(
                "prepare_memory_write",
                "Check likely duplicates and conflicts before creating a memory.",
                {
                    "title": str_prop,
                    "body": str_prop,
                    "category": opt_str,
                    "tags": tags_prop,
                    "limit": int_prop,
                },
                ["title", "body"],
            ),
            _schema(
                "create_memory",
                "Create a memory.",
                {
                    "category_id": str_prop,
                    "title": str_prop,
                    "summary": opt_str,
                    "body": str_prop,
                    "tags": tags_prop,
                    "metadata": metadata_prop,
                    "source": opt_str,
                    "confidence": int_prop,
                    "auto_prefetch_level": {
                        "type": "string",
                        "enum": sorted(VALID_PREFETCH_LEVELS),
                    },
                },
                ["category_id", "title", "body"],
            ),
            _schema(
                "update_memory",
                "Update a memory.",
                {
                    "memory_id": str_prop,
                    "category_id": opt_str,
                    "title": opt_str,
                    "summary": opt_str,
                    "body": opt_str,
                    "tags": tags_prop,
                    "metadata": metadata_prop,
                    "source": opt_str,
                    "confidence": int_prop,
                    "auto_prefetch_level": {
                        "type": "string",
                        "enum": sorted(VALID_PREFETCH_LEVELS),
                    },
                    "archived": bool_prop,
                    "superseded_by": opt_str,
                    "change_reason": opt_str,
                },
                ["memory_id"],
            ),
            _schema(
                "update_memory_confidence",
                "Update a memory confidence score from 1 to 5.",
                {"memory_id": str_prop, "confidence": int_prop, "change_reason": opt_str},
                ["memory_id", "confidence"],
            ),
            _schema("archive_memory", "Archive a memory.", {"memory_id": str_prop}, ["memory_id"]),
            _schema(
                "supersede_memory",
                "Mark a memory as superseded by another memory.",
                {"memory_id": str_prop, "superseded_by": str_prop},
                ["memory_id", "superseded_by"],
            ),
            _schema(
                "list_memory_revisions",
                "List revisions for a memory.",
                {"memory_id": str_prop},
                ["memory_id"],
            ),
            _schema(
                "restore_memory_revision",
                "Restore a memory revision.",
                {"memory_id": str_prop, "revision_id": str_prop, "change_reason": opt_str},
                ["memory_id", "revision_id"],
            ),
            _schema(
                "delete_memory",
                "Delete a memory. Requires confirm_title to match the current memory title.",
                {"memory_id": str_prop, "confirm_title": str_prop},
                ["memory_id", "confirm_title"],
            ),
            _schema(
                "search_secrets",
                "Search secret metadata. Does not reveal values.",
                {
                    "query": opt_str,
                    "service": opt_str,
                    "host": opt_str,
                    "scope": opt_str,
                    "limit": int_prop,
                    "offset": int_prop,
                },
            ),
            _schema(
                "get_secret_metadata",
                "Get secret metadata. Does not reveal the value.",
                {"id_or_name": str_prop},
                ["id_or_name"],
            ),
            _schema(
                "create_secret",
                "Create a managed secret.",
                {
                    "name": str_prop,
                    "value": str_prop,
                    "description": opt_str,
                    "value_type": {"type": "string", "enum": sorted(VALID_SECRET_TYPES)},
                    "service": opt_str,
                    "host": opt_str,
                    "scope": opt_str,
                    "tags": tags_prop,
                    "allowed_agents": tags_prop,
                    "allowed_tools": tags_prop,
                },
                ["name", "value"],
            ),
            _schema(
                "update_secret",
                "Update a managed secret.",
                {
                    "id_or_name": str_prop,
                    "name": opt_str,
                    "value": opt_str,
                    "description": opt_str,
                    "value_type": {"type": "string", "enum": sorted(VALID_SECRET_TYPES)},
                    "service": opt_str,
                    "host": opt_str,
                    "scope": opt_str,
                    "tags": tags_prop,
                    "allowed_agents": tags_prop,
                    "allowed_tools": tags_prop,
                },
                ["id_or_name"],
            ),
            _schema(
                "delete_secret",
                "Delete a secret. Requires confirm_name to match the current secret name.",
                {"id_or_name": str_prop, "confirm_name": str_prop},
                ["id_or_name", "confirm_name"],
            ),
            _schema(
                "reveal_secret",
                "Reveal one secret value for an explicit user-approved task.",
                {
                    "id_or_name": str_prop,
                    "requesting_agent": opt_str,
                    "tool": opt_str,
                    "purpose": opt_str,
                },
                ["id_or_name"],
            ),
            _schema(
                "reveal_secret_env",
                "Reveal secrets as environment variables for an explicit user-approved task.",
                {"scope": opt_str, "requesting_agent": opt_str, "purpose": opt_str},
            ),
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        try:
            return _json_response(self._dispatch_tool(tool_name, args or {}))
        except Exception as exc:
            return tool_error(str(exc))

    def _client(self) -> GlyphHoldClient:
        if self.client is None:
            self.initialize(self.session_id or "default")
        if self.client is None:
            raise GlyphHoldError("Glyph Hold client is not initialized")
        return self.client

    def _dispatch_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        client = self._client()
        if tool_name == "glyphhold_health":
            return client.health()
        if tool_name == "list_categories":
            return client.list_categories()
        if tool_name == "list_memories":
            return client.list_memories(**args)
        if tool_name == "get_memory":
            return client.get_memory(args["memory_id"])
        if tool_name == "search_memories":
            return client.search_memories(**args)
        if tool_name == "prefetch_memories":
            return client.prefetch_memories(**args)
        if tool_name == "find_similar_memories":
            return client.find_similar_memories(**args)
        if tool_name == "prepare_memory_write":
            return client.prepare_memory_write(**args)
        if tool_name == "create_memory":
            return client.create_memory(**args)
        if tool_name == "update_memory":
            memory_id = args.pop("memory_id")
            return client.update_memory(memory_id, **args)
        if tool_name == "update_memory_confidence":
            return client.update_memory_confidence(**args)
        if tool_name == "archive_memory":
            return client.archive_memory(args["memory_id"])
        if tool_name == "supersede_memory":
            return client.supersede_memory(**args)
        if tool_name == "list_memory_revisions":
            return client.list_memory_revisions(args["memory_id"])
        if tool_name == "restore_memory_revision":
            return client.restore_memory_revision(**args)
        if tool_name == "delete_memory":
            memory = client.get_memory(args["memory_id"])
            if memory.get("title") != args["confirm_title"]:
                raise GlyphHoldError("confirm_title must match the current memory title")
            return {"deleted": client.delete_memory(args["memory_id"]) is None}
        if tool_name == "search_secrets":
            return client.search_secrets(**args)
        if tool_name == "get_secret_metadata":
            return client.get_secret_metadata(args["id_or_name"])
        if tool_name == "create_secret":
            return client.create_secret(**args)
        if tool_name == "update_secret":
            id_or_name = args.pop("id_or_name")
            return client.update_secret(id_or_name, **args)
        if tool_name == "delete_secret":
            secret = client.get_secret_metadata(args["id_or_name"])
            if secret.get("name") != args["confirm_name"]:
                raise GlyphHoldError("confirm_name must match the current secret name")
            return {"deleted": client.delete_secret(args["id_or_name"]) is None}
        if tool_name == "reveal_secret":
            return client.reveal_secret(**args)
        if tool_name == "reveal_secret_env":
            return client.reveal_secret_env(**args)
        raise GlyphHoldError(f"Unknown Glyph Hold tool: {tool_name}")


def register(ctx: Any) -> None:
    ctx.register_memory_provider(GlyphHoldMemoryProvider())
