import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { ALLOWED_TOOLS, TOOLS } from "../src/line-readonly-mcp/server.mjs";

const expected = [
  "line_status",
  "list_chats",
  "read_history",
  "refresh_latest",
  "get_relationship_context",
  "save_relationship_context",
  "sync_older_messages",
  "line_health",
];

test("MCP exposes only the read-only allow-list", () => {
  assert.deepEqual(ALLOWED_TOOLS, expected);
  assert.deepEqual(TOOLS.map((tool) => tool.name), expected);
});

test("message and generic automation tools are absent", () => {
  const names = new Set(TOOLS.map((tool) => tool.name));
  for (const forbidden of ["send_message", "select_chat", "type", "paste", "press", "hotkey", "composer", "shell", "applescript"]) {
    assert.equal(names.has(forbidden), false, `${forbidden} must not be exposed`);
  }
});

test("refresh_latest exposes only exact chat and bounded timeout inputs", () => {
  const tool = TOOLS.find((candidate) => candidate.name === "refresh_latest");
  assert.deepEqual(tool.inputSchema.required, ["chat"]);
  assert.equal(tool.inputSchema.properties.timeoutSeconds.minimum, 5);
  assert.equal(tool.inputSchema.properties.timeoutSeconds.maximum, 60);
  assert.equal(tool.inputSchema.properties.timeoutSeconds.default, 30);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties), ["chat", "timeoutSeconds"]);
});

test("relationship context tools are exact, bounded, and local-memory only", () => {
  const getTool = TOOLS.find((candidate) => candidate.name === "get_relationship_context");
  assert.deepEqual(getTool.inputSchema.required, ["chat"]);
  assert.deepEqual(Object.keys(getTool.inputSchema.properties), ["chat"]);
  assert.equal(getTool.inputSchema.additionalProperties, false);

  const saveTool = TOOLS.find((candidate) => candidate.name === "save_relationship_context");
  assert.equal(saveTool.inputSchema.properties.expectedContextVersion.minimum, 0);
  assert.deepEqual(saveTool.inputSchema.properties.summaryThrough.required, ["timestampMs", "messageId"]);
  assert.equal(saveTool.inputSchema.properties.summaryThrough.additionalProperties, false);
  for (const field of ["relationship", "stableBackground", "toneNotes", "avoidReasking"]) {
    assert.equal(saveTool.inputSchema.properties[field].maxLength, 4000);
  }
  assert.equal(saveTool.inputSchema.additionalProperties, false);
  assert.match(saveTool.description, /local memory/i);
  assert.match(saveTool.description, /never modifies LINE's source DB/i);
});

test("read_history exposes bounded timestamp and message ID cursor pagination", () => {
  const tool = TOOLS.find((candidate) => candidate.name === "read_history");
  assert.equal(tool.inputSchema.properties.limit.maximum, 200);
  assert.deepEqual(tool.inputSchema.properties.order.enum, ["latest", "oldest"]);
  assert.deepEqual(tool.inputSchema.properties.cursor.required, ["timestampMs", "messageId"]);
  assert.equal(tool.inputSchema.properties.cursor.additionalProperties, false);
});

test("real stdio MCP handshake lists the exact allow-list and calls line_status", async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [fileURLToPath(new URL("../src/line-readonly-mcp/server.mjs", import.meta.url))],
    stderr: "pipe",
  });
  const client = new Client({ name: "line-readonly-test", version: "0.1.0" });
  try {
    await client.connect(transport);
    const listed = await client.listTools();
    assert.deepEqual(listed.tools.map((tool) => tool.name), expected);
    const status = await client.callTool({ name: "line_status", arguments: {} });
    assert.equal(status.isError, false);
    const payload = JSON.parse(status.content[0].text);
    assert.equal(payload.ok, true);
    assert.equal(typeof payload.keyAvailable, "boolean");
    assert.equal(typeof payload.decryptOk, "boolean");
  } finally {
    await client.close();
  }
});

test("line-chat-advisor skill enforces refresh, incremental context, and one-line output", () => {
  const skill = readFileSync(new URL("../skills/line-chat-advisor/SKILL.md", import.meta.url), "utf8");
  const ordered = [
    "`line_status`",
    "`refresh_latest`",
    "`get_relationship_context`",
    "`read_history`",
    "`save_relationship_context`",
  ];
  let previous = -1;
  for (const operation of ordered) {
    const index = skill.indexOf(operation, previous + 1);
    assert.ok(index > previous, `${operation} must appear in required order`);
    previous = index;
  }
  assert.match(skill, /Continue[\s\S]*freshnessVerified: true/);
  assert.match(skill, /freshnessVerified: false[\s\S]*stop without calling context or history tools/);
  assert.match(skill, /order: "oldest"[\s\S]*pagination\.nextCursor/);
  assert.match(skill, /output exactly `現在不用回。`/);
  assert.match(skill, /Never use Computer Use, Chronicle, OCR, screenshots/);
  assert.match(skill, /Never transmit the proposed sentence to LINE/);
});
