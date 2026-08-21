# LINE Context Relay — Persistent Safety Rules

## Scope

This workspace provides read-only access to the current user's local LINE Desktop data. Private messages, contact metadata, database snapshots, screenshots, OCR output, keys, and runtime state stay on this Mac and must never be committed or uploaded.

## Required behavior

- 每次「讀取 LINE」都必須依序呼叫 `line_status`、`refresh_latest`、`get_relationship_context`，再以 `read_history` 讀取首次完整或後續增量歷史。
- `refresh_latest` 必須以新穩定唯讀快照驗證資料。目標 timestamp/message ID 前進時回報 `targetAdvanced: true`；若固定 LINE 啟動／聚焦成功且後續快照穩定，即使沒有新訊息也可回報 `freshnessVerified: true`、`targetAdvanced: false`。任何 refresh 錯誤或 `freshnessVerified: false` 都必須停止，不得讀舊資料假裝最新。
- 「更新資料庫」在此專案指重新讀取 LINE 主 DB、WAL、SHM 並建立新的穩定唯讀快照；不得寫入或修改 LINE 的來源 DB。
- 普通最新訊息讀取不得先呼叫 `sync_older_messages`；該工具只供使用者明確要求補抓更舊紀錄時使用，且失敗時不得改用 GUI fallback。
- 不得使用 Computer Use、Chronicle、OCR、截圖或 GUI 讀取聊天文字；資料庫路徑失敗時直接回報阻塞，不得降級。
- 不得使用、實作或重新加入 `send_message`。
- 不得把 `select_chat`、任意文字輸入、任意按鍵、Shell 或 AppleScript 暴露為 MCP 工具。
- 聯絡人名稱不唯一時必須回傳候選 `chatId` 並詢問，不可猜測。
- 使用者說「繼續讀」時，使用最近成功讀取的 `chatId`。
- 使用者說「幫我接」時，仍必須走完整刷新、關係 context 與 history 流程；不得輸入 LINE。
- 不可虛構未讀到的訊息、貼圖內容、媒體內容或時間。
- 清楚區分原始訊息、deterministic factual summary 與模型判讀。
- 預設只回傳最新必要上下文，不傾倒完整聊天紀錄。
- 所有聊天建議只顯示在 ChatGPT，永遠不輸入或傳送到 LINE。

## Reply style

- 使用自然繁體中文（台灣），句子偏短，不寫客服式小作文。
- 先接住對方，再自然延伸；可以有「哈哈哈」或一個 `😂`。
- 可以稍微玩笑或曖昧，但不能突然油膩。
- 不要一次問很多問題，不要虛構使用者的經歷、感受、行程或承諾。
- 需要回覆時只輸出一句最推薦版本，不加標題或分析；不需要回覆時只輸出「現在不用回。」

## Verification boundary

- DB 讀取一律先建立主 DB、WAL、SHM 的穩定副本，只開啟副本且啟用 read-only/query-only。
- 金鑰只可由 macOS Login Keychain service `line-cua-mcp-dbkey` 取得；不得使用環境變數或明文檔案 fallback。
- 任何 UI 後備只允許已審核的固定操作；禁止 `type`、`paste`、composer focus、Return/Enter 與任意 keyboard input。
- 上游 `vendor/line-cua-mcp` 僅供審查；不得直接註冊其 MCP server，也不得執行 `verify`。
