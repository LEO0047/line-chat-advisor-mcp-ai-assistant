# 安裝與套件狀態

目前提供待發布的 npm 套件；本文件不代表 npm 已上架，也不代表已有第三方下載。
僅支援 Apple Silicon macOS、Node.js 20 以上及 Python 3.14。
Python 加密 SQLite wheel 已以 SHA-256 固定；Intel Mac 與其他 Python 版本尚未支援。

## 從審閱過的原始碼安裝

```sh
git clone https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant.git
cd line-chat-advisor-mcp-ai-assistant
scripts/bootstrap
```

如 Python 不在預設路徑，可用 `LINE_RELAY_PYTHON` 指定 Python 3.14 執行檔。
bootstrap 會下載 VENDOR_LOCK.md 的固定上游版本、建立 `.venv`、安裝固定依賴並測試。
不會自動取得 LINE 金鑰或授予系統權限。請先閱讀 SECURITY.md、PRIVACY.md、AUDIT.md。

## 從本機套件安裝與驗證

在維護者完成測試、執行 `npm pack` 後，將產生的 `.tgz` 解壓到固定的私人資料夾。
路徑不要放進公開網頁或同步分享資料夾；執行後的 data/ 與 runtime/ 可能包含私人資料。

```sh
mkdir line-relay-install
tar -xzf line-context-relay-0.2.0.tgz -C line-relay-install
cd line-relay-install/package
scripts/bootstrap
node bin/line-context-relay.mjs --help
```

套件包含 npm-shrinkwrap.json，讓 `npm ci` 使用固定 Node 依賴。
沒有 install/postinstall hook；安裝 tarball 不會自動執行初始化、讀取聊天或呼叫 Keychain。
不建議使用全域安裝或 npx 暫存目錄：本版本的私人狀態與 Python 環境位於安裝目錄。

## 啟用 MCP

依 README 的明確步驟另行建置 Peekaboo 與 scripts/build-history-helper、由使用者完成一次性 Keychain 設定。
這些步驟可能要求系統權限，不會在套件安裝時自動完成。
在 MCP 客戶端設定 Node 的絕對路徑，以及此安裝位置的入口：

```toml
[mcp_servers.line-readonly]
command = "/absolute/path/to/node"
args = ["/absolute/path/to/line-relay-install/package/bin/line-context-relay.mjs"]
```

重新載入客戶端，確認只列出 README 的十個受限工具。`scripts/doctor` 會檢查
本機 LINE 與 Keychain，應由使用者在完成設定後主動執行。一般安裝測試不需要私人對話。

## 更新與移除

先停止 MCP。新版本安裝到另一個私人資料夾並測試，再更新客戶端路徑。
不要以覆蓋安裝或刪除舊資料夾的方式處理既有 data/、runtime/ 和 Keychain 項目。
移除時先取消 MCP 設定；私人資料與 Keychain 保留或刪除由使用者個別決定。

## 排錯

- Python 版本／wheel 不相容：確認 Apple Silicon 與 Python 3.14；不要移除 hash 驗證。
- venv_missing：在安裝根目錄明確執行 scripts/bootstrap。
- memory_vendor_missing：執行 scripts/bootstrap-vendors，保持 VENDOR_LOCK.md 的版本。
- 金鑰或資料庫不可用：依 README 處理；不得改用明文金鑰或 GUI 讀聊天。
- 本機快照不等於伺服器完整同步；不得把無法驗證的同步完整性描述為成功。
