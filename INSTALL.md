# 安裝 LINE Context Relay 0.2.0

`line-context-relay@0.2.0` 已於 **2026-10-14 17:53:51（Asia/Taipei）**公開發布至
[npm](https://www.npmjs.com/package/line-context-relay)。這確認套件已可公開取得，
不代表已有外部採用、第三方安全認證或所有相容性測試均通過。

## 安裝前確認

| 項目 | 0.2.0 的要求與驗證邊界 |
| --- | --- |
| 系統 | Apple Silicon macOS；npm 宣告 `os: darwin`、`cpu: arm64`，bootstrap 也會檢查主機。Intel Mac、Windows、Linux 不支援；不要以 Rosetta 的 x64 環境安裝。 |
| Node.js / npm | 套件宣告 Node.js `>=20`，需可用的 npm；這是版本條件，不是所有版本均已測試。PR #2 使用 Node 26.8.0-alpha.0.0.0 / npm 11.19.0，曾有 Node 支援警告；穩定版 Node 20／22 尚未驗證。 |
| Python | Python 3.14；預設 `/opt/homebrew/bin/python3.14`。加密 SQLite 的 macOS ARM64 wheel 以 SHA-256 固定，其他 Python 版本不支援。 |
| 本機工具 | Git、zsh、Xcode 工具鏈（Swift、LLDB、codesign）；需能建置固定版 Peekaboo 與本專案 Swift helper。完整工具鏈／最低 macOS 版本組合尚未完成驗證，不能保證僅安裝 Command Line Tools 即可完成所有建置。 |
| LINE | 已安裝並登入自己的 LINE Desktop；一次性金鑰流程要求 `/Applications/LINE.app`。LINE/macOS 更新可能影響資料庫與金鑰流程。 |
| 客戶端與權限 | 能啟動本機 stdio MCP 的客戶端（如 Codex）、Login Keychain；原生 UI 輔助功能需要對應的 Accessibility 權限，Peekaboo 也可能要求 Screen Recording。Remote 配對另行實測。 |
| 網路與位置 | 初始化需連線取得 npm、Python 依賴與 GitHub 固定上游原始碼；安裝到持久、私人、可寫入且不公開同步的目錄。 |

先閱讀 [SECURITY.md](SECURITY.md)、[PRIVACY.md](PRIVACY.md)、[AUDIT.md](AUDIT.md)。
以下步驟遇到錯誤就先停止排錯，不要繼續註冊尚未完成初始化的 MCP。

## 1. 取得 npm 正式套件

用 `npm pack` 下載**已發布的固定版本**，解壓到持久私人目錄。
這裡不是在原始碼目錄重新打包，也不是重新發布：

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

若目錄已存在，這段會停止；請使用另一個新的私人目錄，不要覆蓋既有狀態。
以下範例以 `$HOME/line-context-relay-0.2.0/package` 為安裝根目錄。

沒有 `install`／`postinstall` hook。下載／解壓不會自動初始化、安裝依賴、
讀取聊天或呼叫 Keychain。`.venv/`、`vendor/`、`runtime/`、`data/` 位於
安裝根目錄，因此不建議 `npm install -g`、`npx` 暫存啟動或讓套件管理器替換此目錄。

### 發行檔核對紀錄（2026-10-14）

來源：[npm registry](https://registry.npmjs.org/line-context-relay)、
[PR #2](https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant/pull/2)。

- `latest`: `0.2.0`
- `gitHead`: `f66a89014e0e795590b06a08028f7136971fc57a`（PR #2 的 head）
- tarball：38 個檔案，解壓內容合計 310,049 bytes
- `dist.integrity`: `sha512-JLnVTb+J+D4U02tOPRhyhDcOglGbKSeZ53LcyMzi6ZnodnefidIT3uyKxdFpriGTIP9i8WURBLoLbjDJygUKzQ==`

本次下載檔的 SHA-512 與 SHA-1 均符合 registry；38 個檔案內容逐一與
合併 commit `42fc2d0e545daead039767d17169a638282976e8` 比對，全數相同。
tarball 是發布檔案清單的子集，不是完整 Git checkout；包含 CLI、Node/Python/Swift
原始碼、初始化腳本、測試、skill、文件與鎖檔，不含依賴、上游 checkout、已編譯
helper、私人資料庫或金鑰。雜湊／原始碼一致性不等於安全認證。

## 2. 明確執行初始化

```sh
cd "$HOME/line-context-relay-0.2.0/package"
scripts/bootstrap
node bin/line-context-relay.mjs --help
```

若 Python 不在預設路徑，改以以下方式執行 bootstrap（替換成真實路徑）：

```sh
LINE_RELAY_PYTHON="/absolute/path/to/python3.14" scripts/bootstrap
```

bootstrap 會先檢查 macOS ARM64 與 Python 3.14，再取得 [VENDOR_LOCK.md](VENDOR_LOCK.md)
的固定上游版本、建立 `.venv`、依兩份 Python lockfile 安裝 hash 鎖定依賴，
以 `npm ci --ignore-scripts` 安裝 `npm-shrinkwrap.json` 固定的 Node 依賴，最後跑測試。
它不會自動建置 Peekaboo／history helper、取得 LINE 金鑰或授予系統權限。
`--help` 也會先匯入 Node 依賴，因此需在 bootstrap 成功後執行。

## 3. 建置本機輔助程式與設定 Keychain

在安裝根目錄逐項執行，確認每一步成功後才繼續：

```sh
scripts/build-peekaboo
scripts/build-history-helper
```

這兩項會在本機編譯；npm 不包含預建 binary。完成所需系統權限後，依 README 的
[一次性金鑰擷取](README.md#一次性金鑰擷取) 進行使用者協助的 Keychain 設定。
金鑰擷取是獨立操作，不是套件安裝的自動步驟；不要把金鑰貼進設定、環境變數或文件。

## 4. 註冊 MCP 並檢查

先取得目前 Node 與安裝根目錄的絕對路徑：

```sh
node -p 'process.execPath'
pwd
```

在 Codex 的 `~/.codex/config.toml` 填入實際路徑（不保留佔位符）：

```toml
[mcp_servers.line-readonly]
command = "/absolute/path/to/node"
args = ["/absolute/path/to/line-context-relay-0.2.0/package/bin/line-context-relay.mjs"]
```

套件的 `bin` 對應為 `line-context-relay` → `bin/line-context-relay.mjs`，這個入口
啟動 stdio MCP；它不是 HTTP 服務，也不會代替初始化。`npm start` 則直接啟動
`src/line-readonly-mcp/server.mjs`。其他 MCP 客戶端可使用同一組 command／args，
依該客戶端設定格式填入。

需要 Codex 聊天顧問流程時，依 README 的[安裝段落](README.md#安裝)安裝 skill
symlink；它不會隨 npm 下載自動註冊。不要覆蓋既有 skill 路徑。

完成 Keychain 設定後，在安裝根目錄主動執行：

```sh
scripts/doctor
```

doctor 會檢查本機 LINE 與 Keychain；它不同於不需私人對話的安裝／單元測試。
重新載入客戶端並開啟新 task，確認只列出 README 的十個受限工具。
stdio server 等待客戶端輸入時不會出現一般互動式介面；手動執行 `npm start`
本身不代表完整驗證成功。

## 已測與尚未驗證

[PR #2](https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant/pull/2) 記錄在
Apple Silicon macOS、Node 26.8.0-alpha 與 Python 3.14 下，7 個 Node 測試、
55 個 Python 測試、原始碼及解壓套件測試、tarball 安裝與 CLI help、Swift history
helper 編譯及原始碼目錄 bootstrap 已完成。

**仍未完成驗證：**穩定版 Node 20／22、全新使用者的完整安裝／Keychain／權限流程、
實際附件下載、原生歷史 UI 行為，以及該 PR 的 Peekaboo 編譯流程。
本次文件修正核對 registry、tarball 內容與入口／腳本，未在新的 Mac 上重跑上述流程。

GitHub 的 Codex Security Review 已於 2026-10-14 顯示完成；這不是第三方安全認證，
也不代表全部相容性或安全測試通過。[ACCEPTANCE.md](ACCEPTANCE.md) 記錄的是較早的
本機觀察（包括舊的八工具與 refresh 行為），不能當成 0.2.0 的完整驗收清單。

## 可選：從固定原始碼安裝

需要審閱／修改程式時，可在另一個新的私人目錄 clone，checkout npm 記錄的 commit，
再從本文件第 2 步開始；此時安裝根目錄為 checkout 本身，請使用其實際路徑：

```sh
git clone https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant.git
cd line-chat-advisor-mcp-ai-assistant
git checkout --detach f66a89014e0e795590b06a08028f7136971fc57a
```

未固定 commit 的 `master` 日後可能與已發布的 0.2.0 不同。
GitHub 上的文件修正不會回寫已發布 tarball 內的舊 README／INSTALL；最新指引以
[GitHub INSTALL.md](https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant/blob/master/INSTALL.md)
為準。本次不變更套件版本、不重新發布 npm。

## 更新與移除

先停止 MCP。新版本安裝到另一個私人資料夾並測試，再更新客戶端與 skill 路徑。
不要以覆蓋安裝或刪除舊資料夾的方式處理既有 `data/`、`runtime/` 和 Keychain 項目。
移除時先取消 MCP 設定與相關 skill symlink；私人資料與 Keychain 保留或刪除由使用者個別決定。

## 排錯

- `EBADPLATFORM`／bootstrap 平台拒絕：確認原生 Apple Silicon macOS；不要用 `--force` 繞過。
- Python 版本／wheel 不相容：確認 Python 3.14 與 ARM64；不要移除 hash 驗證。
- Node import 找不到依賴／`venv_missing`：確認在正確安裝根目錄成功執行 `scripts/bootstrap`。
- `memory_vendor_missing`：執行 `scripts/bootstrap-vendors`，保持 `VENDOR_LOCK.md` 的版本。
- Swift／Peekaboo 建置失敗：檢查目前 Xcode／Swift 工具鏈；此組合尚未全面驗證，不要自行更換上游 pin 當成相容性修復。
- 金鑰或資料庫不可用：依 README 處理；不得改用明文金鑰或 GUI 讀聊天。
- 本機快照不等於伺服器完整同步；`targetAdvanced` 也不能證明同步完整。
