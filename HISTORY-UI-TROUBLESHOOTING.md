# Historical backfill: window-read timeout recovery

## Separate the failing layers

1. `line_status` / `line_health`: database discovery, decryption, read-only snapshot, permissions.
2. Bounded native AX inspection: whether LINE exposes a window and its immediate structure.
3. Computer-tool app selection/window read.
4. Exact-chat selection and message-list scrolling.
5. Fresh `read_history`: oldest timestamp, count, pagination and newly inserted messages.

Success at one layer does not establish success at the next. A running app is
not a usable chat window; successful scrolling is not historical backfill;
`hasMore=false` only exhausts the local snapshot, not remote history.

## Diagnose once, then change the tested condition

- Record the original app-selection request, elapsed time and exact error.
  Do not repeatedly submit an identical timed-out request.
- Compare it with the existing bounded native AX inspector and database
  health check. Inspect only structure; chat text still comes from MCP.
- If the native probe is fast but the computer tool times out, inspect the
  exact computer-use service process and take a short process sample.
  Store raw diagnostics privately, outside the repository.
- Distinguish stale service state from an app-path mismatch and a blocked AX
  traversal. A process sample is supporting evidence, not proof of causality.

## Narrow recovery, not an automatic process killer

When explicitly authorized to repair the failing computer-use service, verify
its current executable and PID and end only that process gracefully. Warn that
other computer-use sessions may briefly be interrupted. Do not reuse old PIDs,
use broad process-name kills, terminate LINE, log out, alter permissions or
security policy, or write to LINE's database. If the service does not recover,
stop and report that condition; do not escalate into repeated restarts.

Reset the computer-tool session and follow its documented initialization flow.
Retest the original app-selection request, not just an alternate path. Then
verify a real window read, exact chat identity and bounded history scrolling.
If a window is unavailable, reacquire once and inspect it. If that fresh tree
exposes `Raise` on the identified window itself, raise that window (never the
composer), obtain fresh state, and test one bounded message-list scroll. A
repeated failure stops input for that target. Do not loop app rebinding or
service restarts. Never use stale element indices or coordinates after switching or
resizing windows. Do not touch the composer, send, call or deletion controls.

## Acceptance and limits

Refresh the exact chat, load its saved context, read incrementally, and read the
requested history from the oldest cursor with all required pages. Compare
before/after oldest time and message count. Save original screenshots privately
with an index and hashes when requested. A UI date later than the database's
oldest date is a discrepancy to report, not a reason to discard older records.

Observed on 2026-10-12: a 120-second app-selection timeout coexisted with a
0.17-second native structural read. After a narrow computer-use service restart,
the original bundle-ID selection succeeded in 0.268 seconds, followed by real
chat selection and scrolling. LINE's process was unchanged. This establishes
runtime recovery, not a source-code fix or a guarantee against recurrence.
A later `noWindowsAvailable` during scrolling was a separate remaining failure.

Follow-up the same day: fresh window inspection plus its exposed `Raise` action
allowed the identified main-window message list to scroll again. Screenshots
confirmed reaching the same earliest date already present in the read-only
database, resolving the previously observed UI/database boundary mismatch.
The subsequent full MCP read was unchanged. This is evidence of a usable
window-routing recovery, not evidence that older server history was retrieved
or that raising alone is the proven root cause of the earlier failure.

The external service implementation is not part of this repository. Do not
claim a unit-tested permanent fix to that service from this recovery procedure.
Server completeness remains unknown without an authoritative acknowledgement.
