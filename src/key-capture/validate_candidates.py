#!/usr/bin/env python3
"""Validate LLDB candidates locally and store only the working key in Keychain."""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "src" / "line-readonly-mcp" / "bridge.py"
KEYCHAIN_HELPER = ROOT / "runtime" / "bin" / "keychain-store"
KEYCHAIN_SERVICE = "line-cua-mcp-dbkey"

SPEC = importlib.util.spec_from_file_location("line_readonly_bridge_for_capture", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


def candidate_lines(path: Path):
    for line in path.read_text(encoding="ascii").splitlines():
        value = line.strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", value):
            yield value


def decrypts(apsw, snapshot: Path, candidate: str) -> bool:
    connection = None
    try:
        connection = apsw.Connection(str(snapshot), flags=apsw.SQLITE_OPEN_READONLY)
        connection.pragma("cipher", "aes128cbc")
        connection.pragma("kdf_iter", 1)
        connection.pragma("key", candidate)
        connection.pragma("query_only", True)
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return True
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage"}))
        return 2
    candidate_path = Path(sys.argv[1]).resolve()
    capture_root = (ROOT / "runtime" / "key-capture").resolve()
    if candidate_path.parent != capture_root or candidate_path.name != "candidates.txt":
        print(json.dumps({"ok": False, "error": "unsafe_candidate_path"}))
        return 2
    try:
        import apsw

        candidates = list(candidate_lines(candidate_path))
        valid = None
        db = bridge.detect_db()
        with bridge.stable_snapshot(db) as (snapshot, _metadata):
            for candidate in candidates:
                if decrypts(apsw, snapshot, candidate):
                    valid = candidate
                    break
        if valid is None:
            print(json.dumps({"ok": False, "error": "no_valid_candidate", "candidateCount": len(candidates)}))
            return 1
        if not KEYCHAIN_HELPER.is_file():
            print(json.dumps({"ok": False, "error": "keychain_helper_missing"}))
            return 1
        stored = subprocess.run(
            [str(KEYCHAIN_HELPER), KEYCHAIN_SERVICE, pwd.getpwuid(os.getuid()).pw_name],
            input=valid,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if stored.returncode != 0:
            print(json.dumps({"ok": False, "error": "keychain_store_failed"}))
            return 1
        if bridge.keychain_key().lower() != valid:
            print(json.dumps({"ok": False, "error": "keychain_verify_failed"}))
            return 1
        print(json.dumps({"ok": True, "candidateCount": len(candidates), "keychainStored": True, "decryptOk": True}))
        return 0
    finally:
        try:
            candidate_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
