# Programmatic attachment download

Use this mode when the user requests LINE files or images saved locally. Use code only:
do not click LINE windows, use CUA, request an unlocked screen, or fall back to
the native Save dialog. Distinguish an implemented downloader from a successfully
downloaded real attachment.

## Workflow

1. Use the MCP chat workflow to identify exact chatId/messageId, filename and
   date range. A transcript already read in this session can identify requested
   files; a new transcript is unnecessary unless freshness matters. “檔案”
   defaults to file messages, not photos or videos. When images are requested,
   include image messages too. Include both directions unless the user specifies
   received/sent only; state the actual counts and direction when useful.
2. Call `get_attachment_info` for declared size and expiry. Metadata does not
   prove availability and is not a downloaded file.
3. Call `download_attachment` with `chat`, `messageId` and absolute
   `destinationDir` under Downloads. It supports file and image messages with
   a declared positive size up to 64 MiB.
   It verifies encrypted-file HMAC, decrypts in memory, checks declared size
   and PDF magic when relevant, and saves atomically without overwriting.
   It returns path, byte count, SHA-256 and the non-secret `authSource`.
   Images use encrypted `emi` or plain `m`, never the preview/thumbnail path.
   Their original downloaded bytes are saved without re-encoding; image decoding
   verifies the format/dimensions/frames first, within bounded pixel/frame limits.
   Supported formats: JPEG, PNG, GIF, WebP, BMP and TIFF. Names use message time,
   a stable short ID hash and the detected extension. The bytes match LINE's
   stored attachment, not necessarily the sender's pre-upload camera original.
4. Report verified saved-file links and actual failed-download errors. Do not
   substitute a preview, screenshot or summary, or execute downloaded files.

If the MCP catalog is stale after upgrading, invoke the same fixed operation
through the project `.venv/bin/python3 src/line-readonly-mcp/bridge.py` with one
properly shell-quoted JSON argument containing `operation` and `args`. Never
put private identifiers in a repository or hard-code them in helpers/tests.
The next MCP server session advertises the added tools automatically.

## Authentication and failures

The local DB key only decrypts the database; it is not an OBS login token.
The downloader now reuses Desktop's cached OBS credential from
`_setting` → `setting` → `obsTokenGeneral.token` in the **same fresh read-only
snapshot** as the requested message. It stays inside the process and is not
copied into another file or Keychain item. Each download rereads this cache;
the LINE Desktop client owns its renewal. Do not claim an independent login or
token-refresh service, or infer validity from the opaque cached timestamp.

Only when that cache is absent, an already provisioned Login Keychain item at
service `line-readonly-obs-token` can be used. Malformed or rejected native
credentials do not trigger fallback or automatic login. No environment-variable,
plaintext-file, tool-argument or GUI fallback is allowed for secrets. Do not ask
the user to paste credentials in chat. If a new credential handoff is needed,
use the available secure-secret-handoff skill.

The attachment key is used only inside the bridge from a read-only snapshot.
Never print raw contentInfo, keys, tokens, headers or private URLs. Network
requests go only to the fixed HTTPS LINE OBS endpoint; redirects fail closed.
`X-Talk-Meta` must bind the requested message for plain `m` and encrypted
`emf`/`emi` objects. Omitting it can produce 401 even with a valid Desktop credential.
There is no token refresh/login automation and no retry loop for 401/403.

`attachment_auth_required` means neither credential source is available.
`attachment_auth_invalid` means a malformed credential/cache. A 401/403 means
the server rejected the authorization request, not necessarily that the token
is expired and not evidence that the file is expired.
Integrity/size/format failures save no final file. Unsupported media types and
files over the limit are reported explicitly. Videos/audio, external download
URLs and unsupported object routes do not trigger fallback. If a batch has
failures, finish the other in-scope attachments and report saved/skipped/failed
separately; never claim all downloaded when an item failed.

No LINE window is needed. Locked-screen success still depends on accessible
Keychain/local DB/network, a valid token and an available server object. Do not
claim full sync or unconditional locked-screen downloads.

## Implementation references

Unofficial protocol references; authenticated interoperability must be tested:
- https://github.com/beeper/line/blob/main/pkg/line/client.go — OBS read endpoint
- https://github.com/beeper/line/blob/main/pkg/connector/media.go — FileEncryption
  HKDF layout and AES-CTR file format
- https://github.com/beeper/line/issues/191 — why the HMAC must be verified
- https://github.com/beeper/line/blob/main/pkg/connector/handlers/image.go — image
  `emi`/`m` routing; use the same authenticated file decryption with HMAC checks

Generated encrypted fixtures validate code behavior, not account authentication.
Report these separately from a real successful download.
