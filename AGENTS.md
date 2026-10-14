# LINE Context Relay — Persistent Safety Rules

## Scope

This workspace provides read-only access to the current user's local LINE Desktop data. Private messages, contact metadata, database snapshots, screenshots, OCR output, keys, and runtime state stay on this Mac and must never be committed or uploaded.

## Required behavior

- 每次「讀取 LINE」都必須依序呼叫 `line_status`、`refresh_latest`、`get_relationship_context`，再以 `read_history` 讀取首次完整或後續增量歷史。
- `refresh_latest` 以新穩定唯讀快照更新本機資料；`ok: true`、`localSnapshotRefreshed: true` 即可繼續讀取。鎖屏時持續被動檢查至要求的期限或目標前進，不需要求解鎖。目標 timestamp/message ID 前進只回報 `targetAdvanced: true`；這可能是傳出訊息，不能證明完整同步。沒有伺服器完成回執時，無論喚醒成功或目標前進都保持 `freshnessVerified: false`、`syncComplete: null`、`serverWatermark: null`。輸出本次快照時間、目標對話最新訊息時間及「LINE 同步狀態未確認」。舊版啟動／聚焦或鎖屏錯誤可在重新通過 `line_status` 的 DB／金鑰／解密／read-only/query-only 檢查後繼續本機讀取；DB／金鑰／快照或聯絡人識別失敗仍必須停止。
- 「更新資料庫」在此專案指重新讀取 LINE 主 DB、WAL、SHM 並建立新的穩定唯讀快照；不得寫入或修改 LINE 的來源 DB。
- 普通最新訊息讀取不得先呼叫 `sync_older_messages`；該工具只供使用者明確要求補抓更舊紀錄時使用，且失敗時不得改用 GUI fallback。
- 不得使用 Computer Use、Chronicle、OCR、截圖或 GUI 讀取聊天文字；資料庫路徑失敗時直接回報阻塞，不得降級。
- 附件下載依 `skills/line-chat-advisor/references/attachments.md` 走程式化 OBS 下載、本機解密與驗證；不得以 CUA 點擊視窗或原生另存視窗作為後備。訊息附件金鑰只在 bridge 內使用，不能輸出到工具回傳／紀錄。
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
- 使用者要求回覆建議時，輸出一句最推薦版本；不需要回覆時輸出「現在不用回。」。要求匯出／日期範圍時，列時間、收發者、原文與無紀錄日期，不加未要求的建議。同步未確認時保留本機紀錄提示。

## Verification boundary

- DB 讀取一律先建立主 DB、WAL、SHM 的穩定副本，只開啟副本且啟用 read-only/query-only。
- 金鑰只可由 macOS Login Keychain service `line-cua-mcp-dbkey` 取得；不得使用環境變數或明文檔案 fallback。
- 上項指資料庫金鑰。附件下載的 OBS 憑證優先從同一唯讀快照的 `_setting` → `setting` → `obsTokenGeneral.token` 取得，只在程式記憶體內使用；快取缺少時才讀取已配置的 Login Keychain `line-readonly-obs-token`。每次下載重讀，憑證續期由 Desktop 負責；不提供獨立登入或續期，不因 malformed／401／403 自動切換憑證或重試。
- 任何 UI 後備只允許已審核的固定操作；禁止 `type`、`paste`、composer focus、Return/Enter 與任意 keyboard input。
- 上游 `vendor/line-cua-mcp` 僅供審查；不得直接註冊其 MCP server，也不得執行 `verify`。
