# Security Policy / 安全政策

## Reporting a vulnerability

Do not open a public issue containing a LINE database, key, chat transcript,
account identifier, screenshot, or exploit details. Use GitHub's private
security-advisory form:

https://github.com/LEO0047/line-chat-advisor-mcp-ai-assistant/security/advisories/new

Include the affected version/commit, reproduction steps using synthetic data,
and the expected security boundary. Remove all personal data before attaching
logs.

## Security boundaries

- LINE source DB/WAL/SHM files are copied to a private temporary directory and
  only the copy is opened with `SQLITE_OPEN_READONLY` and `query_only`.
- The LINE DB key is read from macOS Login Keychain. It must never be supplied
  through an environment variable, command-line argument, repository file, or
  issue report.
- The MCP exposes no message-send tool and no generic keyboard, clipboard,
  AppleScript, Shell, OCR, screenshot, or arbitrary GUI-control tool.
- Relationship summaries are local private memory, not LINE source data. The
  local database is mode `0600` and is excluded from Git.
- Pinned upstream sources are fetched only by explicit bootstrap commands. See
  `VENDOR_LOCK.md` and `AUDIT.md` before changing a pin.

## If a secret was exposed

1. Stop publishing and remove public access if necessary.
2. Delete the leaked artifact from the working tree and Git history; deleting
   only the latest file is insufficient.
3. Remove the affected Login Keychain item and sign out/revoke the affected
   LINE session where appropriate.
4. Rotate any unrelated token that appeared in the same log or archive.
5. Re-run the privacy, secret, and security scans before republishing.

## 漏洞回報

請勿在公開 Issue 張貼 LINE DB、解密金鑰、聊天紀錄、帳號識別資訊、截圖或
漏洞利用細節。請使用上方 GitHub Private Security Advisory，並只提供去識別化
的合成測試資料。

本專案的核心安全邊界是：LINE 來源 DB 永遠唯讀、金鑰只來自 Login Keychain、
MCP 沒有傳送訊息與通用輸入工具、本機關係摘要永不進 Git。
