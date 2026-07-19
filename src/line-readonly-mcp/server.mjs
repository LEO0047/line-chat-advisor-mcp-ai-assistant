#!/usr/bin/env node

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(here, "../..");
const python = join(projectRoot, ".venv", "bin", "python3");
const bridge = join(here, "bridge.py");

export const ALLOWED_TOOLS = Object.freeze([
  "line_status",
  "list_chats",
  "read_history",
  "refresh_latest",
  "get_relationship_context",
  "save_relationship_context",
  "sync_older_messages",
  "line_health",
]);
const allowed = new Set(ALLOWED_TOOLS);

export const TOOLS = Object.freeze([
  {
    name: "line_status",
    description: "Refresh and verify a new stable read-only snapshot of the local LINE DB, then report discovery, Keychain availability, decryption, LINE process state, and snapshot time without returning secrets or chat content. Call this before every LINE read task.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "list_chats",
    description: "List recent chats from a stable read-only snapshot of LINE's local encrypted DB. Does not require LINE to be foreground or running.",
    inputSchema: {
      type: "object",
      properties: { limit: { type: "integer", minimum: 1, maximum: 200, default: 50 } },
      additionalProperties: false,
    },
  },
  {
    name: "read_history",
    description: "Read bounded local history by exact display name or chatId. Every call opens a new stable read-only snapshot of the current source DB and returns its refresh time. Use order=oldest for the first cursor page, then pass the returned timestampMs+messageId cursor. Exact duplicate names return candidate chatIds instead of guessing. Successful reads are deduplicated into local memory.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", minLength: 1, maxLength: 512 },
        limit: { type: "integer", minimum: 1, maximum: 200, default: 20 },
        order: { type: "string", enum: ["latest", "oldest"], default: "latest" },
        cursor: {
          type: "object",
          properties: {
            timestampMs: { type: "integer", minimum: 0 },
            messageId: { type: "string", minLength: 1, maxLength: 256 },
          },
          required: ["timestampMs", "messageId"],
          additionalProperties: false,
        },
      },
      required: ["chat"],
      additionalProperties: false,
    },
  },
  {
    name: "refresh_latest",
    description: "Wait for one exact chat to advance in fresh stable read-only LINE DB snapshots. It first polls passively, then performs only a fixed LINE app activation or fixed LINE app launch when needed. It never selects a chat, focuses the composer, types, pastes, presses keys, or sends messages. Call read_history separately after success.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", minLength: 1, maxLength: 512 },
        timeoutSeconds: { type: "integer", minimum: 5, maximum: 60, default: 30 },
      },
      required: ["chat"],
      additionalProperties: false,
    },
  },
  {
    name: "get_relationship_context",
    description: "Read the local-only relationship summary and its imported-message cursor for one exact LINE chat. This may initialize or migrate the private local memory schema, but it never modifies LINE's source DB and never uses UI automation.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", minLength: 1, maxLength: 512 },
      },
      required: ["chat"],
      additionalProperties: false,
    },
  },
  {
    name: "save_relationship_context",
    description: "Save bounded model-derived relationship context only to private local memory for one exact chat. The cursor must already exist in read_history memory and expectedContextVersion prevents stale overwrites. It never modifies LINE's source DB, controls LINE, or sends a message.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", minLength: 1, maxLength: 512 },
        expectedContextVersion: { type: "integer", minimum: 0 },
        summaryThrough: {
          type: "object",
          properties: {
            timestampMs: { type: "integer", minimum: 0 },
            messageId: { type: "string", minLength: 1, maxLength: 256 },
          },
          required: ["timestampMs", "messageId"],
          additionalProperties: false,
        },
        relationship: { type: "string", maxLength: 4000 },
        stableBackground: { type: "string", maxLength: 4000 },
        toneNotes: { type: "string", maxLength: 4000 },
        avoidReasking: { type: "string", maxLength: 4000 },
      },
      required: [
        "chat",
        "expectedContextVersion",
        "summaryThrough",
        "relationship",
        "stableBackground",
        "toneNotes",
        "avoidReasking",
      ],
      additionalProperties: false,
    },
  },
  {
    name: "sync_older_messages",
    description: "Best-effort, narrowly constrained older-history UI backfill. Do not use it to refresh ordinary latest-message reads: use line_status, refresh_latest, then read_history. It can only resolve an existing exact chat and use the audited Peekaboo adapter; it never focuses the composer, types/pastes a message, or presses Return.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", minLength: 1, maxLength: 512 },
        rounds: { type: "integer", minimum: 1, maximum: 20, default: 5 },
      },
      required: ["chat"],
      additionalProperties: false,
    },
  },
  {
    name: "line_health",
    description: "Return a redacted health report for DB snapshots, Keychain, decrypt status, memory DB, MCP allow-list, Peekaboo, permissions, and power/Remote evidence.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
]);

async function callBridge(operation, args = {}) {
  const payload = JSON.stringify({ operation, args });
  try {
    const { stdout } = await execFileAsync(python, [bridge, payload], {
      cwd: projectRoot,
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
      env: {
        PATH: `/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`,
        LANG: "C.UTF-8",
        LC_ALL: "C.UTF-8",
        LINE_CONTEXT_RELAY_ROOT: projectRoot,
      },
    });
    return JSON.parse(stdout);
  } catch (error) {
    const text = String(error?.stdout || "").trim();
    if (text) {
      try { return JSON.parse(text); } catch { /* use redacted error below */ }
    }
    return { ok: false, error: "bridge_failed", detail: String(error?.message || error).replace(projectRoot, "[PROJECT]") };
  }
}

function textResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }], isError: value?.ok === false };
}

export function createServer() {
  const server = new Server(
    { name: "line-readonly-mcp", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const name = request.params.name;
    if (!allowed.has(name)) return textResult({ ok: false, error: "tool_not_allowed", tool: name });
    const args = request.params.arguments ?? {};
    return textResult(await callBridge(name, args));
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const server = createServer();
  await server.connect(new StdioServerTransport());
  console.error("line-readonly-mcp ready on stdio");
}
