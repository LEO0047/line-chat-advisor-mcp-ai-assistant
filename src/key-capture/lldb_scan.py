"""LLDB command that writes possible 32-hex LINE DB keys to a mode-0600 file.

The command never prints candidate values. The companion validator must delete
the output file after testing the candidates against a read-only DB snapshot.
"""

import json
import os
import re

import lldb


ASCII_HEX = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
UTF16_HEX = re.compile(rb"(?:(?:[0-9A-Fa-f]\x00){32})")
CHUNK_SIZE = 4 * 1024 * 1024
OVERLAP = 4096
MAX_CANDIDATES = 50_000


def _candidate_values(data: bytes):
    for match in ASCII_HEX.finditer(data):
        yield match.group(0).decode("ascii").lower()
    for match in UTF16_HEX.finditer(data):
        yield match.group(0)[::2].decode("ascii").lower()


def line_scan(debugger, _command, result, _internal_dict):
    output_path = os.environ.get("LINE_KEY_CANDIDATES_PATH")
    status_path = os.environ.get("LINE_KEY_SCAN_STATUS_PATH")
    if not output_path or not status_path:
        result.SetError("Required output paths are unavailable.")
        return

    process = debugger.GetSelectedTarget().GetProcess()
    if not process.IsValid():
        result.SetError("No valid process is attached.")
        return

    candidates = set()
    regions_scanned = 0
    bytes_scanned = 0
    address = 0
    max_address = (1 << 64) - 1
    while address < max_address and len(candidates) < MAX_CANDIDATES:
        region = lldb.SBMemoryRegionInfo()
        error = process.GetMemoryRegionInfo(address, region)
        if not error.Success():
            break
        start = region.GetRegionBase()
        end = region.GetRegionEnd()
        if end <= address:
            break
        if region.IsReadable() and region.IsWritable():
            regions_scanned += 1
            cursor = start
            tail = b""
            while cursor < end and len(candidates) < MAX_CANDIDATES:
                amount = min(CHUNK_SIZE, end - cursor)
                read_error = lldb.SBError()
                block = process.ReadMemory(cursor, amount, read_error)
                if read_error.Success() and block:
                    combined = tail + block
                    candidates.update(_candidate_values(combined))
                    bytes_scanned += len(block)
                    tail = combined[-OVERLAP:]
                else:
                    tail = b""
                cursor += amount
        address = end

    old_umask = os.umask(0o077)
    try:
        with open(output_path, "w", encoding="ascii") as handle:
            for candidate in sorted(candidates):
                handle.write(candidate + "\n")
        with open(status_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "ok": True,
                    "candidateCount": len(candidates),
                    "regionsScanned": regions_scanned,
                    "bytesScanned": bytes_scanned,
                    "candidateLimitReached": len(candidates) >= MAX_CANDIDATES,
                },
                handle,
            )
    finally:
        os.umask(old_umask)
    result.AppendMessage("LINE key candidate scan completed without printing candidate material.")


def __lldb_init_module(debugger, _internal_dict):
    debugger.HandleCommand("command script add -f lldb_scan.line_scan line_scan")
