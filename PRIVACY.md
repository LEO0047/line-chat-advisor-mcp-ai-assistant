# Privacy / 隱私

LINE Context Relay is designed for local processing. The project itself does
not upload chat content, keys, database snapshots, or relationship memory.
LINE Desktop and ChatGPT/Codex are separate applications with their own network
behavior and privacy terms.

## Data that stays local

- LINE encrypted DB, WAL, and SHM files
- temporary stable snapshots
- Login Keychain database key
- key-capture candidates and logs
- normalized local chat memory and relationship summaries
- chat IDs, contact names, message text, media, timestamps, screenshots, and
  OCR output
- local binaries, dependency trees, and build products

The repository `.gitignore` excludes `contacts/`, `data/`, `runtime/`, database
formats, images, logs, local configuration, credentials, dependencies, and
vendor working copies. Do not override these exclusions to publish a debug
bundle.

## Data flow

1. A local ChatGPT/Codex task calls the allow-listed MCP server.
2. The server creates a stable private copy of LINE's encrypted DB/WAL/SHM.
3. The key is retrieved from Login Keychain and only the copy is opened.
4. Bounded results are returned to the local task.
5. Optional normalized history and relationship context are saved in a private
   mode-`0600` local SQLite database.
6. A reply suggestion is displayed in ChatGPT/Codex only; it is never sent to
   LINE by this project.

## Before making a repository public

Run a path-only secret scan and inspect every staged file. At minimum verify
that no staged path lives under `contacts/`, `data/`, `runtime/`, `vendor/`,
`node_modules/`, or `.venv/`, and that no real contact name, chat ID, transcript,
home-directory username, account token, or machine-specific snapshot remains.

## 中文說明

本專案以本機處理為原則，不會自行上傳 LINE 聊天內容、資料庫、金鑰、截圖或
關係摘要。LINE Desktop 與 ChatGPT/Codex 是獨立應用程式，仍各自有其網路行為
與隱私條款。

所有 LINE DB/WAL/SHM、Keychain 金鑰、暫存快照、聊天記憶、聯絡人名稱、chatId、
訊息與偵錯輸出都必須留在本機。建議回覆只顯示於 ChatGPT/Codex，本專案永遠
不傳送至 LINE。
