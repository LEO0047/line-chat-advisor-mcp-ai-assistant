# 維護與安全責任

目前儲存庫擁有者及維護聯絡帳號：[LEO0047](https://github.com/LEO0047)。
歷史安全修改請以 Git commit、PR 與 SECURITY.md 為可核對紀錄。
這份文件不代表第三方認證、外部採用或 Anthropic CVP 資格已核准。

## 維護工作

- 審查唯讀資料庫邊界、Keychain-only 金鑰來源、MCP 工具白名單及私人資料排除規則。
- 變更上游 pin 前重做 AUDIT.md 所述審查；不可直接執行上游 MCP server。
- 以合成資料重現漏洞，補回歸測試；不可將使用者聊天或金鑰作為測試 fixture。
- 私下處理 SECURITY.md 指定的漏洞回報；確認影響後提供修補版本及必要公告。
- 不承諾未實際具備的 24/7 回應時間或安全稽核認證。

## 發布檢查

1. 在乾淨分支審閱差異，確認沒有聊天、資料庫、金鑰、帳號資訊或執行紀錄。
2. 執行 `npm ci --ignore-scripts`、建立 Python 3.14 venv 並依 requirements.lock 安裝，執行 scripts/bootstrap-vendors 取得固定 schema。
3. 執行 `npm test`、`npm run check:package`、`git diff --check`。
4. 執行 `npm pack`；在另一個目錄解壓，重新安裝依賴、跑測試及 MCP stdio handshake。
5. 確認文件明確標示已測平台、未測的真實 LINE／權限流程及已知限制。
6. 由維護者審核 PR、決定版本與公開發布；確認 npm 名稱擁有權、登入與 2FA。
   `npm publish --access public` 是另外的公開發布動作，不是本地打包或 PR 的一部分。

公開的下載統計只能在實際發布後由套件平台取得；不可將測試安裝、自建依賴或
文件宣告當成已證實的外部採用。CVP 應使用當時真實可驗證的資料，並由 Anthropic 審核。
