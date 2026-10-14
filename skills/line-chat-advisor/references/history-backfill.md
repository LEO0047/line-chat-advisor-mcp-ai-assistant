# Older-history loading

Use only for an explicit request to load older missing LINE history. It does
not make a normal recent-message request require foreground LINE.

## Failure diagnosis

The old full-window Peekaboo observation can block in synchronous AX child
traversal before searching. An expired snapshot and a newly generated snapshot
also have different element IDs: never reuse an old result ID after observing
again. Neither timeout proves the chat/group does not exist.

The repaired MCP adapter requires the exact chat already open and focused. It
only locates a unique history list adjacent to the composer by structural
metadata, bounds each AX call, and delivers bounded wheel-only events to LINE.
It never searches, clicks, activates, reads message values, or uses keys. A
missing title/list or changed target fails explicitly. Build the reviewed
helper with the project's `scripts/build-history-helper`; don't restore the
old snapshot-ID click script.

## Computer tool workflow

When available and authorized for historical loading, use the computer tool
to open the exact chat and scroll its message list. Inspect its own fresh state
after actions. Setting a value is allowed ONLY on the structurally confirmed
main-window search field; never the message field. Select only one exactly
named visible search result and verify the resulting window title before
scrolling. Do not press Return, click call buttons, or type/paste into a chat.
Names, window titles, and pane structure are UI metadata. Screenshots may
confirm the target/pane but must not substitute for MCP chat-text reads.

Prefer the identified history-list element for scrolling. If LINE exposes no
list element, use a fresh screenshot only to locate the message body's interior,
well clear of the header, call controls, composer, and attachments' buttons.
Never reuse coordinates after changing/resizing/reopening a window. If the
computer tool reports `noWindowsAvailable`, rebind once and inspect/raise the
observed window. If that remains unavailable, stop UI input for that target;
do not substitute global coordinate clicks, keyboard tricks, permission changes,
or manual-export instructions.

## Verification and stopping

Run `line_status` and the normal context/history workflow after loading. Compare
the source's message count and oldest timestamp with the pre-load baseline.
`ok: true` or more recent messages does not establish older-history progress.
If older data appears, paginate through all of it and review the full date range,
not only keyword hits. Save/export only actual MCP-returned messages.

After bounded attempts without an earlier timestamp, report the retrieved range
as partial and the specific remaining failure. Do not infer that earlier server
history is empty, expired, or complete, and do not claim the original user request
is finished. Keep local snapshot refresh distinct from server synchronization.
