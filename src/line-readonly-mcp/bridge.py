#!/usr/bin/env python3
"""Read-only LINE bridge used by the allow-listed MCP server.

The module never opens LINE's source database with SQLite. It first obtains a
stable copy of the main DB and any WAL/SHM sidecars, then opens only that copy
with APSW SQLITE_OPEN_READONLY and query_only enabled.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator


ROOT = Path(os.environ.get("LINE_CONTEXT_RELAY_ROOT", Path(__file__).resolve().parents[2])).resolve()
LINE_DB_DIR = (
    Path.home()
    / "Library/Containers/jp.naver.line.mac/Data/Library/Containers/jp.naver.line/Data/db"
)
KEYCHAIN_SERVICE = "line-cua-mcp-dbkey"
LINE_BUNDLE_ID = "jp.naver.line.mac"
MEMORY_DB = ROOT / "data" / "local-chat-memory.sqlite3"
SNAPSHOT_PARENT = ROOT / "runtime" / "snapshots"
LAST_CHAT_STATE = ROOT / "runtime" / "last-chat.json"
VENDOR_MEMORY_SCHEMA = ROOT / "vendor" / "local-chat-memory" / "local_chat_memory" / "schema.sql"
MAX_CHAT_SCAN = 100_000
ALLOWED_TOOLS = [
    "line_status",
    "list_chats",
    "read_history",
    "refresh_latest",
    "get_relationship_context",
    "save_relationship_context",
    "sync_older_messages",
    "line_health",
]
CONTEXT_TEXT_LIMIT = 4_000
REFRESH_POLL_SECONDS = 0.25
REFRESH_SNAPSHOT_MIN_SECONDS = 1.0
REFRESH_FINAL_SNAPSHOT_RESERVE_SECONDS = 0.5

MESSAGE_TYPES = {
    0: "text",
    1: "image",
    2: "video",
    3: "audio",
    6: "call",
    7: "sticker",
    13: "contact",
    14: "file",
    16: "album",
}


class BridgeError(Exception):
    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "detail": self.detail, **self.extra}


def _stat_tuple(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def source_files(db: Path) -> list[Path]:
    return [candidate for candidate in (db, Path(f"{db}-wal"), Path(f"{db}-shm")) if candidate.exists()]


def source_signature(db: Path | None = None) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    """Return cheap source metadata used to gate expensive encrypted DB snapshots."""
    source_db = db or detect_db()
    try:
        return tuple(sorted((path.name, _stat_tuple(path)) for path in source_files(source_db)))
    except FileNotFoundError:
        return ()


def detect_db(db_dir: Path = LINE_DB_DIR) -> Path:
    """Return the single main DB, supporting both qwb* and current qw* names."""
    if not db_dir.is_dir():
        raise BridgeError("db_directory_missing", "LINE database directory is not present.")
    candidates = []
    for path in db_dir.glob("qw*.edb"):
        name = path.name
        if "_" in name or not path.is_file():
            continue
        candidates.append(path)
    if not candidates:
        raise BridgeError("db_not_found", "No main qw*.edb database was found.")
    if len(candidates) > 1:
        raise BridgeError(
            "db_ambiguous",
            "Multiple possible main LINE databases were found; refusing to guess.",
            candidateCount=len(candidates),
        )
    return candidates[0]


@contextlib.contextmanager
def stable_snapshot(db: Path, attempts: int = 5) -> Iterator[tuple[Path, dict[str, tuple[int, int, int]]]]:
    """Copy DB/WAL/SHM only when source metadata is stable across the copy."""
    SNAPSHOT_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SNAPSHOT_PARENT, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- private snapshots must not be group/world readable
    last_reason = "source changed during copy"
    for attempt in range(1, attempts + 1):
        files = source_files(db)
        before = {path.name: _stat_tuple(path) for path in files}
        temp = tempfile.TemporaryDirectory(prefix="line-db-", dir=SNAPSHOT_PARENT)
        temp_path = Path(temp.name)
        os.chmod(temp_path, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- contains a decrypted DB snapshot
        try:
            target = temp_path / "line.edb"
            for source in files:
                suffix = source.name[len(db.name) :]
                destination = Path(f"{target}{suffix}")
                shutil.copy2(source, destination)
                os.chmod(destination, 0o600)
            after_files = source_files(db)
            after = {path.name: _stat_tuple(path) for path in after_files}
            if before == after:
                try:
                    yield target, before
                finally:
                    temp.cleanup()
                return
            last_reason = f"source changed during copy attempt {attempt}"
        except FileNotFoundError:
            last_reason = f"source sidecar changed during copy attempt {attempt}"
        finally:
            if Path(temp.name).exists():
                temp.cleanup()
        time.sleep(0.08 * attempt)
    raise BridgeError("snapshot_unstable", f"Could not obtain a stable read-only snapshot: {last_reason}.")


def keychain_key() -> str:
    """Read and validate the key without logging or returning it to callers."""
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        raise BridgeError("keychain_key_missing", "Login Keychain item is missing or inaccessible.")
    key = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        raise BridgeError("keychain_key_invalid", "Keychain item exists but is not a valid 32-hex LINE DB key.")
    return key


def keychain_available() -> bool:
    try:
        keychain_key()
        return True
    except BridgeError:
        return False


@contextlib.contextmanager
def open_database() -> Iterator[tuple[Any, Path, dict[str, tuple[int, int, int]]]]:
    try:
        import apsw  # type: ignore
    except Exception as error:
        raise BridgeError("dependency_missing", f"apsw-sqlite3mc is unavailable: {type(error).__name__}.")

    db = detect_db()
    key = keychain_key()
    with stable_snapshot(db) as (snapshot, source_meta):
        connection = None
        try:
            connection = apsw.Connection(str(snapshot), flags=apsw.SQLITE_OPEN_READONLY)
            connection.pragma("cipher", "aes128cbc")
            connection.pragma("kdf_iter", 1)
            connection.pragma("key", key)
            connection.pragma("query_only", True)
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception as error:
            if connection is not None:
                connection.close()
            raise BridgeError("decrypt_failed", f"Snapshot decryption failed: {type(error).__name__}.")
        try:
            yield connection, db, source_meta
        finally:
            connection.close()


def iso_time(milliseconds: int | None) -> str | None:
    if not milliseconds:
        return None
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _chat_name(connection: Any, chat_id: str, cache: dict[str, str | None]) -> str | None:
    if chat_id in cache:
        return cache[chat_id]
    name = None
    row = connection.execute("SELECT _chatName FROM _groupChat WHERE _chatMid=?", (chat_id,)).fetchone()
    if row and row[0]:
        name = row[0]
    if name is None:
        try:
            row = connection.execute("SELECT _name FROM _square WHERE _mid=?", (chat_id,)).fetchone()
            if row and row[0]:
                name = row[0]
        except Exception:
            pass
    if name is None:
        row = connection.execute(
            "SELECT _displayNameOverridden,_displayName,_targetProfileDetail FROM _contact WHERE _mid=?",
            (chat_id,),
        ).fetchone()
        if row:
            name = row[0] or row[1]
            if not name and row[2]:
                try:
                    name = json.loads(row[2]).get("profileName")
                except Exception:
                    pass
    cache[chat_id] = name
    return name


def _list_chats(connection: Any, limit: int) -> list[dict[str, Any]]:
    cache: dict[str, str | None] = {}
    rows = connection.execute(
        "SELECT _id,_lastUpdatedTime FROM _chat ORDER BY _lastUpdatedTime DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {
            "chatId": chat_id,
            "name": _chat_name(connection, chat_id, cache),
            "isGroup": not str(chat_id).startswith("u"),
            "lastUpdated": iso_time(updated),
        }
        for chat_id, updated in rows
    ]


def resolve_chat(connection: Any, value: str) -> dict[str, Any]:
    raw = connection.execute("SELECT 1 FROM _chat WHERE _id=? LIMIT 1", (value,)).fetchone()
    cache: dict[str, str | None] = {}
    if raw:
        return {"chatId": value, "name": _chat_name(connection, value, cache), "isGroup": not value.startswith("u")}
    chats = _list_chats(connection, MAX_CHAT_SCAN)
    exact = [chat for chat in chats if chat["name"] == value]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise BridgeError(
            "ambiguous_chat_name",
            "More than one chat has this exact display name; choose a chatId.",
            candidates=[
                {"chatId": chat["chatId"], "name": chat["name"], "isGroup": chat["isGroup"]} for chat in exact
            ],
        )
    raise BridgeError("chat_not_found", "No chat with this exact display name or chatId was found.", query=value)


def coverage(connection: Any, chat_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT COUNT(*),MIN(_createdTime),MAX(_createdTime) FROM _message WHERE _chatId=?", (chat_id,)
    ).fetchone()
    count, oldest, newest = row if row else (0, None, None)
    return {
        "localMessageCount": count,
        "oldestLocalMessage": iso_time(oldest),
        "newestLocalMessage": iso_time(newest),
        "mayExcludeRemoteUnsyncedHistory": True,
    }


def classify_message(content_type: Any, text: Any, metadata: Any) -> tuple[str, str | None]:
    """Classify LINE content, preferring explicit metadata over legacy type codes."""
    type_code = int(content_type or 0)
    normalized_text = text if text not in (None, "") else None
    metadata_text = ""
    if isinstance(metadata, bytes):
        metadata_text = metadata.decode("utf-8", errors="ignore")
    elif isinstance(metadata, str):
        metadata_text = metadata

    metadata_upper = metadata_text.upper()
    is_sticker = "STICON_OWNERSHIP" in metadata_upper
    if not is_sticker and metadata_text:
        try:
            decoded = json.loads(metadata_text)
            replacement = decoded.get("REPLACE") if isinstance(decoded, dict) else None
            if isinstance(replacement, str):
                is_sticker = '"sticon"' in replacement.lower()
            elif isinstance(replacement, dict):
                is_sticker = "sticon" in replacement
        except (json.JSONDecodeError, TypeError):
            pass
    if is_sticker:
        return "sticker", None
    return MESSAGE_TYPES.get(type_code, f"unknown:{type_code}"), normalized_text


def normalize_cursor(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"timestampMs", "messageId"}:
        raise BridgeError("invalid_cursor", "cursor must contain only timestampMs and messageId.")
    timestamp = value.get("timestampMs")
    message_id = value.get("messageId")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise BridgeError("invalid_cursor", "cursor timestampMs must be a non-negative integer.")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 256:
        raise BridgeError("invalid_cursor", "cursor messageId must be a non-empty string of at most 256 characters.")
    return {"timestampMs": timestamp, "messageId": message_id}


def read_history(
    connection: Any,
    chat: dict[str, Any],
    limit: int,
    *,
    order: str = "latest",
    cursor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = connection.execute("SELECT _mid FROM _profile LIMIT 1").fetchone()
    my_mid = profile[0] if profile else None
    if order not in {"latest", "oldest"}:
        raise BridgeError("invalid_order", "order must be latest or oldest.")
    cursor = normalize_cursor(cursor)
    if cursor is not None and order != "oldest":
        raise BridgeError("invalid_cursor_order", "cursor pagination requires order=oldest.")

    if order == "oldest":
        where = "WHERE _chatId=?"
        parameters: list[Any] = [chat["chatId"]]
        if cursor is not None:
            where += (
                " AND (COALESCE(_createdTime,0)>? OR "
                "(COALESCE(_createdTime,0)=? AND CAST(_id AS TEXT) COLLATE BINARY>?))"
            )
            parameters.extend([cursor["timestampMs"], cursor["timestampMs"], cursor["messageId"]])
        parameters.append(limit + 1)
        rows = connection.execute(
            "SELECT _createdTime,_from,_text,_contentType,_id,_contentMetadata FROM _message "
            f"{where} ORDER BY COALESCE(_createdTime,0) ASC,CAST(_id AS TEXT) COLLATE BINARY ASC LIMIT ?",
            parameters,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
    else:
        rows = connection.execute(
            "SELECT _createdTime,_from,_text,_contentType,_id,_contentMetadata FROM _message "
            "WHERE _chatId=? ORDER BY COALESCE(_createdTime,0) DESC,CAST(_id AS TEXT) COLLATE BINARY DESC LIMIT ?",
            (chat["chatId"], limit),
        ).fetchall()
        rows.reverse()
        has_more = False

    cache: dict[str, str | None] = {}
    messages = []
    for created, sender_id, text, content_type, message_id, content_metadata in rows:
        direction = "out" if sender_id == my_mid else "in"
        type_code = int(content_type or 0)
        message_type, normalized_text = classify_message(type_code, text, content_metadata)
        messages.append(
            {
                "messageId": str(message_id),
                "absoluteTime": iso_time(created),
                "timestampMs": created,
                "direction": direction,
                "sender": "me" if direction == "out" else (_chat_name(connection, sender_id, cache) or sender_id),
                "messageType": message_type,
                "messageTypeCode": type_code,
                "text": normalized_text,
                "originalText": text,
            }
        )
    cov = coverage(connection, chat["chatId"])
    if order == "latest":
        has_more = int(cov["localMessageCount"] or 0) > len(messages)
    next_cursor = None
    if order == "oldest" and has_more and messages:
        last = messages[-1]
        next_cursor = {
            "timestampMs": int(last["timestampMs"] or 0),
            "messageId": last["messageId"],
        }
    return {
        "ok": True,
        "chatId": chat["chatId"],
        "name": chat["name"],
        "isGroup": chat["isGroup"],
        "messages": messages,
        "coverage": {**cov, "returnedMessageCount": len(messages), "limitApplied": has_more},
        "pagination": {
            "order": order,
            "cursor": cursor,
            "nextCursor": next_cursor,
            "hasMore": has_more,
        },
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _memory_schema(connection: sqlite3.Connection) -> None:
    if not VENDOR_MEMORY_SCHEMA.is_file():
        raise BridgeError("memory_vendor_missing", "local-chat-memory schema is unavailable.")
    connection.executescript(VENDOR_MEMORY_SCHEMA.read_text(encoding="utf-8"))
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS line_chat_profiles (
          line_chat_id TEXT PRIMARY KEY,
          local_chat_id INTEGER NOT NULL REFERENCES chats(id),
          display_name TEXT,
          aliases_json TEXT NOT NULL DEFAULT '[]',
          is_group INTEGER NOT NULL,
          relationship TEXT,
          stable_background TEXT,
          recent_summary TEXT,
          tone_notes TEXT,
          avoid_reasking TEXT,
          last_sync TEXT,
          oldest_local_message TEXT,
          newest_local_message TEXT,
          local_message_count INTEGER NOT NULL DEFAULT 0,
          context_version INTEGER NOT NULL DEFAULT 0,
          summary_through_timestamp_ms INTEGER,
          summary_through_message_id TEXT,
          context_updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS line_message_meta (
          source_chat_id TEXT NOT NULL,
          source_message_id TEXT NOT NULL,
          memory_message_id INTEGER NOT NULL REFERENCES messages(id),
          direction TEXT NOT NULL,
          message_type TEXT NOT NULL,
          timestamp_ms INTEGER,
          absolute_time TEXT,
          message_type_code INTEGER,
          original_text TEXT,
          PRIMARY KEY(source_chat_id, source_message_id)
        );
        """
    )
    existing_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(line_message_meta)").fetchall()
    }
    for column, declaration in (
        ("absolute_time", "TEXT"),
        ("message_type_code", "INTEGER"),
        ("original_text", "TEXT"),
    ):
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE line_message_meta ADD COLUMN {column} {declaration}")
    profile_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(line_chat_profiles)").fetchall()
    }
    for column, declaration in (
        ("context_version", "INTEGER NOT NULL DEFAULT 0"),
        ("summary_through_timestamp_ms", "INTEGER"),
        ("summary_through_message_id", "TEXT"),
        ("context_updated_at", "TEXT"),
    ):
        if column not in profile_columns:
            connection.execute(f"ALTER TABLE line_chat_profiles ADD COLUMN {column} {declaration}")


def persist_memory(history: dict[str, Any]) -> dict[str, Any]:
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(MEMORY_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        _memory_schema(connection)
        stable_name = f"line://{history['chatId']}"
        kind = "group" if history["isGroup"] else "personal"
        connection.execute(
            "INSERT INTO chats(chat_name,chat_kind,purpose) VALUES(?,?,?) "
            "ON CONFLICT(chat_name) DO UPDATE SET chat_kind=excluded.chat_kind,purpose=excluded.purpose,updated_at=CURRENT_TIMESTAMP",
            (stable_name, kind, history.get("name")),
        )
        local_chat_id = int(connection.execute("SELECT id FROM chats WHERE chat_name=?", (stable_name,)).fetchone()[0])
        batch_json = json.dumps(history["messages"], ensure_ascii=False, sort_keys=True)
        batch_hash = _sha256(batch_json)
        row = connection.execute(
            "SELECT id FROM raw_exports WHERE chat_id=? AND source_sha256=? ORDER BY id LIMIT 1",
            (local_chat_id, batch_hash),
        ).fetchone()
        if row:
            raw_export_id = int(row[0])
        else:
            cursor = connection.execute(
                "INSERT INTO raw_exports(chat_id,source_path,source_sha256,parser_version,parsed_count) VALUES(?,?,?,?,?)",
                (local_chat_id, f"line-db://{history['chatId']}", batch_hash, "line-readonly-mcp/0.1", len(history["messages"])),
            )
            raw_export_id = int(cursor.lastrowid)

        inserted = 0
        for index, message in enumerate(history["messages"], start=1):
            source_id = message["messageId"]
            fingerprint = _sha256(f"line|{history['chatId']}|{source_id}")
            body = message["text"] if message["text"] is not None else f"[{message['messageType']}]"
            body_hash = _sha256(body)
            sender = str(message["sender"])
            connection.execute(
                "INSERT INTO people(display_name,canonical_name) VALUES(?,?) "
                "ON CONFLICT(display_name) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
                (sender, sender),
            )
            person_id = int(connection.execute("SELECT id FROM people WHERE display_name=?", (sender,)).fetchone()[0])
            connection.execute(
                "INSERT INTO person_aliases(person_id,alias) VALUES(?,?) ON CONFLICT(alias) DO NOTHING", (person_id, sender)
            )
            connection.execute(
                "INSERT INTO chat_participants(chat_id,person_id,role) VALUES(?,?,'member') ON CONFLICT DO NOTHING",
                (local_chat_id, person_id),
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO messages("
                "chat_id,raw_export_id,sent_at,sent_date,sent_time,sender,body,body_hash,fingerprint_base,"
                "occurrence_index,fingerprint,line_start,line_end,sender_person_id,classification) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    local_chat_id,
                    raw_export_id,
                    message["absoluteTime"],
                    (message["absoluteTime"] or "")[:10] or None,
                    (message["absoluteTime"] or "")[11:19] or None,
                    sender,
                    body,
                    body_hash,
                    f"line|{history['chatId']}|{source_id}",
                    1,
                    fingerprint,
                    index,
                    index,
                    person_id,
                    "message" if message["messageType"] == "text" else "attachment",
                ),
            )
            if cursor.rowcount:
                inserted += 1
            memory_row = connection.execute("SELECT id FROM messages WHERE fingerprint=?", (fingerprint,)).fetchone()
            memory_message_id = int(memory_row[0])
            connection.execute(
                "UPDATE messages SET body=?,body_hash=?,classification=? WHERE id=?",
                (body, body_hash, "message" if message["messageType"] == "text" else "attachment", memory_message_id),
            )
            connection.execute(
                "INSERT INTO line_message_meta(source_chat_id,source_message_id,memory_message_id,direction,message_type,"
                "timestamp_ms,absolute_time,message_type_code,original_text) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET "
                "memory_message_id=excluded.memory_message_id,direction=excluded.direction,"
                "message_type=excluded.message_type,timestamp_ms=excluded.timestamp_ms,"
                "absolute_time=excluded.absolute_time,message_type_code=excluded.message_type_code,"
                "original_text=excluded.original_text",
                (
                    history["chatId"], source_id, memory_message_id, message["direction"],
                    message["messageType"], message["timestampMs"], message["absoluteTime"],
                    message["messageTypeCode"], message.get("originalText"),
                ),
            )

        cov = history["coverage"]
        # Derive the recent summary from everything already imported for this
        # chat. A later request for an older cursor page must not replace the
        # profile's recent context with older messages from that page.
        recent = connection.execute(
            "SELECT lm.absolute_time,lm.direction,lm.message_type,lm.original_text "
            "FROM line_message_meta lm WHERE lm.source_chat_id=? "
            "ORDER BY COALESCE(lm.timestamp_ms,0) DESC,"
            "CAST(lm.source_message_id AS TEXT) COLLATE BINARY DESC LIMIT 3",
            (history["chatId"],),
        ).fetchall()
        recent.reverse()
        facts = [
            f"{item['absolute_time']} {item['direction']} {item['message_type']}"
            + (
                f": {item['original_text'][:160]}"
                if item["message_type"] == "text" and item["original_text"]
                else ""
            )
            for item in recent
        ]
        factual_summary = " | ".join(facts) if facts else "本機尚無訊息。"
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        existing = connection.execute(
            "SELECT aliases_json FROM line_chat_profiles WHERE line_chat_id=?", (history["chatId"],)
        ).fetchone()
        aliases = json.loads(existing[0]) if existing else []
        if history.get("name") and history["name"] not in aliases:
            aliases.append(history["name"])
        connection.execute(
            "INSERT INTO line_chat_profiles("
            "line_chat_id,local_chat_id,display_name,aliases_json,is_group,recent_summary,last_sync,"
            "oldest_local_message,newest_local_message,local_message_count) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(line_chat_id) DO UPDATE SET local_chat_id=excluded.local_chat_id,display_name=excluded.display_name,"
            "aliases_json=excluded.aliases_json,is_group=excluded.is_group,recent_summary=excluded.recent_summary,"
            "last_sync=excluded.last_sync,oldest_local_message=excluded.oldest_local_message,"
            "newest_local_message=excluded.newest_local_message,local_message_count=excluded.local_message_count",
            (
                history["chatId"], local_chat_id, history.get("name"), json.dumps(aliases, ensure_ascii=False),
                int(history["isGroup"]), factual_summary, now, cov["oldestLocalMessage"], cov["newestLocalMessage"],
                cov["localMessageCount"],
            ),
        )
        connection.commit()
        os.chmod(MEMORY_DB, 0o600)
        return {"database": "local-chat-memory.sqlite3", "inserted": inserted, "deduplicated": len(history["messages"]) - inserted}
    finally:
        connection.close()


def remember_last_chat(history: dict[str, Any]) -> None:
    LAST_CHAT_STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    LAST_CHAT_STATE.write_text(
        json.dumps({"chatId": history["chatId"], "name": history.get("name"), "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    os.chmod(LAST_CHAT_STATE, 0o600)


def _context_chat_value(args: dict[str, Any]) -> str:
    value = args.get("chat")
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise BridgeError("invalid_chat", "chat must be a non-empty string of at most 512 characters.")
    return value.strip()


def _resolve_context_chat(value: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with open_database() as (connection, _db, _meta):
        chat = resolve_chat(connection, value)
        chat_coverage = coverage(connection, chat["chatId"])
    return chat, chat_coverage


def _open_local_memory() -> sqlite3.Connection:
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MEMORY_DB.parent, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- private chat memory directory
    connection = sqlite3.connect(MEMORY_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    _memory_schema(connection)
    connection.commit()
    os.chmod(MEMORY_DB, 0o600)
    return connection


def _context_response(chat: dict[str, Any], chat_coverage: dict[str, Any], row: sqlite3.Row | None) -> dict[str, Any]:
    through = None
    if row is not None and row["summary_through_timestamp_ms"] is not None and row["summary_through_message_id"]:
        through = {
            "timestampMs": int(row["summary_through_timestamp_ms"]),
            "messageId": str(row["summary_through_message_id"]),
        }
    return {
        "ok": True,
        "chatId": chat["chatId"],
        "name": chat["name"],
        "initialized": through is not None,
        "contextVersion": int(row["context_version"] if row is not None else 0),
        "relationship": row["relationship"] if row is not None else None,
        "stableBackground": row["stable_background"] if row is not None else None,
        "recentSummary": row["recent_summary"] if row is not None else None,
        "toneNotes": row["tone_notes"] if row is not None else None,
        "avoidReasking": row["avoid_reasking"] if row is not None else None,
        "summaryThrough": through,
        "contextUpdatedAt": row["context_updated_at"] if row is not None else None,
        "coverage": chat_coverage,
        "storage": {"database": "local-chat-memory.sqlite3", "localOnly": True, "lineSourceModified": False},
    }


def get_relationship_context_operation(args: dict[str, Any]) -> dict[str, Any]:
    value = _context_chat_value(args)
    chat, chat_coverage = _resolve_context_chat(value)
    connection = _open_local_memory()
    try:
        row = connection.execute(
            "SELECT relationship,stable_background,recent_summary,tone_notes,avoid_reasking,"
            "context_version,summary_through_timestamp_ms,summary_through_message_id,context_updated_at "
            "FROM line_chat_profiles WHERE line_chat_id=?",
            (chat["chatId"],),
        ).fetchone()
        return _context_response(chat, chat_coverage, row)
    finally:
        connection.close()


def _context_text(args: dict[str, Any], field: str) -> str:
    value = args.get(field)
    if not isinstance(value, str) or len(value) > CONTEXT_TEXT_LIMIT:
        raise BridgeError(
            "invalid_relationship_context",
            f"{field} must be a string of at most {CONTEXT_TEXT_LIMIT} characters.",
            field=field,
        )
    return value.strip()


def save_relationship_context_operation(args: dict[str, Any]) -> dict[str, Any]:
    value = _context_chat_value(args)
    version = args.get("expectedContextVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise BridgeError("invalid_context_version", "expectedContextVersion must be a non-negative integer.")
    through = normalize_cursor(args.get("summaryThrough"))
    if through is None:
        raise BridgeError("invalid_cursor", "summaryThrough is required.")
    relationship = _context_text(args, "relationship")
    stable_background = _context_text(args, "stableBackground")
    tone_notes = _context_text(args, "toneNotes")
    avoid_reasking = _context_text(args, "avoidReasking")
    chat, chat_coverage = _resolve_context_chat(value)

    connection = _open_local_memory()
    try:
        imported = connection.execute(
            "SELECT 1 FROM line_message_meta WHERE source_chat_id=? AND source_message_id=? "
            "AND COALESCE(timestamp_ms,0)=? LIMIT 1",
            (chat["chatId"], through["messageId"], through["timestampMs"]),
        ).fetchone()
        if imported is None:
            raise BridgeError(
                "context_cursor_not_imported",
                "summaryThrough must identify a message already imported by read_history.",
            )
        current = connection.execute(
            "SELECT context_version,summary_through_timestamp_ms,summary_through_message_id "
            "FROM line_chat_profiles WHERE line_chat_id=?",
            (chat["chatId"],),
        ).fetchone()
        if current is None:
            raise BridgeError(
                "relationship_context_uninitialized",
                "read_history must import this chat before relationship context can be saved.",
            )
        current_version = int(current["context_version"])
        if current_version != version:
            raise BridgeError(
                "context_version_conflict",
                "Relationship context changed after it was read; reload and rebase once.",
                currentVersion=current_version,
            )
        current_timestamp = current["summary_through_timestamp_ms"]
        current_message_id = current["summary_through_message_id"]
        if current_timestamp is not None and current_message_id is not None:
            cursor_regressed = through["timestampMs"] < int(current_timestamp) or (
                through["timestampMs"] == int(current_timestamp)
                and through["messageId"] < str(current_message_id)
            )
            if cursor_regressed:
                raise BridgeError(
                    "relationship_context_cursor_regression",
                    "summaryThrough cannot move behind the saved relationship context cursor.",
                )

        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        cursor = connection.execute(
            "UPDATE line_chat_profiles SET relationship=?,stable_background=?,tone_notes=?,avoid_reasking=?,"
            "summary_through_timestamp_ms=?,summary_through_message_id=?,context_updated_at=?,"
            "context_version=context_version+1 WHERE line_chat_id=? AND context_version=?",
            (
                relationship,
                stable_background,
                tone_notes,
                avoid_reasking,
                through["timestampMs"],
                through["messageId"],
                now,
                chat["chatId"],
                version,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            latest = connection.execute(
                "SELECT context_version FROM line_chat_profiles WHERE line_chat_id=?", (chat["chatId"],)
            ).fetchone()
            raise BridgeError(
                "context_version_conflict",
                "Relationship context changed during save; reload and rebase once.",
                currentVersion=int(latest[0]) if latest else None,
            )
        connection.commit()
        return {
            "ok": True,
            "chatId": chat["chatId"],
            "name": chat["name"],
            "contextVersion": version + 1,
            "summaryThrough": through,
            "contextUpdatedAt": now,
            "coverage": chat_coverage,
            "storage": {"database": "local-chat-memory.sqlite3", "localOnly": True, "lineSourceModified": False},
        }
    finally:
        connection.close()


def line_running() -> bool:
    result = subprocess.run(["/usr/bin/pgrep", "-x", "LINE"], capture_output=True, text=True, timeout=5)
    return result.returncode == 0


def snapshot_metadata() -> dict[str, Any]:
    """Describe the fresh stable snapshot used by the current operation."""
    return {
        "refreshedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "readOnly": True,
        "queryOnly": True,
    }


def line_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "lineRunning": line_running(),
        "dbFound": False,
        "keyAvailable": keychain_available(),
        "decryptOk": False,
    }
    try:
        db = detect_db()
        result.update({"dbFound": True, "dbFilenamePattern": "qw<redacted>.edb", "dbSizeBytes": db.stat().st_size})
    except BridgeError as error:
        result["databaseError"] = error.code
        return result
    if not result["keyAvailable"]:
        return result
    try:
        with open_database() as (connection, _db, _meta):
            result["decryptOk"] = True
            result["chatCount"] = connection.execute("SELECT count(*) FROM _chat").fetchone()[0]
            result["messageCount"] = connection.execute("SELECT count(*) FROM _message").fetchone()[0]
            newest = connection.execute("SELECT MAX(_createdTime) FROM _message").fetchone()[0]
            result["newestMessage"] = iso_time(newest)
            result["databaseSnapshot"] = snapshot_metadata()
    except BridgeError as error:
        result["decryptError"] = error.code
    return result


def list_chats_operation(args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit", 50)), 200))
    with open_database() as (connection, _db, _meta):
        return {
            "ok": True,
            "chats": _list_chats(connection, limit),
            "databaseSnapshot": snapshot_metadata(),
        }


def read_history_operation(args: dict[str, Any]) -> dict[str, Any]:
    value = str(args.get("chat", "")).strip()
    if not value:
        raise BridgeError("invalid_chat", "chat is required.")
    limit = max(1, min(int(args.get("limit", 20)), 200))
    cursor = normalize_cursor(args.get("cursor"))
    order = str(args.get("order", "oldest" if cursor is not None else "latest"))
    with open_database() as (connection, _db, _meta):
        chat = resolve_chat(connection, value)
        history = read_history(connection, chat, limit, order=order, cursor=cursor)
        history["databaseSnapshot"] = snapshot_metadata()
    history["memory"] = persist_memory(history)
    remember_last_chat(history)
    return history


def refresh_safety() -> dict[str, bool]:
    return {
        "typed": False,
        "pasted": False,
        "keyboardInput": False,
        "composerFocused": False,
        "sent": False,
    }


def target_refresh_state(connection: Any, chat_id: str) -> dict[str, Any]:
    count_row = connection.execute("SELECT COUNT(*) FROM _message WHERE _chatId=?", (chat_id,)).fetchone()
    latest = connection.execute(
        "SELECT _createdTime,_id FROM _message WHERE _chatId=? "
        "ORDER BY COALESCE(_createdTime,0) DESC,CAST(_id AS TEXT) COLLATE BINARY DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    timestamp = latest[0] if latest else None
    message_id = str(latest[1]) if latest and latest[1] is not None else None
    return {
        "localMessageCount": int(count_row[0] if count_row else 0),
        "latestTimestampMs": timestamp,
        "latestMessageTime": iso_time(timestamp),
        "latestMessageId": message_id,
        "snapshotRefreshedAt": snapshot_metadata()["refreshedAt"],
    }


def read_refresh_snapshot(value: str) -> tuple[dict[str, Any], dict[str, Any], int, tuple[Any, ...]]:
    """Resolve one exact chat and capture target/global state from a new stable snapshot."""
    with open_database() as (connection, _db, source_meta):
        chat = resolve_chat(connection, value)
        state = target_refresh_state(connection, chat["chatId"])
        global_count = int(connection.execute("SELECT COUNT(*) FROM _message").fetchone()[0])
    return chat, state, global_count, tuple(sorted(source_meta.items()))


def target_advanced(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_timestamp = before.get("latestTimestampMs")
    after_timestamp = after.get("latestTimestampMs")
    before_id = before.get("latestMessageId")
    after_id = after.get("latestMessageId")
    if after_timestamp is None or after_id is None:
        return False
    if before_timestamp is None or before_id is None:
        return True
    return int(after_timestamp) > int(before_timestamp) or (
        int(after_timestamp) == int(before_timestamp) and str(after_id) > str(before_id)
    )


def poll_target_advance(
    chat_id: str,
    before: dict[str, Any],
    global_before: int,
    deadline: float,
    initial_signature: tuple[Any, ...],
) -> tuple[bool, dict[str, Any], int, tuple[Any, ...]]:
    """Wait for target evidence, using source metadata to limit full snapshots."""
    after = before
    global_after = global_before
    signature = initial_signature
    last_snapshot_at = time.monotonic()
    polling_deadline = max(time.monotonic(), deadline - REFRESH_FINAL_SNAPSHOT_RESERVE_SECONDS)
    while time.monotonic() < polling_deadline:
        remaining = polling_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(REFRESH_POLL_SECONDS, remaining))
        now = time.monotonic()
        current_signature = source_signature()
        if current_signature == signature or now - last_snapshot_at < REFRESH_SNAPSHOT_MIN_SECONDS:
            continue
        _chat, after, global_after, signature = read_refresh_snapshot(chat_id)
        last_snapshot_at = time.monotonic()
        if target_advanced(before, after):
            return True, after, global_after, signature

    # Always finish a phase with target-specific DB evidence, even when source
    # metadata did not change or changed too close to the deadline.
    _chat, after, global_after, signature = read_refresh_snapshot(chat_id)
    return target_advanced(before, after), after, global_after, signature


def graphical_session_available() -> bool | None:
    """Return False for a locked/missing console session and None when unknown."""
    try:
        console = subprocess.run(
            ["/usr/bin/stat", "-f", "%Su", "/dev/console"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        console_user = console.stdout.strip()
        if console.returncode != 0 or console_user in {"", "root", "loginwindow"}:
            return False
        locked = subprocess.run(
            ["/usr/sbin/ioreg", "-n", "Root", "-d1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if locked.returncode != 0:
        return None
    match = re.search(r'"IOConsoleLocked"\s*=\s*(Yes|No)', locked.stdout)
    if not match:
        return None
    return match.group(1) == "No"


def run_fixed_peekaboo_action(command: list[str], timeout: int, failure_code: str) -> dict[str, Any]:
    """Run one internally constructed LINE-only action and redact command failures."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(1, timeout))
    except subprocess.TimeoutExpired:
        raise BridgeError(failure_code, "The fixed LINE-only Peekaboo action timed out.", peekabooCode="TIMEOUT")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise BridgeError(failure_code, "Peekaboo did not return valid JSON for the fixed LINE-only action.")
    if not isinstance(payload, dict):
        raise BridgeError(failure_code, "Peekaboo did not return a JSON object for the fixed LINE-only action.")
    if result.returncode != 0 or payload.get("success") is False:
        peekaboo_code = (payload.get("error") or {}).get("code")
        raise BridgeError(failure_code, "The fixed LINE-only Peekaboo action failed.", peekabooCode=peekaboo_code)
    return payload


def focus_line_window(timeout: int) -> dict[str, Any]:
    """Activate only LINE by name; never use app-switch --cycle (Cmd+Tab)."""
    return run_fixed_peekaboo_action(
        [
            str(_peekaboo_path()),
            "app",
            "switch",
            "--to",
            "LINE",
            "--verify",
            "--json",
            "--no-remote",
        ],
        timeout,
        "line_focus_failed",
    )


def launch_line(timeout: int) -> dict[str, Any]:
    return run_fixed_peekaboo_action(
        [
            str(_peekaboo_path()),
            "app",
            "launch",
            "--bundle-id",
            LINE_BUNDLE_ID,
            "--wait-until-ready",
            "--json",
            "--no-remote",
        ],
        timeout,
        "line_launch_failed",
    )


def refresh_result(
    *,
    chat: dict[str, Any],
    strategy: str,
    advanced: bool,
    verified: bool,
    before: dict[str, Any],
    after: dict[str, Any],
    global_before: int,
    global_after: int,
    started: float,
) -> dict[str, Any]:
    return {
        "chatId": chat["chatId"],
        "name": chat["name"],
        "strategy": strategy,
        "targetAdvanced": advanced,
        "freshnessVerified": verified,
        "freshnessMode": (
            "target_advanced" if advanced else ("stable_after_line_activation" if verified else "unverified")
        ),
        "before": before,
        "after": after,
        "globalMessageCountBefore": global_before,
        "globalMessageCountAfter": global_after,
        "elapsedMs": max(0, int(round((time.monotonic() - started) * 1000))),
        "safety": refresh_safety(),
    }


def refresh_latest_operation(args: dict[str, Any]) -> dict[str, Any]:
    value = str(args.get("chat", "")).strip()
    if not value:
        raise BridgeError("invalid_chat", "chat is required.")
    timeout_value = args.get("timeoutSeconds", 30)
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, int) or not 5 <= timeout_value <= 60:
        raise BridgeError("invalid_timeout", "timeoutSeconds must be an integer from 5 through 60.")
    started = time.monotonic()
    deadline = started + timeout_value
    chat, before, global_before, signature = read_refresh_snapshot(value)
    passive_seconds = min(5.0, max(1.0, timeout_value - 5.0))
    passive_deadline = min(deadline, started + passive_seconds)
    advanced, after, global_after, signature = poll_target_advance(
        chat["chatId"], before, global_before, passive_deadline, signature
    )
    if advanced:
        return {
            "ok": True,
            **refresh_result(
                chat=chat,
                strategy="passive",
                advanced=True,
                verified=True,
                before=before,
                after=after,
                global_before=global_before,
                global_after=global_after,
                started=started,
            ),
        }

    if graphical_session_available() is False:
        details = refresh_result(
            chat=chat,
            strategy="passive",
            advanced=False,
            verified=False,
            before=before,
            after=after,
            global_before=global_before,
            global_after=global_after,
            started=started,
        )
        raise BridgeError(
            "locked_session_unavailable",
            "The macOS graphical session is locked or no console user is available; no UI action was attempted.",
            **details,
        )

    strategy = "focus" if line_running() else "launch"
    if not _peekaboo_path().is_file():
        details = refresh_result(
            chat=chat,
            strategy=strategy,
            advanced=False,
            verified=False,
            before=before,
            after=after,
            global_before=global_before,
            global_after=global_after,
            started=started,
        )
        raise BridgeError(
            "peekaboo_missing",
            "The project-local/system Peekaboo binary is unavailable; no UI action was attempted.",
            **details,
        )
    remaining = max(1, int(math.ceil(deadline - time.monotonic())))
    try:
        if strategy == "focus":
            focus_line_window(remaining)
        else:
            launch_line(remaining)
    except BridgeError as error:
        details = refresh_result(
            chat=chat,
            strategy=strategy,
            advanced=False,
            verified=False,
            before=before,
            after=after,
            global_before=global_before,
            global_after=global_after,
            started=started,
        )
        raise BridgeError(error.code, error.detail, **details, **error.extra)

    advanced, after, global_after, _signature = poll_target_advance(
        chat["chatId"], before, global_before, deadline, signature
    )
    details = refresh_result(
        chat=chat,
        strategy=strategy,
        advanced=advanced,
        verified=True,
        before=before,
        after=after,
        global_before=global_before,
        global_after=global_after,
        started=started,
    )
    return {"ok": True, **details}


def _peekaboo_path() -> Path:
    project_binary = ROOT / "runtime" / "bin" / "peekaboo"
    return project_binary if project_binary.is_file() else Path("/opt/homebrew/bin/peekaboo")


def _run_json(command: list[str], timeout: int = 30, stage: str = "peekaboo_action") -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BridgeError(
            "peekaboo_timeout",
            "Peekaboo did not finish the audited UI snapshot within the safety timeout; no fallback input was attempted.",
            stage=stage,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        if result.returncode != 0:
            raise BridgeError(
                "peekaboo_failed",
                "The audited Peekaboo command failed without valid JSON.",
                stage=stage,
            )
        raise BridgeError("peekaboo_invalid_output", "Peekaboo did not return valid JSON.", stage=stage)
    if not isinstance(payload, dict):
        raise BridgeError("peekaboo_invalid_output", "Peekaboo did not return a JSON object.", stage=stage)
    if result.returncode != 0 or payload.get("success") is False:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        failed = next((step for step in steps if isinstance(step, dict) and step.get("success") is False), {})
        failure_text = str(failed.get("error") or error.get("message") or "").casefold()
        if "snapshot" in failure_text and "stale" in failure_text:
            failure_reason = "snapshot_stale"
        elif "element" in failure_text and ("not found" in failure_text or "no element" in failure_text):
            failure_reason = "element_not_found"
        elif "invalid" in failure_text and "parameter" in failure_text:
            failure_reason = "invalid_parameters"
        else:
            failure_reason = "unclassified"
        raise BridgeError(
            "peekaboo_failed",
            "The audited Peekaboo command failed.",
            stage=stage,
            peekabooCode=error.get("code"),
            failedCommand=failed.get("command"),
            failureReason=failure_reason,
        )
    return payload


def is_safe_search_field_candidate(element: dict[str, Any]) -> bool:
    """Match current Peekaboo textField and older AXTextField role spellings."""
    role = str(element.get("role") or element.get("role_description") or "")
    normalized_role = re.sub(r"[^a-z]", "", role.casefold())
    frame = element.get("frame") or element.get("bounds") or {}
    x = float(frame.get("x", 10_000))
    y = float(frame.get("y", 10_000))
    return (
        normalized_role in {"textfield", "axtextfield", "searchfield", "axsearchfield"}
        and x < 700
        and y < 450
        and bool(element.get("id"))
    )


def _element_bounds(element: dict[str, Any]) -> tuple[float, float, float, float] | None:
    bounds = element.get("frame") or element.get("bounds") or {}
    try:
        return (
            float(bounds["x"]),
            float(bounds["y"]),
            float(bounds["width"]),
            float(bounds["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def resolve_exact_chat_click_target(elements: list[dict[str, Any]], chat_name: str) -> dict[str, Any]:
    exact = []
    for element in elements:
        labels = [element.get(key) for key in ("label", "title", "value", "name")]
        if chat_name in labels and element.get("id"):
            exact.append(element)
    if len(exact) != 1:
        raise BridgeError(
            "safe_chat_result_unavailable",
            "Could not identify one exact search result; no click or scroll was attempted.",
            candidateCount=len(exact),
        )
    label = exact[0]
    if label.get("is_actionable") is not False:
        return label
    label_bounds = _element_bounds(label)
    if label_bounds is None:
        raise BridgeError(
            "safe_chat_result_unavailable",
            "The exact search result had no safe actionable container; no click or scroll was attempted.",
            candidateCount=0,
        )
    x, y, width, height = label_bounds
    center_x = x + width / 2
    center_y = y + height / 2
    containers = []
    for element in elements:
        if not element.get("id") or element.get("is_actionable") is not True:
            continue
        bounds = _element_bounds(element)
        if bounds is None:
            continue
        item_x, item_y, item_width, item_height = bounds
        if item_x <= center_x <= item_x + item_width and item_y <= center_y <= item_y + item_height:
            containers.append((item_width * item_height, element))
    if not containers:
        raise BridgeError(
            "safe_chat_result_unavailable",
            "The exact search result had no safe actionable container; no click or scroll was attempted.",
            candidateCount=0,
        )
    containers.sort(key=lambda item: item[0])
    if len(containers) > 1 and containers[0][0] == containers[1][0]:
        raise BridgeError(
            "safe_chat_result_unavailable",
            "The exact search result had ambiguous actionable containers; no click or scroll was attempted.",
            candidateCount=2,
        )
    return containers[0][1]


def run_exact_chat_script(peekaboo: Path, target_id: str) -> None:
    """Re-observe and click one pre-validated exact label in one Peekaboo process."""
    with tempfile.TemporaryDirectory(prefix="line-peekaboo-") as temp_name:
        temp_path = Path(temp_name)
        os.chmod(temp_path, 0o700)
        script_path = temp_path / "open-exact-chat.peekaboo.json"
        screenshot_path = temp_path / "line-search.png"
        script = {
            "description": "Open one pre-validated exact LINE search result",
            "steps": [
                {
                    "stepId": "observe_exact_result",
                    "command": "see",
                    "params": {
                        "generic": {
                            "_0": {
                                "app": "LINE",
                                "path": str(screenshot_path),
                            }
                        }
                    },
                },
                {
                    "stepId": "open_exact_result",
                    "command": "click",
                    "params": {"generic": {"_0": {"query": target_id, "app": "LINE"}}},
                },
            ],
        }
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
        os.chmod(script_path, 0o600)
        _run_json(
            [str(peekaboo), "run", str(script_path), "--json", "--no-remote"],
            timeout=45,
            stage="open_exact_chat_script",
        )


def open_exact_chat_result(peekaboo: Path, chat_name: str, attempts: int = 3) -> None:
    """Open one exact search result, retrying only expired Peekaboo snapshots."""
    for attempt in range(attempts):
        results = _run_json(
            [
                str(peekaboo),
                "see",
                "--app",
                "LINE",
                "--timeout-seconds",
                "15",
                "--no-web-focus",
                "--json",
                "--no-remote",
            ],
            timeout=25,
            stage="inspect_search_results",
        )
        result_data = results.get("data", {})
        result_snapshot = result_data.get("snapshot_id") or result_data.get("snapshotId")
        result_elements = result_data.get("ui_elements") or result_data.get("uiElements") or []
        if not result_snapshot:
            raise BridgeError(
                "safe_chat_result_unavailable",
                "The exact search result had no valid snapshot; no click or scroll was attempted.",
                candidateCount=0,
            )
        target = resolve_exact_chat_click_target(result_elements, chat_name)
        try:
            _run_json(
                [
                    str(peekaboo),
                    "click",
                    "--on",
                    str(target["id"]),
                    "--snapshot",
                    str(result_snapshot),
                    "--json",
                    "--no-remote",
                ],
                stage="open_exact_chat",
            )
            return
        except BridgeError as error:
            stale = error.code == "peekaboo_failed" and error.extra.get("peekabooCode") == "SNAPSHOT_STALE"
            if not stale:
                raise
            if attempt + 1 >= attempts:
                run_exact_chat_script(peekaboo, str(target["id"]))
                return
            time.sleep(0.2)


def sync_older_operation(args: dict[str, Any]) -> dict[str, Any]:
    """Use only set-value/click/scroll; never type, paste, press, hotkey, or shell."""
    value = str(args.get("chat", "")).strip()
    rounds = max(1, min(int(args.get("rounds", 5)), 20))
    with open_database() as (connection, _db, _meta):
        chat = resolve_chat(connection, value)
        before = coverage(connection, chat["chatId"])
    if not chat["name"]:
        raise BridgeError(
            "chat_name_unavailable",
            "The resolved chat has no display name; refusing UI backfill because a chatId is not a safe search label.",
        )
    if not line_running():
        raise BridgeError("line_not_running", "LINE must be running for older-message backfill.")
    peekaboo = _peekaboo_path()
    if not peekaboo.is_file():
        raise BridgeError("peekaboo_missing", "The project-local/system Peekaboo binary is unavailable.")

    help_result = subprocess.run([str(peekaboo), "set-value", "--help"], capture_output=True, text=True, timeout=10)
    if help_result.returncode != 0:
        raise BridgeError("peekaboo_too_old", "Installed Peekaboo lacks set-value; refusing keyboard-based fallback.")

    seen = _run_json(
        [
            str(peekaboo), "see", "--app", "LINE", "--timeout-seconds", "15", "--no-web-focus", "--json", "--no-remote",
        ],
        timeout=25,
        stage="inspect_line",
    )
    data = seen.get("data", {})
    snapshot = data.get("snapshot_id") or data.get("snapshotId")
    elements = data.get("ui_elements") or data.get("uiElements") or []
    text_fields = [element for element in elements if is_safe_search_field_candidate(element)]
    if not snapshot or len(text_fields) != 1:
        raise BridgeError(
            "safe_search_field_unavailable",
            "Could not uniquely identify LINE's top-left search field; refusing to use keyboard or guess.",
            candidateCount=len(text_fields),
        )
    field_id = str(text_fields[0]["id"])
    _run_json(
        [str(peekaboo), "set-value", chat["name"], "--on", field_id, "--snapshot", str(snapshot), "--json", "--no-remote"],
        stage="set_search_value",
    )
    time.sleep(1.0)
    open_exact_chat_result(peekaboo, chat["name"])
    time.sleep(0.8)
    for _round in range(rounds):
        _run_json(
            [str(peekaboo), "scroll", "--direction", "up", "--amount", "12", "--app", "LINE", "--json", "--no-remote"],
            timeout=45,
            stage="scroll_history",
        )
        time.sleep(1.2)
    with open_database() as (connection, _db, _meta):
        after = coverage(connection, chat["chatId"])
    return {
        "ok": True,
        "chatId": chat["chatId"],
        "name": chat["name"],
        "rounds": rounds,
        "before": before,
        "after": after,
        "newMessages": max(0, after["localMessageCount"] - before["localMessageCount"]),
        "safety": {"typed": False, "pasted": False, "keyboardInput": False, "composerFocused": False, "sent": False},
    }


def _peekaboo_health() -> dict[str, Any]:
    binary = _peekaboo_path()
    if not binary.is_file():
        return {"available": False}
    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    permissions = subprocess.run(
        [str(binary), "permissions", "status", "--json", "--no-remote"], capture_output=True, text=True, timeout=15
    )
    payload: dict[str, Any] = {"available": version.returncode == 0, "version": version.stdout.strip()}
    try:
        payload["permissions"] = json.loads(permissions.stdout).get("data", {}).get("permissions", [])
    except json.JSONDecodeError:
        payload["permissions"] = []
    return payload


def line_health() -> dict[str, Any]:
    status = line_status()
    memory = {"exists": MEMORY_DB.is_file(), "messageCount": 0, "profileCount": 0}
    if MEMORY_DB.is_file():
        connection = sqlite3.connect(f"file:{MEMORY_DB}?mode=ro", uri=True)
        try:
            memory["messageCount"] = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            memory["profileCount"] = connection.execute("SELECT count(*) FROM line_chat_profiles").fetchone()[0]
        except sqlite3.Error:
            memory["readable"] = False
        finally:
            connection.close()
    power = subprocess.run(["/usr/bin/pmset", "-g", "custom"], capture_output=True, text=True, timeout=10).stdout
    ac_sleep_disabled = bool(re.search(r"AC Power:.*?\bsleep\s+0\b", power, flags=re.S))
    return {
        "ok": True,
        "status": status,
        "snapshotPolicy": {"stableCopyRetries": 5, "sourceOpenedBySQLite": False, "readOnly": True, "queryOnly": True},
        "memory": memory,
        "mcp": {"allowedTools": ALLOWED_TOOLS, "forbiddenToolsPresent": False},
        "peekaboo": _peekaboo_health(),
        "power": {"acSleepDisabled": ac_sleep_disabled},
        "remote": {
            "remoteControl": "unverified",
            "computerUse": "unverified",
            "lockedUse": "unverified",
            "lastPhoneSuccess": "not_recorded",
        },
    }


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    args = payload.get("args") or {}
    if operation not in ALLOWED_TOOLS:
        raise BridgeError("tool_not_allowed", "Requested operation is not in the read-only allow-list.", tool=operation)
    if operation == "line_status":
        return line_status()
    if operation == "list_chats":
        return list_chats_operation(args)
    if operation == "read_history":
        return read_history_operation(args)
    if operation == "refresh_latest":
        return refresh_latest_operation(args)
    if operation == "get_relationship_context":
        return get_relationship_context_operation(args)
    if operation == "save_relationship_context":
        return save_relationship_context_operation(args)
    if operation == "sync_older_messages":
        return sync_older_operation(args)
    return line_health()


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "invalid_invocation"}))
        return 2
    try:
        result = dispatch(json.loads(sys.argv[1]))
    except BridgeError as error:
        result = error.payload()
    except Exception as error:
        result = {"ok": False, "error": "internal_error", "detail": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
