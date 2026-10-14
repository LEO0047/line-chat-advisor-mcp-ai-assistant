import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import types
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("line_readonly_bridge", PROJECT / "src/line-readonly-mcp/bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BridgeTests(unittest.TestCase):
    def test_attachment_info_scopes_chat_and_redacts_secrets(self):
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.execute("CREATE TABLE _message (_chatId TEXT,_id TEXT,_createdTime INTEGER,_text TEXT,_contentType INTEGER,_contentMetadata TEXT)")
        c.execute("INSERT INTO _message VALUES (?,?,?,?,?,?)", ("chat-a", "m1", 1000, "sample.pdf", 14,
                  json.dumps({"FILE_SIZE": "42", "OID": "secret", "keyMaterial": "secret"})))
        result = bridge.attachment_info(c, "chat-a", "m1")
        self.assertEqual(result["expectedBytes"], 42)
        self.assertFalse(result["downloaded"])
        self.assertNotIn("secret", json.dumps(result))
        with self.assertRaises(bridge.BridgeError):
            bridge.attachment_info(c, "chat-b", "m1")
        c.execute("UPDATE _message SET _contentMetadata='not json'")
        self.assertIsNone(bridge.attachment_info(c, "chat-a", "m1")["expectedBytes"])
        c.execute("UPDATE _message SET _contentType=0")
        with self.assertRaises(bridge.BridgeError):
            bridge.attachment_info(c, "chat-a", "m1")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_line_db_path_is_home_relative_without_private_username(self):
        self.assertEqual(bridge.LINE_DB_DIR.parts[-8:], (
            "Library", "Containers", "jp.naver.line.mac", "Data",
            "Library", "Containers", "jp.naver.line", "Data", "db",
        )[-8:])
        source = (PROJECT / "src/line-readonly-mcp/bridge.py").read_text(encoding="utf-8")
        self.assertNotIn('"/Users/', source)

    def test_detects_current_qw_database_name(self):
        db = self.root / "qw46abcdef.edb"
        db.write_bytes(b"db")
        self.assertEqual(bridge.detect_db(self.root), db)

    def test_excludes_sibling_databases_and_fails_on_ambiguity(self):
        (self.root / "album_qw111.edb").write_bytes(b"album")
        (self.root / "keep_qw111.edb").write_bytes(b"keep")
        first = self.root / "qw111.edb"
        first.write_bytes(b"one")
        self.assertEqual(bridge.detect_db(self.root), first)
        (self.root / "qwb222.edb").write_bytes(b"two")
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.detect_db(self.root)
        self.assertEqual(caught.exception.code, "db_ambiguous")

    def test_snapshot_copies_sidecars_without_modifying_source_and_cleans(self):
        db = self.root / "qw123.edb"
        wal = Path(f"{db}-wal")
        shm = Path(f"{db}-shm")
        db.write_bytes(b"main")
        wal.write_bytes(b"wal")
        shm.write_bytes(b"shm")
        before = {path: (digest(path), path.stat().st_mtime_ns) for path in (db, wal, shm)}
        snapshots = self.root / "snapshots"
        with mock.patch.object(bridge, "SNAPSHOT_PARENT", snapshots):
            with bridge.stable_snapshot(db) as (copy, metadata):
                temp_dir = copy.parent
                self.assertEqual(copy.read_bytes(), b"main")
                self.assertEqual(Path(f"{copy}-wal").read_bytes(), b"wal")
                self.assertEqual(Path(f"{copy}-shm").read_bytes(), b"shm")
                self.assertEqual(set(metadata), {db.name, wal.name, shm.name})
            self.assertFalse(temp_dir.exists())
        after = {path: (digest(path), path.stat().st_mtime_ns) for path in (db, wal, shm)}
        self.assertEqual(before, after)

    def test_keychain_missing_does_not_return_secret_material(self):
        with mock.patch.object(bridge.subprocess, "run", side_effect=OSError("missing")):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.keychain_key()
        self.assertEqual(caught.exception.code, "keychain_key_missing")
        self.assertNotIn("key", caught.exception.detail.lower().replace("keychain", ""))

    def test_timestamp_has_timezone_offset(self):
        value = bridge.iso_time(1_767_225_600_000)
        self.assertRegex(value, r"[+-]\d\d:\d\d$")

    def test_decryption_success_uses_readonly_and_query_only(self):
        db = self.root / "qw123.edb"
        snapshot = self.root / "snapshot.edb"
        db.write_bytes(b"encrypted")
        snapshot.write_bytes(b"copy")

        class FakeConnection:
            instances = []

            def __init__(self, path, flags):
                self.path = path
                self.flags = flags
                self.pragmas = []
                self.closed = False
                self.__class__.instances.append(self)

            def pragma(self, name, value):
                self.pragmas.append((name, value))

            def execute(self, _sql):
                return types.SimpleNamespace(fetchone=lambda: (1,))

            def close(self):
                self.closed = True

        fake_apsw = types.SimpleNamespace(SQLITE_OPEN_READONLY=0x1, Connection=FakeConnection)

        @contextlib.contextmanager
        def fake_snapshot(_db):
            yield snapshot, {db.name: (9, 1, 1)}

        with mock.patch.dict("sys.modules", {"apsw": fake_apsw}), mock.patch.object(
            bridge, "detect_db", return_value=db
        ), mock.patch.object(bridge, "keychain_key", return_value="0" * 32), mock.patch.object(
            bridge, "stable_snapshot", fake_snapshot
        ):
            with bridge.open_database() as (connection, source, metadata):
                self.assertEqual(source, db)
                self.assertEqual(metadata[db.name], (9, 1, 1))
                self.assertEqual(connection.flags, fake_apsw.SQLITE_OPEN_READONLY)
                self.assertIn(("query_only", True), connection.pragmas)
                self.assertIn(("cipher", "aes128cbc"), connection.pragmas)
        self.assertTrue(FakeConnection.instances[0].closed)

    def test_decryption_failure_is_redacted_and_closes_connection(self):
        db = self.root / "qw123.edb"
        snapshot = self.root / "snapshot.edb"
        db.write_bytes(b"encrypted")
        snapshot.write_bytes(b"copy")

        class FakeConnection:
            instance = None

            def __init__(self, _path, flags):
                self.flags = flags
                self.closed = False
                self.__class__.instance = self

            def pragma(self, _name, _value):
                return None

            def execute(self, _sql):
                raise RuntimeError("secret-looking database failure")

            def close(self):
                self.closed = True

        fake_apsw = types.SimpleNamespace(SQLITE_OPEN_READONLY=0x1, Connection=FakeConnection)

        @contextlib.contextmanager
        def fake_snapshot(_db):
            yield snapshot, {}

        with mock.patch.dict("sys.modules", {"apsw": fake_apsw}), mock.patch.object(
            bridge, "detect_db", return_value=db
        ), mock.patch.object(bridge, "keychain_key", return_value="f" * 32), mock.patch.object(
            bridge, "stable_snapshot", fake_snapshot
        ):
            with self.assertRaises(bridge.BridgeError) as caught:
                with bridge.open_database():
                    pass
        self.assertEqual(caught.exception.code, "decrypt_failed")
        self.assertNotIn("secret-looking", caught.exception.detail)
        self.assertTrue(FakeConnection.instance.closed)

    def make_line_db(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE _chat(_id TEXT PRIMARY KEY,_lastUpdatedTime INTEGER)")
        con.execute("CREATE TABLE _groupChat(_chatMid TEXT,_chatName TEXT)")
        con.execute("CREATE TABLE _square(_mid TEXT,_name TEXT)")
        con.execute("CREATE TABLE _contact(_mid TEXT,_displayNameOverridden TEXT,_displayName TEXT,_targetProfileDetail TEXT)")
        con.execute("CREATE TABLE _profile(_mid TEXT)")
        con.execute(
            "CREATE TABLE _message(_chatId TEXT,_createdTime INTEGER,_from TEXT,_text TEXT,"
            "_contentType INTEGER,_id TEXT,_contentMetadata TEXT)"
        )
        con.execute("INSERT INTO _profile VALUES('me')")
        return con

    def test_duplicate_exact_name_returns_candidate_chat_ids(self):
        con = self.make_line_db()
        con.executemany("INSERT INTO _chat VALUES(?,?)", [("u1", 2), ("u2", 1)])
        con.executemany("INSERT INTO _contact VALUES(?,?,?,?)", [("u1", None, "Sam", None), ("u2", None, "Sam", None)])
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.resolve_chat(con, "Sam")
        payload = caught.exception.payload()
        self.assertEqual(payload["error"], "ambiguous_chat_name")
        self.assertEqual({item["chatId"] for item in payload["candidates"]}, {"u1", "u2"})

    def test_direction_timestamp_group_and_message_type(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('c1',1000)")
        con.execute("INSERT INTO _groupChat VALUES('c1','Group')")
        con.execute("INSERT INTO _contact VALUES('u2',NULL,'Alice',NULL)")
        con.executemany(
            "INSERT INTO _message VALUES(?,?,?,?,?,?,?)",
            [
                ("c1", 1_767_225_600_000, "u2", None, 7, "m1", None),
                ("c1", 1_767_225_660_000, "me", "hello", 0, "m2", None),
            ],
        )
        result = bridge.read_history(con, {"chatId": "c1", "name": "Group", "isGroup": True}, 20)
        self.assertTrue(result["isGroup"])
        self.assertEqual(result["messages"][0]["direction"], "in")
        self.assertEqual(result["messages"][0]["messageType"], "sticker")
        self.assertIsNone(result["messages"][0]["text"])
        self.assertEqual(result["messages"][1]["direction"], "out")
        self.assertRegex(result["messages"][1]["absoluteTime"], r"[+-]\d\d:\d\d$")

    def test_cursor_paginates_all_messages_by_timestamp_and_message_id(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('u1',1000)")
        con.execute("INSERT INTO _contact VALUES('u1',NULL,'Paged',NULL)")
        base = 1_767_225_600_000
        rows = []
        for index in range(575):
            rows.append(("u1", base + index // 3, "me" if index % 2 else "u1", f"text {index}", 0, f"m{index:04d}", None))
        con.executemany("INSERT INTO _message VALUES(?,?,?,?,?,?,?)", rows)

        chat = {"chatId": "u1", "name": "Paged", "isGroup": False}
        first = bridge.read_history(con, chat, 200, order="oldest")
        second = bridge.read_history(
            con, chat, 200, order="oldest", cursor=first["pagination"]["nextCursor"]
        )
        third = bridge.read_history(
            con, chat, 200, order="oldest", cursor=second["pagination"]["nextCursor"]
        )
        messages = first["messages"] + second["messages"] + third["messages"]

        self.assertEqual([len(first["messages"]), len(second["messages"]), len(third["messages"])], [200, 200, 175])
        self.assertEqual(len({message["messageId"] for message in messages}), 575)
        self.assertEqual([message["messageId"] for message in messages], [f"m{index:04d}" for index in range(575)])
        self.assertTrue(first["pagination"]["hasMore"])
        self.assertTrue(second["pagination"]["hasMore"])
        self.assertFalse(third["pagination"]["hasMore"])
        self.assertIsNone(third["pagination"]["nextCursor"])
        self.assertEqual(third["coverage"]["localMessageCount"], 575)
        self.assertEqual(first["coverage"]["oldestLocalMessage"], bridge.iso_time(base))
        self.assertEqual(third["coverage"]["newestLocalMessage"], bridge.iso_time(base + 574 // 3))

    def test_cursor_validation_rejects_unstable_shapes(self):
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.normalize_cursor({"timestampMs": 1})
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_default_history_behavior_still_returns_latest_messages(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('u1',1000)")
        con.execute("INSERT INTO _contact VALUES('u1',NULL,'Latest',NULL)")
        con.executemany(
            "INSERT INTO _message VALUES(?,?,?,?,?,?,?)",
            [("u1", index, "u1", str(index), 0, f"m{index:03d}", None) for index in range(1, 6)],
        )
        result = bridge.read_history(con, {"chatId": "u1", "name": "Latest", "isGroup": False}, 2)
        self.assertEqual([message["messageId"] for message in result["messages"]], ["m004", "m005"])
        self.assertEqual(result["pagination"]["order"], "latest")
        self.assertTrue(result["pagination"]["hasMore"])
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.normalize_cursor({"timestampMs": -1, "messageId": "m1"})
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_sticon_metadata_overrides_text_type_and_placeholder(self):
        metadata = json.dumps(
            {
                "STICON_OWNERSHIP": "[\"product\"]",
                "REPLACE": json.dumps({"sticon": {"resources": [{"productId": "product"}]}}),
            }
        )
        message_type, text = bridge.classify_message(0, "(wailing Moon)", metadata)
        self.assertEqual(message_type, "sticker")
        self.assertIsNone(text)

    def test_memory_deduplicates_structured_messages(self):
        memory = self.root / "memory.sqlite3"
        history = {
            "chatId": "u1",
            "name": "Test",
            "isGroup": False,
            "messages": [
                {
                    "messageId": "m1",
                    "absoluteTime": "2026-01-01T10:00:00+08:00",
                    "timestampMs": 1,
                    "direction": "in",
                    "sender": "Test",
                    "messageType": "text",
                    "messageTypeCode": 0,
                    "text": "hello",
                    "originalText": "hello",
                }
            ],
            "coverage": {
                "localMessageCount": 1,
                "oldestLocalMessage": "2026-01-01T10:00:00+08:00",
                "newestLocalMessage": "2026-01-01T10:00:00+08:00",
            },
        }
        with mock.patch.object(bridge, "MEMORY_DB", memory):
            first = bridge.persist_memory(history)
            second = bridge.persist_memory(history)
            history["messages"][0]["messageType"] = "sticker"
            history["messages"][0]["text"] = None
            corrected = bridge.persist_memory(history)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(corrected["inserted"], 0)
        con = sqlite3.connect(memory)
        self.assertEqual(con.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT body,classification FROM messages").fetchone(), ("[sticker]", "attachment"))
        meta = con.execute(
            "SELECT direction,message_type,timestamp_ms,absolute_time,message_type_code,original_text "
            "FROM line_message_meta"
        ).fetchone()
        self.assertEqual(
            meta,
            ("in", "sticker", 1, "2026-01-01T10:00:00+08:00", 0, "hello"),
        )
        con.close()

    def test_older_memory_page_does_not_regress_recent_summary(self):
        memory = self.root / "memory.sqlite3"

        def history(message_id, timestamp, text):
            return {
                "chatId": "u1",
                "name": "Test",
                "isGroup": False,
                "messages": [
                    {
                        "messageId": message_id,
                        "absoluteTime": f"2026-01-01T10:00:0{timestamp}+08:00",
                        "timestampMs": timestamp,
                        "direction": "in",
                        "sender": "Test",
                        "messageType": "text",
                        "messageTypeCode": 0,
                        "text": text,
                        "originalText": text,
                    }
                ],
                "coverage": {
                    "localMessageCount": 2,
                    "oldestLocalMessage": "2026-01-01T10:00:01+08:00",
                    "newestLocalMessage": "2026-01-01T10:00:02+08:00",
                },
            }

        with mock.patch.object(bridge, "MEMORY_DB", memory):
            bridge.persist_memory(history("m2", 2, "newest"))
            bridge.persist_memory(history("m1", 1, "older"))

        con = sqlite3.connect(memory)
        summary = con.execute(
            "SELECT recent_summary FROM line_chat_profiles WHERE line_chat_id='u1'"
        ).fetchone()[0]
        self.assertLess(summary.index("older"), summary.index("newest"))
        self.assertTrue(summary.endswith("newest"))
        con.close()

    def context_history(self):
        return {
            "chatId": "u1",
            "name": "Context Test",
            "isGroup": False,
            "messages": [
                {
                    "messageId": "m1",
                    "absoluteTime": bridge.iso_time(1),
                    "timestampMs": 1,
                    "direction": "in",
                    "sender": "Context Test",
                    "messageType": "text",
                    "messageTypeCode": 0,
                    "text": "first",
                    "originalText": "first",
                },
                {
                    "messageId": "m2",
                    "absoluteTime": bridge.iso_time(2),
                    "timestampMs": 2,
                    "direction": "out",
                    "sender": "me",
                    "messageType": "text",
                    "messageTypeCode": 0,
                    "text": "second",
                    "originalText": "second",
                },
            ],
            "coverage": {
                "localMessageCount": 2,
                "oldestLocalMessage": bridge.iso_time(1),
                "newestLocalMessage": bridge.iso_time(2),
            },
        }

    def context_line_db(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('u1',2)")
        con.execute("INSERT INTO _contact VALUES('u1',NULL,'Context Test',NULL)")
        con.executemany(
            "INSERT INTO _message VALUES(?,?,?,?,?,?,?)",
            [
                ("u1", 1, "u1", "first", 0, "m1", None),
                ("u1", 2, "me", "second", 0, "m2", None),
            ],
        )
        return con

    def test_relationship_context_starts_uninitialized_without_message_content(self):
        memory = self.root / "memory.sqlite3"
        con = self.context_line_db()

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        with mock.patch.object(bridge, "MEMORY_DB", memory), mock.patch.object(
            bridge, "open_database", fake_open_database
        ):
            result = bridge.get_relationship_context_operation({"chat": "Context Test"})

        self.assertFalse(result["initialized"])
        self.assertEqual(result["contextVersion"], 0)
        self.assertIsNone(result["summaryThrough"])
        self.assertEqual(result["coverage"]["localMessageCount"], 2)
        self.assertTrue(result["storage"]["localOnly"])
        self.assertFalse(result["storage"]["lineSourceModified"])
        self.assertEqual(memory.stat().st_mode & 0o777, 0o600)

    def test_relationship_context_saves_imported_cursor_and_rejects_stale_version(self):
        memory = self.root / "memory.sqlite3"
        con = self.context_line_db()

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        with mock.patch.object(bridge, "MEMORY_DB", memory):
            bridge.persist_memory(self.context_history())
            with mock.patch.object(bridge, "open_database", fake_open_database):
                initial = bridge.get_relationship_context_operation({"chat": "Context Test"})
                saved = bridge.save_relationship_context_operation(
                    {
                        "chat": "Context Test",
                        "expectedContextVersion": initial["contextVersion"],
                        "summaryThrough": {"timestampMs": 2, "messageId": "m2"},
                        "relationship": "朋友",
                        "stableBackground": "認識一段時間",
                        "toneNotes": "短句、自然",
                        "avoidReasking": "不要重問已回答的安排",
                    }
                )
                current = bridge.get_relationship_context_operation({"chat": "Context Test"})
                with self.assertRaises(bridge.BridgeError) as conflict:
                    bridge.save_relationship_context_operation(
                        {
                            "chat": "Context Test",
                            "expectedContextVersion": 0,
                            "summaryThrough": {"timestampMs": 2, "messageId": "m2"},
                            "relationship": "stale",
                            "stableBackground": "",
                            "toneNotes": "",
                            "avoidReasking": "",
                        }
                    )

        self.assertEqual(saved["contextVersion"], 1)
        self.assertTrue(current["initialized"])
        self.assertEqual(current["relationship"], "朋友")
        self.assertEqual(current["summaryThrough"], {"timestampMs": 2, "messageId": "m2"})
        self.assertEqual(conflict.exception.code, "context_version_conflict")
        self.assertEqual(conflict.exception.extra["currentVersion"], 1)

    def test_relationship_context_rejects_unimported_and_regressing_cursor(self):
        memory = self.root / "memory.sqlite3"
        con = self.context_line_db()

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        base = {
            "chat": "Context Test",
            "expectedContextVersion": 0,
            "relationship": "",
            "stableBackground": "",
            "toneNotes": "",
            "avoidReasking": "",
        }
        with mock.patch.object(bridge, "MEMORY_DB", memory):
            bridge.persist_memory(self.context_history())
            with mock.patch.object(bridge, "open_database", fake_open_database):
                with self.assertRaises(bridge.BridgeError) as missing:
                    bridge.save_relationship_context_operation(
                        {**base, "summaryThrough": {"timestampMs": 3, "messageId": "m3"}}
                    )
                bridge.save_relationship_context_operation(
                    {**base, "summaryThrough": {"timestampMs": 2, "messageId": "m2"}}
                )
                with self.assertRaises(bridge.BridgeError) as regression:
                    bridge.save_relationship_context_operation(
                        {
                            **base,
                            "expectedContextVersion": 1,
                            "summaryThrough": {"timestampMs": 1, "messageId": "m1"},
                        }
                    )

        self.assertEqual(missing.exception.code, "context_cursor_not_imported")
        self.assertEqual(regression.exception.code, "relationship_context_cursor_regression")

    def test_relationship_context_validates_bounded_fields_and_exact_name(self):
        for value in (None, 1, "", "x" * 513):
            with self.subTest(chat=value), self.assertRaises(bridge.BridgeError) as invalid_chat:
                bridge.get_relationship_context_operation({"chat": value})
            self.assertEqual(invalid_chat.exception.code, "invalid_chat")

        with self.assertRaises(bridge.BridgeError) as invalid:
            bridge.save_relationship_context_operation(
                {
                    "chat": "Context Test",
                    "expectedContextVersion": 0,
                    "summaryThrough": {"timestampMs": 1, "messageId": "m1"},
                    "relationship": "x" * (bridge.CONTEXT_TEXT_LIMIT + 1),
                    "stableBackground": "",
                    "toneNotes": "",
                    "avoidReasking": "",
                }
            )
        self.assertEqual(invalid.exception.code, "invalid_relationship_context")

        con = self.make_line_db()
        con.executemany("INSERT INTO _chat VALUES(?,?)", [("u1", 2), ("u2", 1)])
        con.executemany(
            "INSERT INTO _contact VALUES(?,?,?,?)",
            [("u1", None, "Duplicate", None), ("u2", None, "Duplicate", None)],
        )

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        with mock.patch.object(bridge, "open_database", fake_open_database):
            with self.assertRaises(bridge.BridgeError) as ambiguous:
                bridge.get_relationship_context_operation({"chat": "Duplicate"})
        self.assertEqual(ambiguous.exception.code, "ambiguous_chat_name")

    def test_line_closed_read_path_does_not_require_ui(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('u1',1000)")
        con.execute("INSERT INTO _contact VALUES('u1',NULL,'Test',NULL)")

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        with mock.patch.object(bridge, "open_database", fake_open_database), mock.patch.object(
            bridge, "line_running", return_value=False
        ):
            result = bridge.list_chats_operation({"limit": 20})
        self.assertTrue(result["ok"])
        self.assertEqual(result["chats"][0]["chatId"], "u1")
        self.assertTrue(result["databaseSnapshot"]["readOnly"])
        self.assertTrue(result["databaseSnapshot"]["queryOnly"])
        self.assertRegex(result["databaseSnapshot"]["refreshedAt"], r"\+00:00$")

    def test_each_history_read_reports_a_fresh_readonly_snapshot(self):
        con = self.make_line_db()
        con.execute("INSERT INTO _chat VALUES('u1',1000)")
        con.execute("INSERT INTO _contact VALUES('u1',NULL,'Fresh',NULL)")
        con.execute("INSERT INTO _message VALUES('u1',1000,'u1','latest',0,'m1',NULL)")

        @contextlib.contextmanager
        def fake_open_database():
            yield con, self.root / "source.edb", {}

        with mock.patch.object(bridge, "open_database", fake_open_database), mock.patch.object(
            bridge, "persist_memory", return_value={"inserted": 0, "deduplicated": 1}
        ), mock.patch.object(bridge, "remember_last_chat"):
            result = bridge.read_history_operation({"chat": "Fresh", "limit": 1})

        self.assertEqual(result["messages"][0]["text"], "latest")
        self.assertTrue(result["databaseSnapshot"]["readOnly"])
        self.assertTrue(result["databaseSnapshot"]["queryOnly"])
        self.assertRegex(result["databaseSnapshot"]["refreshedAt"], r"\+00:00$")

    @staticmethod
    def refresh_state(timestamp, message_id, count=1):
        return {
            "localMessageCount": count,
            "latestTimestampMs": timestamp,
            "latestMessageTime": bridge.iso_time(timestamp),
            "latestMessageId": message_id,
            "snapshotRefreshedAt": "2026-07-19T12:00:00+00:00",
        }

    def refresh_mocks(self, *, running=True, session=True):
        peekaboo = self.root / "peekaboo"
        peekaboo.write_bytes(b"binary")
        return (
            mock.patch.object(bridge, "_peekaboo_path", return_value=peekaboo),
            mock.patch.object(bridge, "line_running", return_value=running),
            mock.patch.object(bridge, "graphical_session_available", return_value=session),
            mock.patch.object(bridge, "focus_line_window"),
            mock.patch.object(bridge, "launch_line"),
        )

    def test_refresh_latest_passive_advance_does_not_focus_or_launch(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        after = self.refresh_state(101, "m2", 2)
        patches = self.refresh_mocks()
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, (("db", (1, 2, 3)),))
        ), mock.patch.object(
            bridge,
            "poll_target_advance",
            return_value=(True, after, 11, (("db", (2, 3, 4)),)),
        ):
            result = bridge.refresh_latest_operation({"chat": "Exact", "timeoutSeconds": 30})
        self.assertTrue(result["ok"])
        self.assertTrue(result["targetAdvanced"])
        self.assertFalse(result["freshnessVerified"])
        self.assertTrue(result["localSnapshotRefreshed"])
        self.assertIsNone(result["syncComplete"])
        self.assertIsNone(result["serverWatermark"])
        self.assertEqual(result["freshnessMode"], "target_advanced")
        self.assertEqual(result["strategy"], "passive")
        focus.assert_not_called()
        launch.assert_not_called()

    def test_refresh_latest_focuses_running_line_then_requires_target_advance(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        after = self.refresh_state(102, "m2", 2)
        patches = self.refresh_mocks(running=True)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, (("db", (1, 2, 3)),))
        ), mock.patch.object(
            bridge,
            "poll_target_advance",
            side_effect=[
                (False, before, 10, (("db", (1, 2, 3)),)),
                (True, after, 11, (("db", (2, 3, 4)),)),
            ],
        ):
            result = bridge.refresh_latest_operation({"chat": "Exact", "timeoutSeconds": 30})
        self.assertEqual(result["strategy"], "focus")
        self.assertTrue(result["targetAdvanced"])
        self.assertFalse(result["freshnessVerified"])
        self.assertIsNone(result["syncComplete"])
        focus.assert_called_once()
        launch.assert_not_called()

    def test_refresh_latest_launches_line_when_not_running(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        after = self.refresh_state(101, "m2", 2)
        patches = self.refresh_mocks(running=False)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, (("db", (1, 2, 3)),))
        ), mock.patch.object(
            bridge,
            "poll_target_advance",
            side_effect=[
                (False, before, 10, (("db", (1, 2, 3)),)),
                (True, after, 11, (("db", (2, 3, 4)),)),
            ],
        ):
            result = bridge.refresh_latest_operation({"chat": "Exact"})
        self.assertEqual(result["strategy"], "launch")
        self.assertFalse(result["freshnessVerified"])
        self.assertTrue(result["localSnapshotRefreshed"])
        launch.assert_called_once()
        focus.assert_not_called()

    def test_same_timestamp_with_greater_message_id_advances(self):
        before = self.refresh_state(100, "m1")
        after = self.refresh_state(100, "m2", 2)
        self.assertTrue(bridge.target_advanced(before, after))
        self.assertFalse(bridge.target_advanced(after, before))

    def test_activation_and_unrelated_messages_do_not_prove_sync_complete(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        patches = self.refresh_mocks(running=True)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, (("db", (1, 2, 3)),))
        ), mock.patch.object(
            bridge,
            "poll_target_advance",
            side_effect=[
                (False, before, 11, (("db", (2, 3, 4)),)),
                (False, before, 12, (("db", (3, 4, 5)),)),
            ],
        ):
            result = bridge.refresh_latest_operation({"chat": "Exact", "timeoutSeconds": 5})
        self.assertTrue(result["ok"])
        self.assertFalse(result["targetAdvanced"])
        self.assertFalse(result["freshnessVerified"])
        self.assertIsNone(result["syncComplete"])
        self.assertIsNone(result["serverWatermark"])
        self.assertEqual(result["freshnessMode"], "stable_after_line_activation")
        self.assertEqual(result["globalMessageCountAfter"], 12)
        self.assertEqual(result["safety"], bridge.refresh_safety())
        focus.assert_called_once()
        launch.assert_not_called()

    def test_locked_session_reads_local_snapshot_through_full_deadline_without_ui(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        patches = self.refresh_mocks(session=False)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, (("db", (1, 2, 3)),))
        ), mock.patch.object(
            bridge,
            "poll_target_advance",
            return_value=(False, before, 10, (("db", (1, 2, 3)),)),
        ) as poll, mock.patch.object(bridge.time, "monotonic", return_value=100.0):
            result = bridge.refresh_latest_operation({"chat": "Exact", "timeoutSeconds": 30})
        self.assertEqual(poll.call_count, 2)
        self.assertEqual(poll.call_args_list[0].args[3], 105.0)
        self.assertEqual(poll.call_args_list[1].args[3], 130.0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["localSnapshotRefreshed"])
        self.assertFalse(result["freshnessVerified"])
        self.assertFalse(result["targetAdvanced"])
        self.assertIsNone(result["syncComplete"])
        self.assertEqual(result["activationSkipped"], "locked_session")
        self.assertEqual(result["freshnessMode"], "local_snapshot")
        focus.assert_not_called()
        launch.assert_not_called()

    def test_locked_session_detects_target_update_after_initial_passive_window(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        after = self.refresh_state(102, "m2", 2)
        patches = self.refresh_mocks(session=False)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, ())
        ), mock.patch.object(bridge, "poll_target_advance", side_effect=[
            (False, before, 10, ()), (True, after, 11, ()),
        ]):
            result = bridge.refresh_latest_operation({"chat": "Exact"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["targetAdvanced"])
        self.assertEqual(result["after"]["latestMessageId"], "m2")
        self.assertFalse(result["freshnessVerified"])
        self.assertIsNone(result["syncComplete"])
        focus.assert_not_called()
        launch.assert_not_called()

    def test_locked_session_final_snapshot_failure_is_not_local_success(self):
        chat = {"chatId": "u1", "name": "Exact", "isGroup": False}
        before = self.refresh_state(100, "m1")
        patches = self.refresh_mocks(session=False)
        with patches[0], patches[1], patches[2], patches[3] as focus, patches[4] as launch, mock.patch.object(
            bridge, "read_refresh_snapshot", return_value=(chat, before, 10, ())
        ), mock.patch.object(bridge, "poll_target_advance", side_effect=[
            (False, before, 10, ()), bridge.BridgeError("snapshot_unstable", "test failure"),
        ]):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge.refresh_latest_operation({"chat": "Exact"})
        self.assertEqual(caught.exception.code, "snapshot_unstable")
        focus.assert_not_called()
        launch.assert_not_called()

    def test_refresh_timeout_validation_rejects_bool_and_out_of_range(self):
        for value in (True, 4, 61, 5.5):
            with self.subTest(value=value), self.assertRaises(bridge.BridgeError) as caught:
                bridge.refresh_latest_operation({"chat": "Exact", "timeoutSeconds": value})
            self.assertEqual(caught.exception.code, "invalid_timeout")

    def test_refresh_peekaboo_commands_are_fixed_and_have_no_input_path(self):
        peekaboo = self.root / "peekaboo"
        peekaboo.write_bytes(b"binary")
        with mock.patch.object(bridge, "_peekaboo_path", return_value=peekaboo), mock.patch.object(
            bridge, "run_fixed_peekaboo_action", return_value={"success": True}
        ) as run:
            bridge.focus_line_window(8)
            focus_command = run.call_args.args[0]
            bridge.launch_line(8)
            launch_command = run.call_args.args[0]

        self.assertEqual(focus_command[1:5], ["app", "switch", "--to", "LINE"])
        self.assertIn("--verify", focus_command)
        self.assertNotIn("--cycle", focus_command)
        self.assertEqual(
            launch_command[1:6],
            ["app", "launch", "--bundle-id", bridge.LINE_BUNDLE_ID, "--wait-until-ready"],
        )
        forbidden = {"type", "paste", "press", "hotkey", "composer", "send", "return", "enter"}
        for command in (focus_command, launch_command):
            self.assertTrue(forbidden.isdisjoint({token.lower() for token in command}))

    def test_run_json_returns_only_redacted_peekaboo_failure_details(self):
        failed = subprocess.CompletedProcess(
            args=["peekaboo", "see"],
            returncode=1,
            stdout=json.dumps({"success": False, "error": {"code": "WINDOW_NOT_FOUND", "message": "private"}}),
            stderr="private stderr",
        )
        with mock.patch.object(bridge.subprocess, "run", return_value=failed), self.assertRaises(
            bridge.BridgeError
        ) as caught:
            bridge._run_json(["peekaboo", "see"], stage="inspect_line")
        self.assertEqual(caught.exception.code, "peekaboo_failed")
        self.assertEqual(
            caught.exception.extra,
            {
                "stage": "inspect_line",
                "peekabooCode": "WINDOW_NOT_FOUND",
                "failedCommand": None,
                "failureReason": "unclassified",
            },
        )
        self.assertNotIn("private", json.dumps(caught.exception.payload()))

    def test_safe_search_field_accepts_current_and_legacy_peekaboo_roles(self):
        base = {"id": "field", "bounds": {"x": 20, "y": 30, "width": 200, "height": 24}}
        self.assertTrue(bridge.is_safe_search_field_candidate({**base, "role": "textField"}))
        self.assertTrue(bridge.is_safe_search_field_candidate({**base, "role": "AXTextField"}))
        self.assertTrue(bridge.is_safe_search_field_candidate({**base, "role": "searchField"}))
        self.assertFalse(bridge.is_safe_search_field_candidate({**base, "role": "button"}))
        self.assertFalse(
            bridge.is_safe_search_field_candidate(
                {**base, "role": "textField", "bounds": {"x": 900, "y": 30, "width": 200, "height": 24}}
            )
        )

    def test_exact_chat_static_text_resolves_to_smallest_actionable_container(self):
        elements = [
            {
                "id": "label",
                "label": "Exact",
                "is_actionable": False,
                "bounds": {"x": 30, "y": 40, "width": 80, "height": 20},
            },
            {
                "id": "row",
                "role": "group",
                "is_actionable": True,
                "bounds": {"x": 20, "y": 30, "width": 200, "height": 50},
            },
            {
                "id": "list",
                "role": "group",
                "is_actionable": True,
                "bounds": {"x": 0, "y": 0, "width": 500, "height": 500},
            },
        ]
        target = bridge.resolve_exact_chat_click_target(elements, "Exact")
        self.assertEqual(target["id"], "row")

    def test_history_backfill_does_not_reuse_snapshots_or_click_results(self):
        before = {"localMessageCount": 2, "oldestLocalMessage": "2026-08-15", "newestLocalMessage": "2026-09-01"}
        after = {**before, "localMessageCount": 3, "newestLocalMessage": "2026-10-11"}
        db_context = contextlib.nullcontext((object(), None, {}))
        with mock.patch.object(bridge, "open_database", return_value=db_context), mock.patch.object(
            bridge, "resolve_chat", return_value={"chatId": "chat-a", "name": "Exact"}
        ), mock.patch.object(bridge, "coverage", side_effect=[before, after]), mock.patch.object(
            bridge, "line_running", return_value=True
        ), mock.patch.object(bridge, "graphical_session_available", return_value=True), mock.patch.object(
            bridge, "run_history_helper", return_value={"historyPaneVerified": True}
        ) as load, mock.patch.object(bridge, "_run_json", side_effect=AssertionError("No snapshot UI")), mock.patch.object(
            bridge, "focus_line_window", side_effect=AssertionError("No activation")
        ):
            result = bridge.sync_older_operation({"chat": "chat-a", "rounds": 1})
        load.assert_called_once_with("Exact", 1)
        self.assertFalse(result["olderHistoryAdvanced"])
        self.assertEqual(result["newMessages"], 1)
        self.assertIsNone(result["syncComplete"])

    def test_history_backfill_rejects_locked_session_before_ui(self):
        with mock.patch.object(bridge, "open_database", return_value=contextlib.nullcontext((object(), None, {}))), mock.patch.object(
            bridge, "resolve_chat", return_value={"chatId": "chat-a", "name": "Exact"}
        ), mock.patch.object(bridge, "coverage", return_value={}), mock.patch.object(
            bridge, "line_running", return_value=True
        ), mock.patch.object(bridge, "graphical_session_available", return_value=False), mock.patch.object(
            bridge, "run_history_helper"
        ) as load, self.assertRaises(bridge.BridgeError) as caught:
            bridge.sync_older_operation({"chat": "chat-a", "rounds": 1})
        self.assertEqual(caught.exception.code, "history_ui_session_unavailable")
        load.assert_not_called()

    def test_peekaboo_timeout_is_explicit_and_does_not_fallback(self):
        with mock.patch.object(bridge.subprocess, "run", side_effect=subprocess.TimeoutExpired(["peekaboo"], 5)):
            with self.assertRaises(bridge.BridgeError) as caught:
                bridge._run_json(["peekaboo", "see"], timeout=5)
        self.assertEqual(caught.exception.code, "peekaboo_timeout")
        self.assertIn("no fallback input", caught.exception.detail)

    def test_native_history_adapter_bounds_timeout_and_redacts_output(self):
        helper = self.root / "line-history-accessibility"
        helper.write_bytes(b"binary")
        with mock.patch.object(bridge, "history_helper_path", return_value=helper), mock.patch.object(
            bridge.subprocess, "run", side_effect=subprocess.TimeoutExpired([str(helper)], 20)
        ) as run, self.assertRaises(bridge.BridgeError) as caught:
            bridge.run_history_helper("Exact", 1)
        self.assertEqual(caught.exception.code, "history_ui_timeout")
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertEqual(run.call_args.args[0], [str(helper), "load", "Exact", "1"])

    def test_native_history_adapter_rejects_unverified_chat_and_rounds(self):
        helper = self.root / "line-history-accessibility"
        helper.write_bytes(b"binary")
        result = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "openedExactResult": True,
                  "completedRounds": 1, "historyPaneVerified": False, "private": "not exposed"}), "private stderr")
        with mock.patch.object(bridge, "history_helper_path", return_value=helper), mock.patch.object(
            bridge.subprocess, "run", return_value=result
        ), self.assertRaises(bridge.BridgeError) as caught:
            bridge.run_history_helper("Exact", 1)
        self.assertEqual(caught.exception.code, "history_ui_unverified")
        self.assertNotIn("private", json.dumps(caught.exception.payload()))

    def test_native_history_identity_failure_reports_only_boolean_checks(self):
        helper = self.root / "line-history-accessibility"
        helper.write_bytes(b"binary")
        result = subprocess.CompletedProcess([], 1, json.dumps({
            "ok": False, "error": "opened_chat_identity_unverified",
            "windowTitleMatches": False, "lineFrontmost": True,
            "windowTitle": "private title", "private": "private content",
        }), "private stderr")
        with mock.patch.object(bridge, "history_helper_path", return_value=helper), mock.patch.object(
            bridge.subprocess, "run", return_value=result
        ), self.assertRaises(bridge.BridgeError) as caught:
            bridge.run_history_helper("Exact", 1)
        payload = caught.exception.payload()
        self.assertEqual(payload["identityChecks"], {
            "windowTitleMatches": False, "lineFrontmost": True,
        })
        self.assertNotIn("private", json.dumps(payload))

    def test_native_history_identity_checks_reject_non_boolean_values(self):
        helper = self.root / "line-history-accessibility"
        helper.write_bytes(b"binary")
        result = subprocess.CompletedProcess([], 1, json.dumps({
            "ok": False, "error": "opened_chat_identity_unverified",
            "windowTitleMatches": "private title", "lineFrontmost": 1,
        }), "")
        with mock.patch.object(bridge, "history_helper_path", return_value=helper), mock.patch.object(
            bridge.subprocess, "run", return_value=result
        ), self.assertRaises(bridge.BridgeError) as caught:
            bridge.run_history_helper("Exact", 1)
        self.assertNotIn("identityChecks", caught.exception.payload())


if __name__ == "__main__":
    unittest.main()
