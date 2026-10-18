"""The world's own MCP server (ADR-0021): the team board and the wiki as Model Context Protocol tools.

Served by the services container under the `mcp` hostname with the Streamable HTTP transport in its
simplest form: the client POSTs JSON-RPC messages, the server answers each with a JSON body (never a
stream), acknowledges notifications with 202 and refuses GET (no server-initiated stream). Tool calls
are the same board and wiki operations as the REST API and are logged the same way, by the caller's
address, with `via: mcp` and the tool name. Nothing here reaches past the services.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "team-tools", "version": "1.0.0"}

TOOLS: list[dict[str, Any]] = [
    {"name": "board_list", "description": "List the newest posts on the team board (title, author, id).",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
         "offset": {"type": "integer", "minimum": 0, "default": 0}}}},
    {"name": "board_read", "description": "Read one board post with its replies.",
     "inputSchema": {"type": "object", "properties": {"post_id": {"type": "integer"}}, "required": ["post_id"]}},
    {"name": "board_post", "description": "Publish a post on the team board.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                     "required": ["body"]}},
    {"name": "board_reply", "description": "Reply to a board post.",
     "inputSchema": {"type": "object", "properties": {"post_id": {"type": "integer"}, "body": {"type": "string"}},
                     "required": ["post_id", "body"]}},
    {"name": "wiki_list", "description": "List the wiki pages that are currently published.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "wiki_read", "description": "Read a wiki page as markdown.",
     "inputSchema": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}},
]

ToolFn = Callable[[dict[str, Any]], str]


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def parse(body: bytes) -> dict[str, Any]:
    try:
        msg = json.loads(body)
    except json.JSONDecodeError as e:
        raise JsonRpcError(-32700, f"parse error: {e}") from e
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        raise JsonRpcError(-32600, "invalid request: a single JSON-RPC 2.0 request or notification is expected")
    return msg


def handle(msg: dict[str, Any], tools: dict[str, ToolFn]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. None means a notification (the HTTP layer answers 202).
    `tools` maps a tool name to a function of its arguments returning the text content."""
    method, params, msg_id = msg["method"], msg.get("params") or {}, msg.get("id")
    if "id" not in msg:                     # a notification: initialized, cancelled, progress ...
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[0])
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(msg_id, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        fn = tools.get(name)
        if fn is None:
            return _error(msg_id, -32602, f"unknown tool {name!r}")
        try:
            text = fn(params.get("arguments") or {})
        except ToolError as e:
            return _result(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
    if method in ("resources/list", "resources/templates/list", "prompts/list"):
        key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates",
               "prompts/list": "prompts"}[method]
        return _result(msg_id, {key: []})
    return _error(msg_id, -32601, f"method not found: {method}")


class ToolError(Exception):
    """A tool call that fails for the caller's reasons (no such post, empty body): an isError result."""
