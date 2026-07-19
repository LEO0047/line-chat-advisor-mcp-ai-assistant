# line-cua-mcp Security Audit

## Summary

Audit target: archival fork commit `f83d70fcae9a6daa350909053017af9f6794d59c` (three commits). The original GitHub repository was publicly indexed but returned HTTP 404 and `Repository not found` to live Git/GitHub API access on 2026-07-19. Two surviving forks reported the same HEAD; provenance is therefore strong enough for code review, but not equivalent to a currently verifiable original upstream.

Verdict: **not suitable for direct registration on this Mac**. It is acceptable only as a reviewed reference behind our own read-only implementation. The upstream server exposes message sending and general UI-driver access, uses a now-obsolete DB filename pattern on LINE 26.3.0, and lacks the required DB safety tests. No dependency was installed and no upstream runtime script was executed before this audit.

## Acceptable parts

- `package.json:18-23` has no `preinstall`, `install`, `postinstall`, or `prepare` lifecycle hook. Build copies two checked-in helpers; test builds then runs a local matcher test.
- `src/linedb.py:70-84` copies the main DB and available `-wal`/`-shm` files into a temporary directory, then opens only the copy with `SQLITE_OPEN_READONLY`.
- SQL used by the read path is `SELECT`-only. Cipher PRAGMAs configure the copied connection; the source DB is never opened by SQLite.
- Temp DB cleanup is registered with `atexit`; OCR screenshots use `finally` cleanup in `src/ocr.ts:93-104`.
- Static source review found no `fetch`, HTTP client, telemetry SDK, analytics, upload, or network endpoint in project code. Normal package installation and LINE's own sync/telemetry remain external network activity.
- Runtime dependencies declared in `package.json` match the imports: `@modelcontextprotocol/sdk` and `zod`; TypeScript/tsx and Node types are development-only. The lockfile pins package checksums.

## Must change before use

- `src/server.ts:24-123` advertises `select_chat` and `send_message`; `src/server.ts:158-193` executes them. Direct registration violates the hard read-only requirement.
- `src/driver.ts:71-76` accepts an arbitrary cua-driver tool name internally. Even if the public tool list were edited, importing this layer would leave a broad UI-automation capability in the trusted process.
- `src/verify-cua.ts:145-151` intentionally sends a real message. It must never be run in this workspace.
- `src/linedb.py:28-46` only searches `qwb*.edb`. Live inspection of LINE 26.3.0 found the current main DB uses `qw<hash>.edb`, so upstream discovery fails on this Mac.
- `src/linedb.py:49-59` accepts `LINE_DB_KEY` from the environment before Keychain. This violates the Keychain-only policy and makes accidental process/log exposure easier.
- `src/linedb.py:70-76` copies live DB files once without verifying source metadata stayed stable during the multi-file copy. The source is not modified, but a concurrent LINE write can yield an inconsistent snapshot. Our adapter uses metadata-before/after retries and fails closed if it cannot obtain a stable copy.
- Cleanup depends on process exit. Our adapter uses a scoped temporary directory and closes the connection before cleanup, while retaining a startup sweep for abandoned snapshots.
- `src/linedb.py:140-158` returns only display names for ambiguous matches, not candidate `chatId`s. It also permits fuzzy substring resolution; our read path requires exact name or raw `chatId` and returns full candidates.
- `src/linedb.py:166-187` emits timezone-naive timestamps and collapses content type into the text body. Our schema returns offset-aware absolute time plus numeric and named message type fields.
- `read_history` defaults to unlimited output. Our MCP defaults to 20 and enforces a bounded maximum to avoid unnecessary disclosure to the model.
- `export-md` can write a full transcript to a caller-selected path (`src/linedb.py:245-290`). It is not exposed by the upstream MCP, but our wrapper does not include or import it.

## Unconfirmed / residual risk

- The database schema and AES parameters cannot be validated until the user completes one-time key capture and the key is stored in Login Keychain.
- Capturing a LINE account key from a re-signed app copy may be incompatible with future LINE/macOS releases and may conflict with LINE's Terms of Service. This audit is technical, not legal advice.
- `apsw-sqlite3mc` is a third-party native dependency. Its exact artifact must be pinned, installed into the project venv, and verified separately.
- LINE itself can contact LINE/Sentry endpoints when launched. This behavior is outside the audited server and is especially relevant during the one-time re-signed-copy login.
- The original upstream is currently unavailable, so commit authorship/signature and fork relationship cannot be revalidated against GitHub's live parent metadata.

## Actual data flow

1. Find the local LINE encrypted DB in LINE's sandbox container.
2. Read the DB key from Login Keychain service `line-cua-mcp-dbkey` without printing it.
3. Copy main DB plus present WAL/SHM files to a private temporary directory.
4. Open only that temporary copy read-only with wxSQLite3 AES-128-CBC parameters.
5. Run bounded `SELECT` queries and normalize chat/message metadata.
6. For successful history reads, write normalized messages and deterministic factual coverage into the ignored local memory SQLite database.
7. Delete the temporary DB copy before returning.

## Actual external connections

- Project source: none found.
- Dependency acquisition: npm/PyPI/GitHub only during explicit installation/update.
- LINE Desktop: normal account sync and its own telemetry are independent of the read wrapper.
- `sync_older_messages`: causes LINE Desktop to fetch older messages from LINE servers; this is expected and must be reported as a UI/network operation.

## Key handling

- Upstream: environment variable or Keychain.
- Final wrapper policy: Login Keychain only; only a boolean availability result is returned. Full key values are never logged, returned by MCP, stored in files, or passed on the command line.

## Test coverage assessment

Upstream `test/matcher.test.mjs` covers send-target name matching and group safeguards only. It does **not** cover DB path discovery, live-copy stability, read-only flags, decryption failure, Keychain absence, read-name ambiguity, timestamp/direction/content-type parsing, temp cleanup, LINE-closed reads, or the MCP allow-list. Those tests are implemented in this workspace rather than patched into the vendor tree.

## Dependency artifact follow-up

The macOS arm64 CPython 3.14 wheel for `apsw-sqlite3mc==3.51.0.0` was downloaded without installation and inspected as a ZIP. It contains the APSW Python package, tests, metadata, and two native `.so` extensions; its entry point is the local `apsw` shell. No install-time script is present. The wheel is pinned in `requirements.lock` with SHA256 `2b44393be2508c9e0e620fce8092ea99d97c064b8fd878ace36c8df9dd612cc7`.
