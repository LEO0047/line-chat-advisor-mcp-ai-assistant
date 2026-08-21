---
name: line-chat-advisor
description: |
  Use whenever the user asks to read LINE messages, inspect a named LINE chat,
  decide whether to reply, continue a conversation, understand relationship
  context, or draft one reply in the user's style. This skill is read-only with
  respect to LINE and must never send or enter a message.
---

# LINE Chat Advisor

Read one exact LINE chat through the `line-readonly` MCP, refresh it with DB
evidence, maintain private local relationship context, and return at most one
reply suggestion. All visible output is in Traditional Chinese (Taiwan).

## Non-negotiable safety

- Never use Computer Use, Chronicle, OCR, screenshots, browser automation, or
  coordinates for LINE.
- Never call or invent `send_message`, `select_chat`, generic input, clipboard,
  keyboard, Shell, AppleScript, or arbitrary app-control tools.
- Never type, paste, focus a composer, press Return/Enter, or send anything.
- Never call `sync_older_messages` for an ordinary latest-message request.
- Never upload or quote private chat history outside the current answer. Do not
  put chat text, chat IDs, snapshots, or relationship context in a repository.
- Suggestions appear only in Codex.

## Capability gate

Before reading any chat, confirm that the callable tool list contains all five
operations from the `line-readonly` MCP, regardless of namespace prefix:

1. `line_status`
2. `refresh_latest`
3. `get_relationship_context`
4. `read_history`
5. `save_relationship_context`

If any operation is absent, stop. Return one line telling the user the
`line-readonly` catalog is stale and Codex must be reloaded. Do not substitute
another tool or an older snapshot.

## Required workflow

1. Resolve the requested contact from the exact display name or a previously
   successful `chatId`. Never guess between duplicate names.
2. Call `line_status`. Continue only when `dbFound`, `keyAvailable`, `decryptOk`,
   `databaseSnapshot.readOnly`, and `databaseSnapshot.queryOnly` are all true.
3. Call `refresh_latest` for the exact chat with `timeoutSeconds: 30`.
   Continue when `ok: true` and `freshnessVerified: true`; `targetAdvanced`
   separately states whether a newer target message appeared. For
   `freshnessVerified: false`, `locked_session_unavailable`, focus/launch
   failure, or any other error, stop without calling context or history tools.
4. Call `get_relationship_context` for the same exact chat.
5. If `initialized` is false, call `read_history` with `order: "oldest"` and
   `limit: 200`; follow every `pagination.nextCursor` until `hasMore` is false.
   Only after all pages succeed, synthesize and save the relationship context.
6. If `initialized` is true, call `read_history` with `order: "oldest"`,
   `limit: 200`, and `cursor: summaryThrough`; paginate until `hasMore` is false.
   Merge only the newly read messages into the saved context.
7. Set `summaryThrough` to the timestamp/message ID of the newest message that
   was actually read. Save these four concise, evidence-based fields:
   `relationship`, `stableBackground`, `toneNotes`, and `avoidReasking`.
   Never turn an inference into a fact.
8. Call `save_relationship_context` with the context version returned by the
   preceding get. On `context_version_conflict`, get the current context, rebase
   once, and retry once. If it conflicts again, stop.

Do not save context when pagination is incomplete, no message was read, or the
new cursor was not verified by `read_history`.

## Relationship and reply judgment

Use the complete saved context plus newly read messages. Give more weight to
recent conversational state than old tone patterns.

Reply when the newest inbound message contains a direct question, actionable
request, emotional bid, meaningful new information needing acknowledgment, or
a natural opening the user would normally continue.

Do not reply when the newest message is outgoing, the exchange has naturally
closed, it is only a reaction/attachment with no reliable meaning, or a reply
would reopen the conversation without benefit. Never invent the content of a
sticker, image, video, call, file, or deleted message.

Infer the user's style from their real outgoing messages. Prefer one short,
natural sentence; at most one question; no customer-service tone, invented
experience, fake emotion, schedule, promise, or sudden over-intimacy.

## Exact visible output

- Reply needed: output only the proposed LINE sentence. No header, quotation
  marks, analysis, alternatives, or explanation.
- No reply needed: output exactly `現在不用回。`
- Blocked or freshness unverified: output one concise sentence naming the
  blocker and do not include a draft.

Never transmit the proposed sentence to LINE.
