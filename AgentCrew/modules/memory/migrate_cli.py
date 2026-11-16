"""Standalone Chroma-to-SQLite memory migration CLI for production installs.

Production installs ship only the AgentCrew package, so the migration runs via
the ``agentcrew-migrate-memory`` console entry point (for example with uvx:
``uvx --from 'agentcrew-ai[chromadb]==<version>' agentcrew-migrate-memory``).
The default mode delegates to the same verified production flow the
application uses at startup; ``--discard-old-memory`` is the explicit skip
path that deletes the old Chroma data so the application starts fresh.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from AgentCrew.modules.memory import engine_selection


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcrew-migrate-memory",
        description=(
            "Migrate old Chroma memory data to the SQLite memory store, or "
            "discard the old Chroma data to start fresh. Run this when AgentCrew "
            "reports old Chroma memory on startup. The migration is offline: no "
            "network, no embedding calls, and the Chroma source directory is left "
            "untouched."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=engine_selection.chroma_source_path(),
        help="Chroma memory directory (default: $MEMORYDB_PATH or ./memory_db).",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=engine_selection.sqlite_store_path(),
        help="SQLite store to create (default: agent_memory.sqlite3 under "
        "$MEMORYDB_PATH or ./memory_db).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=engine_selection.sqlite_migration_work_path(),
        help="Migration work directory for the offline copy and report "
        "(default: migration-work under $MEMORYDB_PATH or ./memory_db).",
    )
    parser.add_argument(
        "--collection",
        default="conversation",
        help="Chroma collection to migrate (default: conversation).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=engine_selection.MIGRATION_BATCH_SIZE,
        help="Records per migration batch (default: 100).",
    )
    parser.add_argument(
        "--discard-old-memory",
        action="store_true",
        help="Skip the migration: delete the old Chroma artifacts in the source "
        "directory (everything except the SQLite store and the migration work "
        "directory, which is also removed) so the application starts with a "
        "fresh, empty memory store. Asks for confirmation unless --yes is given.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask for confirmation (also overrides the completed-migration "
        "refusal of --discard-old-memory).",
    )
    return parser


def _migrate(
    source: Path,
    destination: Path,
    work_dir: Path,
    collection: str,
    batch_size: int,
) -> int:
    if not (source / "chroma.sqlite3").is_file():
        print(
            f"No Chroma memory data found at '{source}' (missing chroma.sqlite3).",
            file=sys.stderr,
        )
        return 1
    try:
        import chromadb  # noqa: F401
    except ImportError:
        print(
            "The chromadb package is needed to read the old Chroma memory. Install "
            "it (for example: pip install \"agentcrew-ai[chromadb]\") or re-run "
            "this command from an environment that includes the chromadb extra.",
            file=sys.stderr,
        )
        return 1
    resume = work_dir.exists()
    report = engine_selection._migrate_chroma_to_sqlite(
        source, destination, work_dir, collection, resume, batch_size
    )
    status = str(report.get("status"))
    print(f"Memory migration status: {status}")
    if report.get("warning_skipped_count"):
        print(
            f"Skipped {report['warning_skipped_count']} of "
            f"{report['source_count']} record(s) with parse warnings "
            f"({report['warning_counts']}); the skipped records' hashed IDs are "
            f"listed in {work_dir / 'report.json'}."
        )
    print(
        f"Inserted {report.get('inserted', 0)} record(s); "
        f"verified {report.get('verified', 0)} record(s)."
    )
    print(f"Full report: {work_dir / 'report.json'}")
    return 0 if status in ("verified", "applied") else 1


def _discard_old_memory(
    source: Path, work_dir: Path, destination: Path, assume_yes: bool
) -> int:
    if (
        destination.exists()
        and engine_selection._migration_completed(work_dir)
        and not assume_yes
    ):
        print(
            f"Refusing to discard: the SQLite store '{destination}' already exists "
            f"and a completed migration report was found in '{work_dir}'. The old "
            "Chroma memory is the rollback source for that migration. Re-run with "
            "--yes to discard it anyway.",
            file=sys.stderr,
        )
        return 1
    if not source.is_dir():
        print(
            f"No Chroma memory directory found at '{source}'; nothing to discard.",
            file=sys.stderr,
        )
        return 1
    targets = [
        entry
        for entry in sorted(source.iterdir())
        if not engine_selection._is_non_chroma_name(entry.name)
    ]
    if not assume_yes:
        print(f"The following Chroma artifacts in '{source}' will be deleted:")
        for path in targets:
            print(f"  - {path}")
        if work_dir.exists():
            print(f"The migration work directory '{work_dir}' will also be removed.")
        try:
            answer = input("Delete the old Chroma memory now? [y/N] ")
        except KeyboardInterrupt:
            print()
            print("Aborted; nothing was deleted.")
            return 130
        except EOFError:
            print("Aborted; nothing was deleted. Pass --yes to skip the prompt.")
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted; nothing was deleted.")
            return 0
    deleted = []
    for path in targets:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(path)
    work_dir_removed = False
    if work_dir.exists():
        shutil.rmtree(work_dir)
        work_dir_removed = True
    print(f"Deleted {len(deleted)} Chroma artifact(s) from '{source}':")
    for path in deleted:
        print(f"  - {path}")
    if work_dir_removed:
        print(f"Removed migration work directory: {work_dir}")
    print("The application will start with a fresh, empty SQLite memory store.")
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    try:
        if args.discard_old_memory:
            return _discard_old_memory(source, work_dir, destination, args.yes)
        return _migrate(
            source, destination, work_dir, args.collection, args.batch_size
        )
    except KeyboardInterrupt:
        print(
            "Interrupted; re-run the same command to resume the migration.",
            file=sys.stderr,
        )
        return 130
    except Exception as error:
        print(f"Migration blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
