"""Dev-only shim re-exporting the packaged migrator with repo private roots.

The migrator core now lives in
``AgentCrew/modules/memory/migrate_chroma_memory_to_sqlite.py`` so production
installs can run it through the ``agentcrew-migrate-memory`` entry point. This
scripts/ module preserves the historical dev CLI and the harness import surface:
it re-exports every name and enforces the repository's ignored private
workspaces by injecting them as ``MigrationOptions.private_roots``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory import migrate_chroma_memory_to_sqlite as _core
from AgentCrew.modules.memory.migrate_chroma_memory_to_sqlite import *
from AgentCrew.modules.memory.migrate_chroma_memory_to_sqlite import (  # noqa: F401
    MigrationError,
    MigrationOptions,
    _assert_counts,
    _destination_fingerprint,
    _inspect,
    _lock,
    _overlap,
    _path,
    _records,
    _report_base,
    _sdk_collection,
    _write_json,
    checksum,
    fingerprint,
    json_text,
    map_record,
    validate_source_tree,
)

PRIVATE_ROOTS = (
    ROOT / "verification/memory/local",
    ROOT / "verification/memory-migration",
)


def _with_private_roots(options: MigrationOptions) -> MigrationOptions:
    if options.private_roots is not None:
        return options
    return replace(options, private_roots=PRIVATE_ROOTS)


def _ignored(path: Path) -> None:
    _core._ignored(path, PRIVATE_ROOTS)


def _private_directory(path: Path) -> None:
    _core._private_directory(path, PRIVATE_ROOTS)


def copy_stable_source(source: Path, destination: Path) -> dict:
    return _core.copy_stable_source(source, destination, PRIVATE_ROOTS)


def validate_options(options: MigrationOptions) -> MigrationOptions:
    return _core.validate_options(_with_private_roots(options))


def migrate(options: MigrationOptions) -> dict:
    return _core.migrate(_with_private_roots(options))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--collection", default="conversation")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--fail-on-parse-error", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate(
            MigrationOptions(
                args.source,
                args.destination,
                args.work_dir,
                args.collection,
                args.batch_size,
                args.report,
                args.apply,
                args.resume,
                args.verify,
                args.fail_on_parse_error,
                private_roots=PRIVATE_ROOTS,
            )
        )
    except BaseException as error:
        reason = (
            str(error) if isinstance(error, MigrationError) else type(error).__name__
        )
        print(json.dumps({"status": "blocked", "reason": reason}), file=sys.stderr)
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
