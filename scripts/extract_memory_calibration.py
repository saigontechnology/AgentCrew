"""Explicit offline operator; private execution needs separate method/boundary clearance.

All source bytes are read twice, including heldout bytes. Only structural JSON
syntax, duplicate-key digests and the top-level split discriminator are used.
No question/label payload objects are constructed. This is NOT a human review tool.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import stat
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.migrate_chroma_memory_to_sqlite import (
    MigrationError,
    _ignored,
    _overlap,
    _path,
    _private_directory,
    _write_json,
)
from verification.memory.benchmark import BenchmarkError, check, file_hash

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 1024 * 1024
MAX_ROWS = 100_000
MAX_DEPTH = 64


def row_split(data: bytes) -> str:
    """Validate one bounded UTF-8 JSON object without building payload objects.

    Key digests use canonical Unicode scalar encodings solely for structural
    duplicate detection. Non-discriminator value strings are validated/skipped,
    never decoded into complete Python strings, lists, dictionaries or numbers.
    All cursor/frame state is request-local; no source payload enters errors.
    """
    check(0 < len(data) <= MAX_ROW_BYTES, "extract_row_size")
    offset = 0
    role = None

    def peek() -> int:
        return data[offset] if offset < len(data) else -1

    def whitespace() -> None:
        nonlocal offset
        while peek() in (9, 10, 13, 32):
            offset += 1

    def take(token: int) -> None:
        nonlocal offset
        check(peek() == token, "extract_invalid_json")
        offset += 1

    def hex4() -> int:
        nonlocal offset
        check(offset + 4 <= len(data), "extract_invalid_unicode")
        value = 0
        for byte in data[offset : offset + 4]:
            check(byte in b"0123456789abcdefABCDEF", "extract_invalid_unicode")
            value = value * 16 + (byte - 48 if byte <= 57 else (byte | 32) - 87)
        offset += 4
        return value

    def string(mode: str = "skip") -> tuple[bytes | None, str | None]:
        nonlocal offset
        take(34)
        evidence = hashlib.sha256() if mode == "key" else None
        matches = {"key": ("split",), "split": ("calibration", "holdout")}.get(mode, ())
        length = 0
        while peek() != 34:
            byte = peek()
            check(byte >= 32, "extract_invalid_string")
            offset += 1
            if byte == 92:
                escape = peek()
                offset += 1
                if escape == 117:
                    scalar = hex4()
                    if 0xD800 <= scalar <= 0xDBFF:
                        take(92)
                        take(117)
                        low = hex4()
                        check(0xDC00 <= low <= 0xDFFF, "extract_invalid_unicode")
                        scalar = 0x10000 + ((scalar - 0xD800) << 10) + low - 0xDC00
                    else:
                        check(not 0xDC00 <= scalar <= 0xDFFF, "extract_invalid_unicode")
                else:
                    escapes = {
                        34: 34,
                        92: 92,
                        47: 47,
                        98: 8,
                        102: 12,
                        110: 10,
                        114: 13,
                        116: 9,
                    }
                    check(escape in escapes, "extract_invalid_escape")
                    scalar = escapes[escape]
            elif byte < 128:
                scalar = byte
            else:
                if 0xC2 <= byte <= 0xDF:
                    count, scalar, minimum = 1, byte & 31, 0x80
                elif 0xE0 <= byte <= 0xEF:
                    count, scalar, minimum = 2, byte & 15, 0x800
                elif 0xF0 <= byte <= 0xF4:
                    count, scalar, minimum = 3, byte & 7, 0x10000
                else:
                    raise BenchmarkError("extract_invalid_utf8")
                for _ in range(count):
                    continuation = peek()
                    check(0x80 <= continuation <= 0xBF, "extract_invalid_utf8")
                    scalar = (scalar << 6) | (continuation & 63)
                    offset += 1
                check(
                    minimum <= scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF,
                    "extract_invalid_utf8",
                )
            if evidence is not None:
                evidence.update(chr(scalar).encode("utf-8"))
            matches = tuple(
                word
                for word in matches
                if length < len(word) and ord(word[length]) == scalar
            )
            length += 1
        take(34)
        matched = next((word for word in matches if len(word) == length), None)
        return (evidence.digest() if evidence is not None else None), matched

    def number() -> None:
        nonlocal offset
        if peek() == 45:
            offset += 1
        if peek() == 48:
            offset += 1
        else:
            check(49 <= peek() <= 57, "extract_invalid_number")
            while 48 <= peek() <= 57:
                offset += 1
        if peek() == 46:
            offset += 1
            check(48 <= peek() <= 57, "extract_invalid_number")
            while 48 <= peek() <= 57:
                offset += 1
        if peek() in (69, 101):
            offset += 1
            if peek() in (43, 45):
                offset += 1
            check(48 <= peek() <= 57, "extract_invalid_number")
            while 48 <= peek() <= 57:
                offset += 1

    def value(depth: int) -> None:
        nonlocal offset, role
        check(depth <= MAX_DEPTH, "extract_json_depth")
        whitespace()
        token = peek()
        if token == 123:
            offset += 1
            whitespace()
            seen = set()
            if peek() != 125:
                while True:
                    key, discriminator = string("key")
                    check(key not in seen, "extract_duplicate_key")
                    seen.add(key)
                    whitespace()
                    take(58)
                    whitespace()
                    if depth == 0 and discriminator == "split":
                        check(peek() == 34, "extract_split_string_required")
                        _, role = string("split")
                        check(role is not None, "extract_unknown_split")
                    else:
                        value(depth + 1)
                    whitespace()
                    if peek() != 44:
                        break
                    offset += 1
                    whitespace()
            take(125)
        elif token == 91:
            offset += 1
            whitespace()
            if peek() != 93:
                while True:
                    value(depth + 1)
                    whitespace()
                    if peek() != 44:
                        break
                    offset += 1
            take(93)
        elif token == 34:
            string()
        elif token == 45 or 48 <= token <= 57:
            number()
        else:
            literal = {116: b"true", 102: b"false", 110: b"null"}.get(token)
            check(
                literal is not None and data[offset : offset + len(literal)] == literal,
                "extract_invalid_json",
            )
            offset += len(literal)

    whitespace()
    check(peek() == 123, "extract_root_object_required")
    value(0)
    whitespace()
    check(offset == len(data), "extract_trailing_data")
    check(role is not None, "extract_split_missing")
    return role


def _open_source(path: Path) -> BinaryIO:
    """Pin every absolute path component without following any symlink."""
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
    finally:
        os.close(directory)
    stream = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise BenchmarkError("extract_source_not_regular")
    return stream


def _identity(stream: BinaryIO) -> tuple:
    info = os.fstat(stream.fileno())
    check(0 < info.st_size <= MAX_SOURCE_BYTES, "extract_source_size")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _unchanged(stream: BinaryIO, path: Path, identity: tuple) -> None:
    check(_identity(stream) == identity, "extract_source_changed")
    with _open_source(path) as current:
        check(_identity(current) == identity, "extract_source_path_changed")


def _verified_source(stream: BinaryIO, expected: str) -> tuple:
    identity = _identity(stream)
    evidence = hashlib.sha256()
    total = 0
    while block := stream.read(65536):
        total += len(block)
        check(total <= MAX_SOURCE_BYTES, "extract_source_size")
        evidence.update(block)
    check(
        total == identity[2] and evidence.hexdigest() == expected,
        "extract_source_identity_mismatch",
    )
    check(_identity(stream) == identity, "extract_source_changed")
    stream.seek(0)
    return identity


def _publish(stage: Path, output: Path) -> None:
    """Linux atomic no-replace directory publication; no unsafe fallback."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    check(rename is not None, "extract_atomic_publish_unavailable")
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    check(
        rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1) == 0,
        "extract_atomic_publish_failed",
    )


def extract_calibration(
    source: Path, output: Path, expected_source_sha256: str
) -> dict:
    """Publish only a mechanically selected export plus evidence, never review.

    Requires a separately authorized immutable source and an operator-owned
    output parent. Reads combined/heldout bytes twice; does NOT claim zero access.
    """
    check(
        isinstance(expected_source_sha256, str)
        and len(expected_source_sha256) == 64
        and all(c in "0123456789abcdef" for c in expected_source_sha256),
        "extract_expected_digest_required",
    )
    source, output = _path(source), _path(output)
    _ignored(output)
    check(not _overlap(output, source) and not output.exists(), "extract_unsafe_output")
    with _open_source(source) as stream:
        identity = _verified_source(stream, expected_source_sha256)
        _unchanged(stream, source, identity)
        _private_directory(output.parent)
        with TemporaryDirectory(
            prefix=".calibration-extract-", dir=output.parent
        ) as temporary:
            stage = Path(temporary) / "ready"
            stage.mkdir(mode=0o700)
            export = stage / "calibration-cases.jsonl"
            evidence, selected, counts = (
                hashlib.sha256(),
                hashlib.sha256(),
                {"calibration": 0, "holdout": 0},
            )
            total = 0
            descriptor = os.open(
                export, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as destination:
                while row := stream.readline(MAX_ROW_BYTES + 1):
                    total += len(row)
                    check(total <= MAX_SOURCE_BYTES, "extract_source_size")
                    check(sum(counts.values()) < MAX_ROWS, "extract_row_count")
                    evidence.update(row)
                    role = row_split(row)
                    counts[role] += 1
                    if role == "calibration":
                        destination.write(row)
                        selected.update(row)
                destination.flush()
                os.fsync(destination.fileno())
            check(
                counts["calibration"] > 0 and counts["holdout"] > 0,
                "extract_both_splits_required",
            )
            check(
                total == identity[2] and evidence.hexdigest() == expected_source_sha256,
                "extract_source_changed",
            )
            _unchanged(stream, source, identity)
            receipt = {
                "schema_version": 1,
                "artifact_type": "mechanical-calibration-extraction-evidence",
                "source_sha256": expected_source_sha256,
                "export_sha256": selected.hexdigest(),
                "expected_digest_origin": "caller-supplied; authenticity not established by this operator",
                "rows": counts,
                "source_bytes": total,
                "source_full_read_passes": 2,
                "structural_scanning_includes_heldout_bytes": True,
                "heldout_payload_objects_constructed": False,
                "non_discriminator_payload_values_interpreted": False,
                "reviewed_locally": False,
                "review_method": None,
                "valid_split_review": False,
                "method_sha256": file_hash(Path(__file__)),
                "shared_validation_sha256": {
                    name: file_hash(ROOT / name)
                    for name in (
                        "verification/memory/benchmark.py",
                        "scripts/migrate_chroma_memory_to_sqlite.py",
                    )
                },
                "limits": {
                    "source_bytes": MAX_SOURCE_BYTES,
                    "row_bytes": MAX_ROW_BYTES,
                    "rows": MAX_ROWS,
                    "json_depth": MAX_DEPTH,
                },
                "calibration_validation_and_genuine_human_review_required": True,
                "calibration_benchmark_freeze_or_holdout_execution": False,
            }
            _write_json(stage / "extraction-evidence.json", receipt)
            _unchanged(stream, source, identity)
            _publish(stage, output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        extract_calibration(args.source, args.output, args.expected_source_sha256)
    except (BenchmarkError, MigrationError, OSError) as error:
        reason = (
            str(error)
            if isinstance(error, (BenchmarkError, MigrationError))
            else type(error).__name__
        )
        print("Extraction blocked: " + reason)
        return 1
    print(
        "Mechanical export prepared; calibration validation and genuine human review still required."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
