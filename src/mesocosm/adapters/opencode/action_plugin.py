"""A plugin for OpenCode's documented, awaited tool.execute.before extension point.

No harness internals are changed. The plugin must be mounted read-only outside the task workspace.
A permitted tool can still launch subprocesses: this hook is not a syscall security boundary.
"""
from __future__ import annotations

import hashlib
import json

PROTOCOL = "tool-gate-v1"


def render_action_plugin(gateway_url: str, api_key: str, timeout_seconds: float = 30.0) -> str:
    if not 0 < timeout_seconds <= 600:
        raise ValueError("tool approval timeout must be between 0 and 600 seconds")
    config = json.dumps({"url": gateway_url.rstrip("/"), "key": api_key,
                         "timeout": int((timeout_seconds + 10) * 1000)}, separators=(",", ":"))
    return '''import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const config = CONFIG;
export const ApprovalGate = async () => {
  const instance = randomUUID();
  const digest = createHash("sha256").update(await readFile(fileURLToPath(import.meta.url))).digest("hex");
  const post = async (path, body) => {
    const response = await fetch(config.url + path, {
      method: "POST",
      headers: { "Authorization": "Bearer " + config.key, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(config.timeout),
    });
    if (!response.ok) throw new Error("Tool approval unavailable");
    return await response.json();
  };
  const ready = await post("/v1/actions/ready", {
    instance_id: instance, plugin_sha256: digest, protocol: "tool-gate-v1",
  });
  if (ready.ok !== true || ready.protocol !== "tool-gate-v1") throw new Error("Tool approval unavailable");
  return {
    "tool.execute.before": async (input, output) => {
      if (typeof input.sessionID !== "string" || typeof input.callID !== "string" ||
          typeof input.tool !== "string" || typeof output.args !== "object" ||
          output.args === null || Array.isArray(output.args)) throw new Error("Unsupported tool invocation");
      const nonce = randomUUID();
      let decision;
      try {
        decision = await post("/v1/actions/authorize", {
          instance_id: instance, nonce, session_id: input.sessionID,
          call_id: input.callID, tool: input.tool, args: output.args,
        });
      } catch {
        throw new Error("Tool approval unavailable; action blocked");
      }
      if (decision.allow !== true || decision.nonce !== nonce ||
          decision.session_id !== input.sessionID || decision.call_id !== input.callID ||
          typeof decision.action_id !== "string") throw new Error("Tool action denied");
    },
  };
};
'''.replace("CONFIG", config)


def action_plugin_digest(gateway_url: str, api_key: str, timeout_seconds: float = 30.0) -> str:
    return hashlib.sha256(render_action_plugin(gateway_url, api_key, timeout_seconds).encode()).hexdigest()
