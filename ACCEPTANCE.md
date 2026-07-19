# Acceptance status

Sanitized evidence captured on 2026-07-19 (Asia/Taipei). This document contains
no real contact name, chat ID, message text, key, database filename, screenshot,
or snapshot. `PASS` means exercised locally; `UNIT PASS` means covered with
synthetic data; `Provisional` requires a fresh device/session test.

## Tested environment

- Apple Silicon macOS development host
- LINE Desktop 26.3.x with current `qw<redacted>.edb` main-file pattern
- ChatGPT desktop with Codex/MCP support
- Pinned upstream commits recorded in `VENDOR_LOCK.md`
- Project-local Peekaboo 3.9.6 build from the reviewed commit

## Core verification

| Check | State | Evidence |
| --- | --- | --- |
| Stable DB/WAL/SHM snapshot | PASS | Source metadata is compared before/after copy; only the private copy is opened and the temp directory is removed. |
| Keychain-only key | PASS | Boolean availability and decryption checks pass without returning or logging the key. |
| Read while LINE is running | PASS | Exact-name bounded read completed without UI chat extraction. |
| Read while LINE is closed | PASS | Already-synchronized local history remained readable after LINE exited. |
| Source DB unchanged | PASS | Source digest and metadata were checked around the closed-read scenario. |
| Exact name and chatId | PASS | Exact resolver works; duplicate names return candidates instead of guessing. |
| Direction/time/type | PASS | Offset-aware timestamps, direction, text, stickers, and attachments are normalized. |
| Local memory and dedup | PASS | Repeated reads are idempotent and corrected classifications update in place. |
| Cursor pagination | PASS | Compound timestamp/message-ID pagination completed with unique IDs and no page overlap; private identifiers were not retained here. |
| MCP allow-list | PASS | Real stdio handshake lists only eight approved tools; send and generic automation tools are absent. |
| Temp key-capture cleanup | PASS | Temporary app copy, candidates, logs, and scan state were removed after validation. |
| Automated tests | PASS | Node and Python test suites pass without warnings. |

## `refresh_latest`

| Scenario | State | Evidence |
| --- | --- | --- |
| Passive target advance | UNIT PASS | Target advancement returns without focus or launch. |
| LINE running, fixed activation | PASS | Fixed `app switch --to LINE --verify --json --no-remote` completed without selecting a chat or using keyboard input. |
| No target update before timeout | PASS | Real bounded call returned `target_not_advanced` and all safety fields remained false. |
| Only another chat/global count advances | UNIT PASS | Unchanged target tuple is never reported as success. |
| LINE not running, fixed launch | UNIT PASS / Provisional live | Only the fixed bundle-ID launch command is permitted; a new clean-machine live test remains. |
| Locked graphical session | UNIT PASS / Provisional live | Locked/no-console state fails before UI action; device-specific live verification remains. |

The originally proposed Peekaboo `window focus --verify` path hung on the tested
LINE version. The reviewed `app switch --to LINE` path uses application
activation rather than the keyboard-producing `--cycle` path.

## Relationship context and global skill

| Check | State | Evidence |
| --- | --- | --- |
| Uninitialized context | PASS | Real stdio call returned version 0/local-only storage without emitting message content. |
| Imported cursor requirement | UNIT PASS | Save rejects a cursor not previously imported by `read_history`. |
| Incremental cursor | UNIT PASS | Cursor may advance or remain equal but cannot regress. |
| Concurrent task protection | UNIT PASS | Stale `expectedContextVersion` returns a version conflict. |
| Local-only storage | PASS | Local relationship memory is mode `0600` and ignored by Git. |
| Skill contract | PASS | Static test verifies capability gate, strict refresh stop, complete pagination, incremental context, single-line output, and no Computer Use/OCR/send fallback. |
| Global installation | PASS locally | The local Codex skill path resolves to the canonical repo skill. Each new clone must install its own symlink. |
| Fresh Codex task discovery | Provisional | MCP and skill catalogs load at task start and require a newly loaded task after installation. |

## ChatGPT Remote boundary

| Criterion | State |
| --- | --- |
| Local Codex task reads LINE and returns advice | PASS |
| ChatGPT Remote request reaches the paired Mac | Provisional; pairing is user/device/version specific |
| Locked-computer Remote workflow | Provisional; requires user-assisted device verification |
| Project sends a LINE message | Intentionally unsupported |

The Mac must remain powered on, online, and signed into a usable graphical
session for refresh actions. A synchronized local DB can still be read while
LINE itself is closed, but that does not fetch new remote messages.

## Known degradation

`sync_older_messages` is not accepted as a normal latest-message path. On the
tested LINE version, Peekaboo UI snapshots may time out. The wrapper fails
closed and does not fall back to typing, pasting, keypresses, screenshots, OCR,
or composer interaction. Stable local DB reads remain available.
