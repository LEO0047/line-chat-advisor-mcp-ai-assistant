import base64
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock
from PIL import Image

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("attachment_bridge", PROJECT / "src/line-readonly-mcp/bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def encrypt_fixture(plain):
    key = bytes(range(32))
    derived = HKDF(algorithm=hashes.SHA256(), length=76, salt=None, info=b"FileEncryption").derive(key)
    encryptor = Cipher(algorithms.AES(derived[:32]), modes.CTR(derived[64:] + bytes(4))).encryptor()
    cipher = encryptor.update(plain) + encryptor.finalize()
    return cipher + hmac.digest(derived[32:64], cipher, "sha256"), base64.b64encode(key).decode()


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_verified_decryption_and_tamper_rejection(self):
        plain = b"%PDF-1.7\nfixture only\n%%EOF"
        encrypted, key = encrypt_fixture(plain)
        self.assertEqual(bridge.decrypt_attachment_bytes(encrypted, key), plain)
        for offset in (0, len(encrypted) - 1):
            altered = bytearray(encrypted)
            altered[offset] ^= 1
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.decrypt_attachment_bytes(bytes(altered), key)
            self.assertEqual(error.exception.code, "attachment_integrity_failed")

    def test_destination_cannot_escape_downloads_via_symlink(self):
        with mock.patch.object(bridge.Path, "home", return_value=self.root):
            downloads = self.root / "Downloads"
            downloads.mkdir()
            outside = self.root / "outside"
            outside.mkdir()
            (downloads / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(bridge.BridgeError):
                bridge.attachment_destination(str(downloads / "escape"))
            with self.assertRaises(bridge.BridgeError):
                bridge.attachment_destination("relative")

    def test_save_is_deduplicated_and_does_not_overwrite_or_follow_symlinks(self):
        first = bridge.save_attachment_bytes(self.root, "a.pdf", "m1", b"first")
        duplicate = bridge.save_attachment_bytes(self.root, "a.pdf", "m1", b"first")
        self.assertTrue(duplicate["alreadyPresent"])
        self.assertEqual(first["path"], duplicate["path"])
        second = bridge.save_attachment_bytes(self.root, "a.pdf", "m2", b"second")
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).read_bytes(), b"first")
        (self.root / "link.pdf").symlink_to(self.root / "a.pdf")
        linked = bridge.save_attachment_bytes(self.root, "link.pdf", "m3", b"third")
        self.assertNotEqual(Path(linked["path"]), self.root / "link.pdf")
        traversal = bridge.save_attachment_bytes(self.root, "../bad.pdf", "m4", b"fourth")
        self.assertEqual(Path(traversal["path"]).parent, self.root)
        self.assertFalse(list(self.root.glob(".line-download-*")))

    def test_missing_keychain_token_is_redacted(self):
        with mock.patch.object(bridge.subprocess, "run", side_effect=subprocess.CalledProcessError(44, "security", stderr="secret")):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.keychain_obs_token()
            self.assertEqual(error.exception.code, "attachment_auth_required")
            self.assertNotIn("secret", json.dumps(error.exception.payload()))

    def test_desktop_credential_is_reread_and_does_not_provision_keychain(self):
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.execute("CREATE TABLE _setting (_key TEXT PRIMARY KEY, _value TEXT)")
        c.execute("INSERT INTO _setting VALUES ('setting',?)", (json.dumps({"obsTokenGeneral": {"token": "first-secret", "timestamp": 1}}),))
        with mock.patch.object(bridge, "keychain_obs_token") as fallback:
            self.assertEqual(bridge.attachment_obs_token(c), ("first-secret", "desktop_snapshot"))
            # Desktop owns renewal. A subsequent request uses its new snapshot
            # value rather than an in-process cache or a copied Keychain item.
            c.execute("UPDATE _setting SET _value=?", (json.dumps({"obsTokenGeneral": {"token": "renewed-secret"}}),))
            self.assertEqual(bridge.attachment_obs_token(c), ("renewed-secret", "desktop_snapshot"))
            fallback.assert_not_called()

    def test_only_missing_desktop_credentials_allow_keychain_fallback(self):
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        with mock.patch.object(bridge, "keychain_obs_token", return_value="provisioned-secret") as fallback:
            self.assertEqual(bridge.attachment_obs_token(c), ("provisioned-secret", "keychain"))
            c.execute("CREATE TABLE _setting (_key TEXT PRIMARY KEY, _value TEXT)")
            for settings in ({}, {"obsTokenGeneral": {}}, {"obsTokenGeneral": {"token": ""}}):
                c.execute("INSERT OR REPLACE INTO _setting VALUES ('setting',?)", (json.dumps(settings),))
                self.assertEqual(bridge.attachment_obs_token(c)[1], "keychain")
            fallback.reset_mock()
            for raw in ('not-json-secret', '[]', '{"obsTokenGeneral":"secret"}',
                        json.dumps({"obsTokenGeneral": {"token": "secret\r\ninjected"}}),
                        json.dumps({"obsTokenGeneral": {"token": 42}})):
                c.execute("UPDATE _setting SET _value=?", (raw,))
                with self.assertRaises(bridge.BridgeError) as error:
                    bridge.attachment_obs_token(c)
                self.assertEqual(error.exception.code, "attachment_auth_invalid")
                self.assertNotIn("secret", json.dumps(error.exception.payload()))
            fallback.assert_not_called()

    def test_plain_and_encrypted_requests_include_message_authorization(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        response.read.return_value = b"downloaded"
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(bridge.urllib.request, "build_opener", return_value=opener):
            for sid in ("m", "emf", "emi"):
                self.assertEqual(bridge.fetch_attachment_bytes("object-id", sid, "secret", "message-1"), b"downloaded")
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.get_header("X-line-access"), "secret")
                metadata = json.loads(base64.b64decode(request.get_header("X-talk-meta")))
                thrift = base64.b64decode(metadata["message"])
                self.assertEqual(thrift, b"\x0b\x00\x04\x00\x00\x00\x09message-1\x0f\x00\x1b\x0c\x00\x00\x00\x00\x00")
                self.assertNotIn("secret", request.full_url)

    def test_invalid_credential_never_reaches_network(self):
        with mock.patch.object(bridge.urllib.request, "build_opener") as network:
            for token in (None, "", "bad\r\nsecret", "a" * 16385):
                with self.assertRaises(bridge.BridgeError):
                    bridge.fetch_attachment_bytes("object-id", "emf", token, "m1")
            network.assert_not_called()

    def test_image_validation_checks_complete_data_and_pixel_budget(self):
        for kind, extension in (("PNG", "png"), ("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp")):
            out = io.BytesIO()
            Image.new("RGB", (3, 2), color="blue").save(out, format=kind)
            data = out.getvalue()
            info = bridge.validate_image_bytes(data)
            self.assertEqual(info, {"format": kind, "extension": extension, "width": 3, "height": 2, "frames": 1})
            with mock.patch.object(bridge, "MAX_IMAGE_PIXELS", 5):
                with self.assertRaises(bridge.BridgeError) as error:
                    bridge.validate_image_bytes(data)
                self.assertEqual(error.exception.code, "attachment_image_too_large")
        for invalid in (b"<html>not an image</html>", b"\x89PNG\r\n\x1a\n", data[:len(data)//2]):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.validate_image_bytes(invalid)
            self.assertEqual(error.exception.code, "attachment_format_mismatch")

    def test_image_download_preserves_full_bytes_and_verifies_before_saving(self):
        out = io.BytesIO()
        Image.new("RGB", (3, 2), color="blue").save(out, format="PNG")
        plain = out.getvalue()
        encrypted, material = encrypt_fixture(plain)
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.execute("CREATE TABLE _message (_chatId TEXT,_id TEXT,_createdTime INTEGER,_text TEXT,_contentType INTEGER,_contentMetadata TEXT,_contentInfo TEXT)")
        c.execute("INSERT INTO _message VALUES (?,?,?,?,?,?,?)", ("chat-a", "m1", 1000, "", 1,
                  json.dumps({"OID": "object-id", "SID": "emi", "FILE_SIZE": str(len(plain))}),
                  json.dumps({"keyMaterial": material})))
        @contextlib.contextmanager
        def database():
            yield c, None, None
        with mock.patch.object(bridge, "open_database", database), \
             mock.patch.object(bridge, "resolve_chat", return_value={"chatId": "chat-a"}), \
             mock.patch.object(bridge, "keychain_obs_token", return_value="test-token"), \
             mock.patch.object(bridge, "fetch_attachment_bytes", return_value=encrypted) as fetch, \
             mock.patch.object(bridge.Path, "home", return_value=self.root):
            destination = self.root / "Downloads" / "images"
            args = {"chat": "chat-a", "messageId": "m1", "destinationDir": str(destination)}
            result = bridge.download_attachment_operation(args)
            fetch.assert_called_once_with("object-id", "emi", "test-token", "m1")
            self.assertTrue(result["integrityVerified"])
            self.assertEqual(result["image"]["width"], 3)
            self.assertEqual(Path(result["path"]).suffix, ".png")
            self.assertEqual(Path(result["path"]).read_bytes(), plain)
            duplicate = bridge.download_attachment_operation(args)
            self.assertTrue(duplicate["alreadyPresent"])
            # Plain image route uses the message ID, not an external URL.
            c.execute("UPDATE _message SET _contentMetadata=?,_contentInfo='{}'", (json.dumps({"FILE_SIZE": str(len(plain))}),))
            fetch.return_value = plain
            self.assertFalse(bridge.download_attachment_operation(args)["integrityVerified"])
            fetch.assert_called_with("m1", "m", "test-token", "m1")
            args["destinationDir"] = str(self.root / "Downloads" / "bad-images")
            fetch.return_value = b"not an image".ljust(len(plain), b" ")
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.download_attachment_operation(args)
            self.assertEqual(error.exception.code, "attachment_format_mismatch")
            self.assertFalse(Path(args["destinationDir"]).exists())
            fetch.reset_mock()
            c.execute("UPDATE _message SET _contentMetadata=?", (json.dumps({"FILE_SIZE": str(len(plain)), "DOWNLOAD_URL": "https://outside.invalid/image"}),))
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.download_attachment_operation(args)
            self.assertEqual(error.exception.code, "attachment_metadata_unsupported")
            fetch.assert_not_called()

    def test_http_auth_failure_does_not_expose_token_or_url(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://secret", 401, "secret", {}, None)
        with mock.patch.object(bridge.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.fetch_attachment_bytes("object-id", "emf", "secret", "m1")
            self.assertEqual(error.exception.code, "attachment_auth_rejected")
            self.assertNotIn("secret", json.dumps(error.exception.payload()))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://obs.line-apps.com/r/talk/emf/object-id")
        self.assertIsNone(bridge._NoAttachmentRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_download_operation_saves_only_verified_bytes(self):
        plain = b"%PDF-1.7\nfixture only\n%%EOF"
        encrypted, material = encrypt_fixture(plain)
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.execute("CREATE TABLE _setting (_key TEXT PRIMARY KEY, _value TEXT)")
        c.execute("INSERT INTO _setting VALUES ('setting',?)", (json.dumps({"obsTokenGeneral": {"token": "desktop-test-token"}}),))
        c.execute("CREATE TABLE _message (_chatId TEXT,_id TEXT,_createdTime INTEGER,_text TEXT,_contentType INTEGER,_contentMetadata TEXT,_contentInfo TEXT)")
        c.execute("INSERT INTO _message VALUES (?,?,?,?,?,?,?)", ("chat-a", "m1", 1000, "example.pdf", 14,
                  json.dumps({"OID": "object-id", "FILE_SIZE": str(len(plain))}),
                  json.dumps({"keyMaterial": material, "fileName": "example.pdf"})))
        @contextlib.contextmanager
        def database():
            yield c, None, None
        with mock.patch.object(bridge, "open_database", database), \
             mock.patch.object(bridge, "resolve_chat", return_value={"chatId": "chat-a"}), \
             mock.patch.object(bridge, "keychain_obs_token", side_effect=AssertionError("unexpected Keychain fallback")), \
             mock.patch.object(bridge, "fetch_attachment_bytes", return_value=encrypted) as fetch, \
             mock.patch.object(bridge.Path, "home", return_value=self.root):
            destination = self.root / "Downloads" / "files"
            args = {"chat": "chat-a", "messageId": "m1", "destinationDir": str(destination)}
            result = bridge.download_attachment_operation(args)
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["integrityVerified"])
            self.assertEqual(result["authSource"], "desktop_snapshot")
            fetch.assert_called_once_with("object-id", "emf", "desktop-test-token", "m1")
            self.assertEqual(Path(result["path"]).read_bytes(), plain)
            self.assertEqual(result["sha256"], hashlib.sha256(plain).hexdigest())
            self.assertNotIn(material, json.dumps(result))
            self.assertNotIn("desktop-test-token", json.dumps(result))
            args["destinationDir"] = str(self.root / "Downloads" / "bad")
            fetch.return_value = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
            with self.assertRaises(bridge.BridgeError):
                bridge.download_attachment_operation(args)
            self.assertFalse(Path(args["destinationDir"]).exists())
            fetch.return_value, _ = encrypt_fixture(b"not a pdf".ljust(len(plain), b" "))
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.download_attachment_operation(args)
            self.assertEqual(error.exception.code, "attachment_format_mismatch")
            fetch.return_value, _ = encrypt_fixture(plain + b"extra")
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.download_attachment_operation(args)
            self.assertEqual(error.exception.code, "attachment_size_mismatch")


if __name__ == "__main__":
    unittest.main()
