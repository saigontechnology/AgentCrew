"""Validate Phase 0 labels and privacy boundaries, not retrieval effectiveness."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent
KINDS = {
    "preference",
    "decision",
    "fact",
    "episode",
    "constraint",
    "correction",
    "workaround",
    "unresolved",
}
REQUIRED_CATEGORIES = {
    "exact_identifier",
    "paraphrase",
    "multilingual",
    "ambiguous_followup",
    "preference",
    "decision",
    "constraint_rejected_approach",
    "unresolved",
    "correction_superseded",
    "active_conflict",
    "date_filter",
    "current_session_exclusion",
    "cross_agent_exclusion",
    "same_agent_different_cwd",
    "cwd_not_ranking",
    "new_cwd_display",
    "legacy_cwd_display",
    "deleted_memory",
    "prompt_injection",
    "no_relevant_memory",
    "delegation_transfer",
    "file_name",
    "symbol",
    "model_id",
    "provider_name",
    "url",
    "acronym",
}
COMMITTED_ASSETS = {
    ".gitignore",
    "README.md",
    "baseline.json",
    "fixture-manifest.json",
    "synthetic-corpus.jsonl",
    "benchmark-cases.jsonl",
    "capture_baseline.py",
    "validate_assets.py",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def read_json(name: str) -> dict:
    return json.loads(
        (ASSETS / name).read_text(encoding="utf-8"), object_pairs_hook=unique_object
    )


def read_jsonl(name: str, identifier: str) -> dict[str, dict]:
    result = {}
    for line in (ASSETS / name).read_text(encoding="utf-8").splitlines():
        require(bool(line.strip()), "Blank JSONL record")
        row = json.loads(line, object_pairs_hook=unique_object)
        require(isinstance(row, dict), "JSONL records must be objects")
        key = row[identifier]
        require(key not in result, "Duplicate JSONL identifier")
        require(
            row["schema_version"] == 1 and row["synthetic"] is True,
            "Only versioned synthetic records may be committed",
        )
        result[key] = row
    return result


def safe_cwd(value: str | None) -> bool:
    return value is None or (
        isinstance(value, str)
        and value.startswith("/synthetic/projects/")
        and ".." not in Path(value).parts
    )


def validate_text() -> None:
    for name in COMMITTED_ASSETS:
        raw = (ASSETS / name).read_bytes()
        text = raw.decode("utf-8")
        require(
            raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
            "Invalid text encoding or line ending",
        )
        require(
            not any(line.rstrip() != line for line in text.splitlines()),
            "Trailing whitespace",
        )
        require(
            not re.search(r"^(<{7}|={7}|>{7})(?:\s|$)", text, re.MULTILINE),
            "Unresolved conflict marker",
        )
        require(
            not re.search(
                r"/(?:home|Users|root|tmp|var|opt|workspace|workspaces|mnt|media)/",
                text,
            ),
            "Potential private absolute path",
        )
        require(
            not re.search(
                r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
                text,
            ),
            "Potential credential",
        )
        if name.endswith(".py"):
            ast.parse(text, filename=name)
        if name.endswith(".md"):
            require(
                sum(line.startswith("```") for line in text.splitlines()) % 2 == 0,
                "Unbalanced Markdown code fence",
            )


def validate_corpus(records: dict[str, dict]) -> None:
    for memory_id, record in records.items():
        require(memory_id.startswith("syn-mem-"), "Non-synthetic memory ID")
        metadata = record["metadata"]
        require(metadata["agent"].startswith("Synthetic"), "Non-synthetic agent")
        require(
            metadata["session_id"].startswith("synthetic-session-"),
            "Non-synthetic session",
        )
        require(type(metadata["date"]) is int, "Require integer UTC timestamp")
        require(metadata["type"] == "conversation", "Unexpected source memory type")
        require(safe_cwd(record["cwd"]), "Unsafe CWD")
        require(
            record["status"] in {"active", "superseded", "deleted"},
            "Unknown lifecycle label",
        )
        require(
            bool(record["kinds"]) and set(record["kinds"]) <= KINDS,
            "Invalid kind label",
        )
        require(record["document_format"] == "xml", "Unexpected source document format")
        xml = ElementTree.fromstring(record["document"])
        require(xml.tag == "MEMORY", "Missing MEMORY root")
        for field in (
            "HEAD",
            "DATE",
            "FLOW/INTENT",
            "FLOW/EXPLORATION",
            "FLOW/OUTCOME",
            "INSIGHTS/INSIGHT",
            "ENTITIES/ENTITY/NAME",
            "DOMAINS/DOMAIN",
            "RESOURCES/RESOURCE",
            "CONVERSATION_NOTES/NOTE",
        ):
            require(bool(xml.findtext(field)), "Missing structured source memory field")
        require(xml.findtext("HEAD") == metadata["header"], "Header mismatch")
        require(
            date.fromisoformat(xml.findtext("DATE"))
            == datetime.fromtimestamp(metadata["date"], UTC).date(),
            "Source date mismatch",
        )
        if record["status"] == "superseded":
            target = record["superseded_by"]
            require(
                target in records and records[target]["status"] == "active",
                "Invalid supersession target",
            )
            require(
                records[target]["subject_key"] == record["subject_key"],
                "Supersession crosses subjects",
            )
        else:
            require(record["superseded_by"] is None, "Unexpected supersession link")


def validate_cases(
    records: dict[str, dict], cases: dict[str, dict], manifest: dict
) -> None:
    coverage = set()
    for case_id, case in cases.items():
        require(
            case_id.startswith("syn-case-") and bool(case["query"].strip()),
            "Invalid case identity/query",
        )
        require(safe_cwd(case["current_cwd"]), "Unsafe case CWD")
        require(
            case["agent_name"].startswith("Synthetic")
            and case["current_session_id"].startswith("synthetic-session-"),
            "Invalid case scope",
        )
        require(set(case["kinds"]) <= KINDS, "Invalid query kind")
        categories = case["categories"]
        require(
            bool(categories) and len(categories) == len(set(categories)),
            "Invalid category labels",
        )
        coverage.update(categories)
        sets = []
        for field in (
            "expected_memory_ids",
            "acceptable_memory_ids",
            "excluded_memory_ids",
        ):
            values = case[field]
            require(len(values) == len(set(values)), "Duplicate label ID")
            require(set(values) <= records.keys(), "Unknown fixture reference")
            sets.append(set(values))
        expected, acceptable, excluded = sets
        require(
            not (expected & acceptable or expected & excluded or acceptable & excluded),
            "Contradictory relevance labels",
        )
        require(
            case["must_abstain"] is (not expected and not acceptable),
            "Abstention label mismatch",
        )
        require(
            set(case["expected_cwds"]) == expected,
            "Required results must have CWD labels",
        )
        require(
            bool(case["expected_facts"]) == bool(expected or acceptable),
            "Missing or contradictory answer facts",
        )
        start = date.fromisoformat(case["from_date"]) if case["from_date"] else None
        end = date.fromisoformat(case["to_date"]) if case["to_date"] else None
        require(not (start and end and start > end), "Inverted date range")
        for memory_id in expected | acceptable:
            record = records[memory_id]
            metadata = record["metadata"]
            day = datetime.fromtimestamp(metadata["date"], UTC).date()
            require(record["status"] == "active", "Required result is not active")
            require(
                metadata["agent"] == case["agent_name"],
                "Required result crosses agent boundary",
            )
            require(
                metadata["session_id"] != case["current_session_id"],
                "Required result leaks current session",
            )
            require(not start or day >= start, "Required result predates filter")
            require(not end or day <= end, "Required result postdates filter")
        for memory_id, cwd in case["expected_cwds"].items():
            require(cwd == records[memory_id]["cwd"], "Incorrect provenance label")
        require(
            {
                "syn-mem-other-agent",
                "syn-mem-current-session",
                "syn-mem-deleted",
                "syn-mem-falcon-old",
            }
            <= excluded,
            "Missing safety exclusions",
        )
        if "active_conflict" in categories:
            require(
                case.get("require_conflict_marker") is True and len(expected) >= 2,
                "Conflict labels require both pieces of evidence",
            )
            require(
                len({records[item]["subject_key"] for item in expected}) == 1,
                "Conflict labels cross subjects",
            )
        if "legacy_cwd_display" in categories:
            require(
                case.get("expected_cwd_label") == "Unknown (legacy memory)",
                "Missing legacy provenance label",
            )
            require(
                all(records[item]["cwd"] is None for item in expected),
                "Legacy fixture has invented CWD",
            )
        if "new_cwd_display" in categories:
            require(
                all(records[item]["cwd"] is not None for item in expected),
                "Missing new CWD",
            )
        if "same_agent_different_cwd" in categories:
            require(
                any(records[item]["cwd"] != case["current_cwd"] for item in expected),
                "Missing cross-CWD result",
            )
        if "ambiguous_followup" in categories:
            require(
                all(
                    case["context"].get(field)
                    for field in ("previous_user", "previous_assistant")
                ),
                "Missing follow-up context",
            )
        if "prompt_injection" in categories:
            require(
                case.get("must_not_follow_memory_instructions") is True,
                "Missing untrusted-memory safety assertion",
            )
        if "deleted_memory" in categories:
            require(
                case["required_setup"]
                in {action["action_id"] for action in manifest["setup_actions"]},
                "Missing physical deletion setup",
            )
        if "cwd_not_ranking" in categories:
            paired = cases[case["same_ranking_as_case"]]
            for field in (
                "query",
                "agent_name",
                "current_session_id",
                "from_date",
                "to_date",
                "kinds",
                "expected_memory_ids",
            ):
                require(
                    case[field] == paired[field],
                    "CWD comparison changes retrieval input",
                )
            require(
                case["current_cwd"] != paired["current_cwd"],
                "CWD comparison must change only provenance context",
            )
    require(
        coverage == REQUIRED_CATEGORIES == set(manifest["required_categories"]),
        "Incomplete or unrecognized category coverage",
    )
    for action in manifest["setup_actions"]:
        require(
            action["operation"] == "insert_then_physically_forget",
            "Invalid deletion setup operation",
        )
        require(set(action["memory_ids"]) <= records.keys(), "Unknown setup fixture")
        require(
            all(records[item]["status"] == "deleted" for item in action["memory_ids"]),
            "Deletion setup must target deletion labels",
        )


def main() -> None:
    validate_text()
    manifest = read_json("fixture-manifest.json")
    baseline = read_json("baseline.json")
    require(
        manifest["schema_version"] == baseline["schema_version"] == 1,
        "Unsupported asset version",
    )
    require(
        manifest["synthetic_only"] is True and manifest["fixture_version"] == "1.0.0",
        "Unexpected corpus version",
    )
    records = read_jsonl(manifest["corpus_file"], "id")
    cases = read_jsonl(manifest["cases_file"], "case_id")
    require(
        manifest["record_count"] == len(records)
        and manifest["case_count"] == len(cases),
        "Manifest counts differ",
    )
    for filename, expected_hash in manifest["hashes_sha256"].items():
        require(
            filename in {"synthetic-corpus.jsonl", "benchmark-cases.jsonl"},
            "Unexpected manifest file",
        )
        require(
            hashlib.sha256((ASSETS / filename).read_bytes()).hexdigest()
            == expected_hash,
            "Fixture checksum mismatch",
        )
    require(
        set(manifest["hashes_sha256"])
        == {"synthetic-corpus.jsonl", "benchmark-cases.jsonl"},
        "Missing fixture checksum",
    )
    validate_corpus(records)
    validate_cases(records, cases, manifest)
    require(
        type(baseline["record_count"]) is int and baseline["record_count"] >= 0,
        "Invalid baseline count",
    )
    require(
        baseline["record_count"] == baseline["inspected_metadata_records"],
        "Incomplete metadata inventory",
    )
    require(baseline["snapshot_integrity_check"] == "ok", "Failed snapshot integrity")
    for flag in (
        "source_bytes_stable_during_copy_and_inventory",
        "copied_files_match_source_before_sdk_open",
        "chroma_sdk_used_only_on_copy",
        "fts5_available",
    ):
        require(
            baseline[flag] is True, "Incomplete baseline capability/safety evidence"
        )
    require(
        baseline["source_values_or_paths_recorded"] is False,
        "Unsafe baseline disclosure",
    )
    require(
        hashlib.sha256((ASSETS / "capture_baseline.py").read_bytes()).hexdigest()
        == baseline["capture_script_sha256"],
        "Baseline collector provenance mismatch",
    )
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE VIRTUAL TABLE capability_probe USING fts5(body)")
        connection.execute(
            "INSERT INTO capability_probe VALUES ('unicode mémoire retrieval')"
        )
        require(
            connection.execute(
                "SELECT count(*) FROM capability_probe WHERE capability_probe MATCH ?",
                ("retrieval",),
            ).fetchone()[0]
            == 1,
            "FTS5 capability failed",
        )
    for relative in (
        "local/chroma-copy/chroma.sqlite3",
        "results.json",
        "results/summary.md",
        "private-cases.jsonl",
        "corpus.sqlite3",
        "migration/manifest.json",
        "../memory-migration/chroma-copy/chroma.sqlite3",
    ):
        result = subprocess.run(
            [
                "git",
                "check-ignore",
                "--no-index",
                "--quiet",
                f"verification/memory/{relative}",
            ],
            cwd=ROOT,
            capture_output=True,
        )
        require(result.returncode == 0, "Private/generated artifact is not ignored")
    print(
        f"PASS: {len(records)} synthetic records, {len(cases)} labeled cases, {len(REQUIRED_CATEGORIES)} categories; JSON/XML, references, lifecycle/scope labels, CWD, checksums, UTF-8/whitespace, heuristic privacy scan, syntax, ignore rules and runtime FTS5."
    )
    print(
        "No retrieval quality, latency, migration, production behavior, or private-case review is asserted by this validation."
    )


if __name__ == "__main__":
    main()
