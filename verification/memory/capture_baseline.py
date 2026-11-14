"""Capture aggregate memory inventory from a stable, private Chroma copy."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = ROOT / "verification/memory/local"
SAFE_METADATA_KEYS = {
    "agent",
    "session_id",
    "conversation_id",
    "date",
    "timestamp",
    "type",
    "header",
    "evolved_at",
    "cwd",
}


def fingerprint(directory: Path) -> dict[str, str]:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Source symlinks require operator review")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(path.relative_to(directory))] = digest.hexdigest()
    return result


def capture(source: Path, destination: Path, source_label: str) -> dict:
    source = source.expanduser().resolve()
    destination = destination.resolve()
    if not destination.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ValueError("Snapshot must be inside the ignored local directory")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Source and destination must not overlap")
    if destination.exists() or not (source / "chroma.sqlite3").is_file():
        raise ValueError(
            "Require an existing Chroma source and an unused snapshot directory"
        )
    PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE_ROOT, 0o700)
    before = fingerprint(source)
    destination.mkdir(mode=0o700)
    backup = destination / "source-backup"
    working_copy = destination / "chroma-copy"
    shutil.copytree(source, backup)
    after = fingerprint(source)
    if before != after or before != fingerprint(backup):
        raise RuntimeError(
            "Source changed during copy; pause writers and retry with a fresh snapshot"
        )
    with sqlite3.connect(":memory:") as probe:
        probe.execute("CREATE VIRTUAL TABLE capability_probe USING fts5(body)")
        probe.execute(
            "INSERT INTO capability_probe VALUES (?)", ("retrieval capability",)
        )
        fts5 = (
            probe.execute(
                "SELECT count(*) FROM capability_probe WHERE capability_probe MATCH ?",
                ("retrieval",),
            ).fetchone()[0]
            == 1
        )
    shutil.copytree(backup, working_copy)
    if fingerprint(working_copy) != before:
        raise RuntimeError("Working copy does not match frozen backup")
    uri = (working_copy / "chroma.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("Snapshot SQLite integrity check failed")
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(working_copy),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_collection("conversation", embedding_function=None)
    count = collection.count()
    keys = set()
    inspected = 0
    for offset in range(0, count, 250):
        batch = collection.get(limit=250, offset=offset, include=["metadatas"])
        inspected += len(batch["ids"])
        for metadata in batch["metadatas"] or []:
            keys.update((metadata or {}).keys())
    if inspected != count or collection.count() != count:
        raise RuntimeError("Copied collection inventory count changed")
    if not keys <= SAFE_METADATA_KEYS:
        raise ValueError("Unknown metadata key names withheld; private review required")
    if fingerprint(source) != before or fingerprint(backup) != before:
        raise RuntimeError(
            "Source or frozen backup changed during inventory; take a new baseline"
        )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    chroma_lock = next(
        item["version"] for item in lock["package"] if item["name"] == "chromadb"
    )
    return {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "implementation_baseline_commit": "a6c186fde41f7252fb311abd2af8e6d2abde87f1",
        "checkout_commit_at_capture": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "capture_script_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "package_version": project["version"],
        "python_requirement": project["requires-python"],
        "runtime_python": platform.python_version(),
        "installed_chromadb": importlib.metadata.version("chromadb"),
        "locked_chromadb": chroma_lock,
        "runtime_sqlite": sqlite3.sqlite_version,
        "fts5_available": fts5,
        "source_selector": source_label,
        "collection_name": "conversation",
        "record_count": count,
        "inspected_metadata_records": inspected,
        "metadata_keys": sorted(keys),
        "snapshot_integrity_check": integrity,
        "source_bytes_stable_during_copy_and_inventory": True,
        "copied_files_match_source_before_sdk_open": True,
        "chroma_sdk_used_only_on_copy": True,
        "source_values_or_paths_recorded": False,
        "writers_confirmed_paused": False,
        "inventory_only_not_migration_or_retrieval_benchmark": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("MEMORYDB_PATH"))
    parser.add_argument(
        "--source-label",
        choices=[
            "MEMORYDB_PATH",
            "repository-development-default",
            "operator-selected",
        ],
        default="MEMORYDB_PATH",
    )
    parser.add_argument("--snapshot-dir", required=True)
    args = parser.parse_args()
    try:
        if not args.source:
            raise ValueError("Set MEMORYDB_PATH or provide --source explicitly")
        report = capture(Path(args.source), Path(args.snapshot_dir), args.source_label)
        output = ROOT / "verification/memory/baseline.json"
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except Exception as error:
        print(
            f"Baseline capture blocked ({type(error).__name__}); inspect privately, do not publish exception values.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
