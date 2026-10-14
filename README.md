# LINE Context Relay

**LINE Context Relay — Read-only AI Chat Advisor for ChatGPT, Codex and MCP Agents**<br>
**LINE Context Relay — 給 ChatGPT、Codex 與 MCP Agent 使用的唯讀 LINE 聊天顧問**

[繁體中文](#繁體中文) · [English](#english) · [Security](SECURITY.md) · [Privacy](PRIVACY.md) · [MIT License](LICENSE)

A local-first, read-only LINE conversation assistant for macOS. It connects
ChatGPT/Codex to LINE Desktop's local encrypted database through a narrowly
allow-listed MCP server, refreshes the target chat using database evidence,
builds private relationship context, and suggests whether and how to reply.

**It never sends a LINE message.**

> Unofficial community project. Not affiliated with, endorsed by, or sponsored
> by LINE Corporation or OpenAI. Use only with your own account and data, and
> review the relevant product terms before use.

> **Published / 已發布：** [line-context-relay@0.2.0](https://www.npmjs.com/package/line-context-relay)，2026-10-14 17:53（Asia/Taipei）。正式安裝與限制：[INSTALL.md](INSTALL.md) · 維護：[MAINTAINERS.md](MAINTAINERS.md)。發布不代表已證實外部採用、第三方安全認證或完整相容性驗證。

## English

### What this project does

LINE Context Relay turns a Mac running LINE Desktop and ChatGPT/Codex into a
private conversation-advice workflow:

```mermaid
flowchart LR
    U["User or ChatGPT Remote"] --> C["ChatGPT / Codex on Mac"]
    C --> M["Allow-listed line-readonly MCP"]
    M --> S["Stable DB + WAL + SHM snapshot"]
    S --> R["Bounded chat history"]
    R --> X["Private local relationship context"]
    X --> A["One reply decision or suggestion"]
    A --> C
```

With ChatGPT Remote enabled in the user's desktop/mobile setup, a request can
start from a phone, run on the paired Mac, read the local LINE context, and
return a suggestion to ChatGPT. Remote availability, locked-computer behavior,
and pairing depend on the installed ChatGPT version and must be verified on the
user's own devices. The Mac must be powered on, online, and signed into a usable
graphical session for LINE refresh actions.

The project is intentionally an **advisor, not a bot that sends messages**.
Reply text is displayed in ChatGPT/Codex only.

### Core features

- Stable copies of LINE's encrypted main DB and present WAL/SHM sidecars
- `SQLITE_OPEN_READONLY` and `query_only` on the private snapshot only
- Login Keychain-only database key retrieval
- Exact display-name or `chatId` resolution; ambiguous names fail closed
- Bounded history reads with stable timestamp/message-ID cursor pagination
- Target-specific local snapshot refresh; server completeness remains unverified
- First-full-then-incremental local relationship context
- Optimistic context versions to prevent stale task overwrites
- A global `line-chat-advisor` Codex skill
- No send tool, generic input tool, arbitrary app target, OCR, screenshot, Shell
  MCP, or AppleScript MCP

### Safety architecture

The MCP exposes exactly ten operations:

| Tool | Purpose | Writes LINE source DB? |
| --- | --- | --- |
| `get_attachment_info` | Read bounded attachment metadata without keys | No |
| `download_attachment` | Fetch from fixed LINE OBS host, verify and save under Downloads | No |
| `line_status` | Verify DB discovery, Keychain access, decryption, and snapshot state | No |
| `list_chats` | List recent exact chat candidates | No |
| `read_history` | Read bounded history and deduplicate it into private local memory | No |
| `refresh_latest` | Passively poll, then activate or launch only LINE if needed | No |
| `get_relationship_context` | Read private local semantic context | No |
| `save_relationship_context` | Save bounded context to local memory with cursor/version checks | No |
| `sync_older_messages` | Optional, constrained older-history backfill; not used for normal reads | No |
| `line_health` | Return redacted diagnostics | No |

`read_history` and `save_relationship_context` may write to the ignored local
memory database. They never modify LINE's source DB. See [PRIVACY.md](PRIVACY.md)
and [AUDIT.md](AUDIT.md) for the detailed boundary.

### Requirements

- Apple Silicon macOS with LINE Desktop installed and signed in; Intel Mac,
  Windows and Linux are unsupported by this package
- A client supporting local stdio MCP, such as Codex
- Node.js 20 or later and npm (declared range; stable Node 20/22 remain unverified)
- Homebrew Python 3.14 compatible with the pinned `apsw-sqlite3mc` wheel
- Xcode toolchain with Swift/LLDB, Git and zsh; the complete toolchain/minimum
  macOS combination needed by the pinned Peekaboo build remains unverified
- macOS Login Keychain access
- Accessibility permission for the pinned local Peekaboo build; Screen Recording
  may also be requested by Peekaboo even though ordinary chat text comes from
  the DB, not screenshots
- Optional: ChatGPT Remote paired between the phone and Mac

### Installation

Start with the published npm tarball in a persistent private directory:

```sh
(
  set -eu
  umask 077
  mkdir -m 700 "$HOME/line-context-relay-0.2.0"
  cd "$HOME/line-context-relay-0.2.0"
  npm pack line-context-relay@0.2.0 --ignore-scripts --registry=https://registry.npmjs.org
  tar -xzf line-context-relay-0.2.0.tgz
)
```

If the directory already exists, stop and choose a new private directory.
Then run each setup step from the extracted package root, stopping on errors:

```sh
cd "$HOME/line-context-relay-0.2.0/package"
scripts/bootstrap
node bin/line-context-relay.mjs --help
scripts/build-peekaboo
scripts/build-history-helper
```

There are no install/postinstall hooks. Downloading the package does not set up
dependencies, permissions or Keychain. Avoid global installation or temporary
`npx` execution: this version stores its Python environment and private state
under the installation root. See [INSTALL.md](INSTALL.md) for prerequisites,
release integrity evidence, alternate Python paths and pinned-source installation.

`scripts/bootstrap` clones the three commits in [VENDOR_LOCK.md](VENDOR_LOCK.md)
without executing vendor code, installs hash-pinned Python and lockfile-pinned
Node dependencies, and runs the tests. `scripts/build-peekaboo` separately
builds only the reviewed Peekaboo commit into the ignored `runtime/` directory.

`scripts/build-history-helper` builds the project's bounded native AX adapter.
Historical backfill requires an already-open, focused chat with an exact window
title and one identifiable message list. It sends wheel events only to LINE;
it does not search, click results, read message text from UI, or use keys.
If LINE omits the accessible message list, the operation stops explicitly.
The Codex computer tool can open/scroll an exact chat when available; actual
text must still be read through `read_history`. Verify the oldest message and
count after loading, not just a successful scroll. Server completeness remains
unknown without an authoritative acknowledgement.

For window-read timeouts, use the bounded diagnostic and recovery procedure in
[Historical UI troubleshooting](HISTORY-UI-TROUBLESHOOTING.md). Keep window
recovery, actual scrolling, and verified new history as separate results.

Complete the one-time Keychain setup below, then register the MCP in
`~/.codex/config.toml`. Use `node -p 'process.execPath'` to find Node and `pwd`
from the package root to find the installation path; replace both placeholders:

```toml
[mcp_servers.line-readonly]
command = "/absolute/path/to/node"
args = ["/absolute/path/to/line-context-relay-0.2.0/package/bin/line-context-relay.mjs"]
```

Install the global skill from the package root without copying private state:

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$(pwd)/skills/line-chat-advisor" "$HOME/.codex/skills/line-chat-advisor"
```

Do not overwrite an existing skill path without reviewing it. Restart/reload
ChatGPT/Codex after changing MCP or skill configuration.

### Release verification limits

The published tarball contains 38 files matching PR #2's merged source; see
[INSTALL.md](INSTALL.md) for hashes, commit IDs and the verification boundary.
PR #2 reported 7 Node and 55 Python tests using Node 26.8.0-alpha and Python
3.14 on Apple Silicon macOS. Stable Node 20/22, a new user's complete setup,
live attachment retrieval, native history UI behavior and the Peekaboo build
were not verified in that PR. The completed Codex Security Review is not a
third-party certification. [ACCEPTANCE.md](ACCEPTANCE.md) contains older local
observations, not a complete acceptance record for 0.2.0.

These GitHub documentation updates do not change the README/INSTALL already
inside the published 0.2.0 tarball; follow the current
[GitHub installation guide](https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant/blob/master/INSTALL.md).

### One-time key capture

LINE's local DB is encrypted. The project includes a deliberately isolated,
user-assisted capture path that stores the validated key directly in Login
Keychain and removes temporary artifacts:

```bash
scripts/prepare-key-capture
# Sign in only to the visible temporary LINE copy.
scripts/capture-key
scripts/cleanup-key-capture   # safe to run again if cleanup is needed
```

Read [SECURITY.md](SECURITY.md) and [AUDIT.md](AUDIT.md) first. Never paste the
key into a terminal command, `.env`, issue, log, prompt, or repository. The
technique may break after LINE/macOS updates and may be subject to LINE's terms;
use it only on your own Mac and account.

### Verify and run

```bash
npm run test
scripts/doctor
npm start
```

After reloading Codex, prompts such as these trigger the global skill:

```text
Read the latest LINE messages from <exact contact name>, use the full
relationship context, tell me whether I should reply, and if so give me only
one reply in my style. Do not send it.
```

The skill requires this sequence:

```text
line_status → refresh_latest → get_relationship_context → read_history
            → save_relationship_context
```

`ok: true` and `localSnapshotRefreshed: true` permit reading the refreshed local
snapshot. While locked, the bridge polls passively for the requested window or
until the target advances, without UI actions. `targetAdvanced` only records
local message progress, which may be outgoing. Neither app activation nor local
changes prove server completeness: `freshnessVerified` is now always false,
with `syncComplete: null` and `serverWatermark: null` until an authoritative
server acknowledgement is available. Consumers of the older freshness flag
must use `localSnapshotRefreshed` for local-read success instead.

Results report snapshot time, latest target-message time, and unverified server
sync. DB/key/snapshot failures still stop the read. Date-range requests return
transcripts; reply-advice requests return a short suggestion when appropriate.

The documented [LINE Messaging API](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)
receives messages for Official Accounts through webhooks; it is not a private
Desktop-chat synchronization acknowledgement or backfill interface for this
bridge. Its documentation also says received text cannot be fetched again by
API. No server completeness guarantee is implemented here.

### Repository contents

- `src/line-readonly-mcp/` — Python DB bridge and Node MCP server
- `src/key-capture/` — isolated Keychain/key-validation helpers
- `skills/line-chat-advisor/` — global Codex skill source
- `scripts/` — bootstrap, build, doctor, capture, and cleanup commands
- `tests/` — Python and real stdio MCP tests
- `AUDIT.md` — upstream security review
- `VENDOR_LOCK.md` — source URLs and reviewed commits
- `ACCEPTANCE.md` — sanitized verification status

Dependencies, vendor working copies, build outputs, databases, snapshots,
screenshots, logs, keys, local memory, and contact material are intentionally
excluded from Git. They are recreated locally or remain private.

### Development

```bash
npm run test
scripts/doctor
git diff --check
```

Before changing a vendor pin, repeat the security audit. Never add a message
send path or a generic GUI/keyboard tool to the MCP allow-list.

### License

Original code in this repository is released under the [MIT License](LICENSE).
Fetched upstream projects remain under their own licenses. Their source,
license, and reviewed commit are listed in [VENDOR_LOCK.md](VENDOR_LOCK.md).

---

## 繁體中文

### 這是什麼

LINE Context Relay 是 macOS 上的本機優先、唯讀 LINE 聊天輔助機器人。
它透過受限 MCP 讓 ChatGPT/Codex 讀取 LINE Desktop 的本機加密資料庫，先用
DB 證據刷新精確目標聊天室，再結合完整關係脈絡，判斷現在該不該回；需要回覆
時只提供一句最像使用者的版本。

**本專案永遠不會傳送 LINE 訊息。** 建議只顯示在 ChatGPT/Codex。

若使用者的 ChatGPT 桌面版與手機已啟用並配對 ChatGPT Remote，可以由手機送出
任務，讓配對的 Mac 執行本機 LINE 讀取，再把建議帶回 ChatGPT。Remote、Locked
Use 與鎖定畫面下的實際可用性會隨版本與裝置狀態改變，必須在自己的設備實測；
Mac 仍需開機、連網，且已登入可用的圖形工作階段。

### 主要能力

- 對 LINE 主 DB、WAL、SHM 建立穩定私人副本
- 只用 `SQLITE_OPEN_READONLY` 與 `query_only` 開啟副本
- 解密金鑰只從 macOS Login Keychain 取得
- 精確名稱／`chatId` 解析，重名就停止而不是猜測
- 有上限的歷史讀取與 timestamp/message-ID cursor 分頁
- 重新建立目標聊天室的本機穩定快照；DB 紀錄前進不代表伺服器同步完整
- 第一次完整讀取，之後依 cursor 增量更新關係脈絡
- context version 防止不同 Codex task 互相覆蓋新版摘要
- 全域 `line-chat-advisor` skill
- 沒有 `send_message`、通用輸入、任意 app、OCR、截圖、Shell MCP 或
  AppleScript MCP

### 安全資料流

一般讀取固定依序執行：

```text
line_status → refresh_latest → get_relationship_context → read_history
            → save_relationship_context
```

`refresh_latest` 以 `localSnapshotRefreshed` 表示已取得本機穩定唯讀快照；
鎖屏時會持續被動檢查至指定期限或目標前進。`targetAdvanced` 僅表示本機目標
紀錄前進，不能證明伺服器同步完整；`freshnessVerified` 保持 false，
`syncComplete` 與 `serverWatermark` 保持 null。輸出會附讀取時間與同步未確認提示，
不因鎖屏阻擋本機讀取；DB／金鑰／快照失敗仍停止。`read_history` 與
`save_relationship_context` 只會寫入被 Git 忽略、權限為 `0600` 的本機記憶，
永遠不會修改 LINE 來源 DB。

### 安裝

正式版本為 **`line-context-relay@0.2.0`**。先依
[INSTALL.md](INSTALL.md) 的前置條件與第 1 步，從 npm 下載固定版本並解壓到私人目錄。
僅限 Apple Silicon macOS、Python 3.14；Node 宣告 `>=20`，但穩定版 Node 20／22
仍未驗證。全新使用者的 Keychain／權限流程、實際附件下載、原生歷史 UI 與該 PR
的 Peekaboo 編譯流程也尚未驗證；已發布不等於全面驗收或第三方安全認證。

在解壓後的套件根目錄逐項執行，失敗就先停止排錯：

```sh
cd "$HOME/line-context-relay-0.2.0/package"
scripts/bootstrap
node bin/line-context-relay.mjs --help
scripts/build-peekaboo
scripts/build-history-helper
```

下載套件不會自動初始化、取得金鑰或註冊 MCP。因私人狀態與 `.venv` 位於安裝目錄，
不建議全域安裝或以 `npx` 暫存啟動。固定原始碼安裝方式也列於 INSTALL.md。

`scripts/bootstrap` 會依 [VENDOR_LOCK.md](VENDOR_LOCK.md) 下載三個經審查的固定
commit，不執行 vendor 程式，接著安裝 lockfile／hash 鎖定依賴並跑測試。
Peekaboo 必須用獨立指令明確建置，產物只放在被忽略的 `runtime/`。

完成下方的一次性 Keychain 設定後，在 `~/.codex/config.toml` 註冊 MCP。
以 `node -p 'process.execPath'` 取得 Node 路徑、`pwd` 取得套件根目錄，替換以下佔位符：

```toml
[mcp_servers.line-readonly]
command = "/absolute/path/to/node"
args = ["/absolute/path/to/line-context-relay-0.2.0/package/bin/line-context-relay.mjs"]
```

在套件根目錄安裝全域 skill（不要覆蓋既有同名路徑）：

```bash
mkdir -p "$HOME/.codex/skills"
ln -s "$(pwd)/skills/line-chat-advisor" "$HOME/.codex/skills/line-chat-advisor"
```

修改 MCP 或 skill 後必須重新載入 ChatGPT/Codex，已開啟的 task 不會自動更新
啟動時載入的工具清單。

### 一次性金鑰擷取

LINE 本機 DB 有加密。專案提供隔離、需要使用者配合的一次性流程，驗證後直接
把金鑰寫入 Login Keychain，並刪除暫存檔：

```bash
scripts/prepare-key-capture
# 只登入畫面上可見的暫時 LINE 副本
scripts/capture-key
scripts/cleanup-key-capture
```

執行前請完整閱讀 [SECURITY.md](SECURITY.md) 與 [AUDIT.md](AUDIT.md)。金鑰不可
放入命令列、`.env`、Issue、log、prompt 或 Git。這項技術可能因 LINE/macOS 更新
失效，也可能受到 LINE 使用條款限制；只能用於自己的 Mac、帳號與資料。

### 驗證與使用

```bash
npm run test
scripts/doctor
npm start
```

重新載入 Codex 後，可使用：

```text
讀取 LINE：<聯絡人精確名稱> 的最近訊息，結合完整關係脈絡，告訴我現在該不該回；
需要回覆時只給我一句最像我的版本，不要傳送。
```

需要回覆時只會輸出一句建議；不需要時只輸出 `現在不用回。`；刷新或安全條件
不成立時只回報阻塞原因，不會改用 Computer Use、OCR 或舊快照。

### 隱私與公開範圍

以下內容永遠不進 Git：LINE DB/WAL/SHM、Keychain 金鑰、擷取候選值、聊天內容、
chatId、聯絡人名稱、截圖、OCR、log、本機 memory、dependency tree、vendor working
copy、binary 與 build artifacts。詳細規則見 [PRIVACY.md](PRIVACY.md)。

### 授權

本 repo 的原創程式採 [MIT License](LICENSE)。透過 bootstrap 下載的第三方專案
仍使用各自授權；來源、license 與固定 commit 請見
[VENDOR_LOCK.md](VENDOR_LOCK.md)。

本專案是非官方社群工具，與 LINE Corporation、OpenAI 無隸屬、贊助或背書關係。
