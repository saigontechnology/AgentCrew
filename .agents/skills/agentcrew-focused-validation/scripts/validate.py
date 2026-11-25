"""Run explicit, narrowly scoped validation without modifying source files."""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run_tool(command: list[str], root: Path) -> subprocess.CompletedProcess[str] | None:
    """Capture tool output without exposing source or initialization payloads."""
    try:
        return subprocess.run(
            command,
            cwd=root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(f"FAIL tool unavailable or timed out: {command[0]}")
        return None


def check_ruff(root: Path, files: list[Path]) -> bool:
    """Report statistics and rule/file/fix-risk groups without applying fixes."""
    version = run_tool(["uvx", "ruff", "--version"], root)
    if version is None or version.returncode != 0:
        print("FAIL Ruff version unavailable")
        return False
    print(version.stdout.strip())
    targets = [path.relative_to(root).as_posix() for path in files]
    prefix = ["uvx", "ruff", "check", "--no-cache"]
    statistics = run_tool([*prefix, "--statistics", "--", *targets], root)
    diagnostics = run_tool([*prefix, "--output-format", "json", "--", *targets], root)
    if statistics is None or diagnostics is None:
        return False
    if statistics.returncode not in (0, 1) or diagnostics.returncode not in (0, 1):
        print(
            "FAIL Ruff invocation; inspect tool availability/configuration separately"
        )
        return False
    print("Ruff statistics:")
    print(statistics.stdout.strip() or "(no statistics emitted)")
    try:
        findings = json.loads(diagnostics.stdout)
        if not isinstance(findings, list):
            raise TypeError("Unexpected diagnostics")
        groups = collections.Counter()
        for finding in findings:
            filename = Path(finding["filename"]).resolve().relative_to(root).as_posix()
            fix = finding.get("fix")
            risk = fix.get("applicability", "manual-review") if fix else "manual-review"
            groups[(finding.get("code") or "uncoded", filename, risk)] += 1
    except (ValueError, TypeError, KeyError, AttributeError):
        print("FAIL invalid Ruff diagnostic format")
        return False
    for (code, filename, risk), count in sorted(groups.items()):
        print(f"{code} | {count} | {filename} | {risk}")
    passed = not findings and statistics.returncode == diagnostics.returncode == 0
    print("PASS Ruff" if passed else "FAIL Ruff findings present")
    return passed


def main() -> int:
    """Validate reviewed files and explicitly selected import modules."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="Explicit repository Python files")
    parser.add_argument("--ruff", action="store_true", help="Run read-only Ruff checks")
    parser.add_argument("--import-module", action="append", default=[], dest="modules")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "AgentCrew").is_dir():
        parser.error("Run from an AgentCrew repository root")
    files = []
    for raw in args.files:
        try:
            path = Path(raw).resolve(strict=True)
            relative = path.relative_to(root)
        except (OSError, ValueError, RuntimeError):
            parser.error(f"File missing or outside repository: {raw}")
        if path.suffix != ".py" or not path.is_file():
            parser.error(f"Expected an explicit Python file: {raw}")
        if "tests" in relative.parts:
            parser.error("The deprecated tests/ tree is excluded")
        if path not in files:
            files.append(path)
    for module in args.modules:
        if not re.fullmatch(r"AgentCrew(?:\.[A-Za-z_][A-Za-z_0-9]*)*", module):
            parser.error(f"Expected an AgentCrew module name: {module}")
    passed = True
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            compile(path.read_bytes(), relative, "exec", dont_inherit=True)
            print(f"PASS syntax {relative}")
        except SyntaxError as error:
            print(f"FAIL syntax {relative}:{error.lineno}:{error.offset}")
            passed = False
        except (OSError, ValueError):
            print(f"FAIL source unreadable or invalid: {relative}")
            passed = False
    if args.ruff:
        passed = check_ruff(root, files) and passed
    else:
        print("SKIP Ruff (request --ruff)")
    for module in args.modules:
        result = run_tool(
            [
                sys.executable,
                "-B",
                "-c",
                "import importlib, sys; importlib.import_module(sys.argv[1])",
                module,
            ],
            root,
        )
        success = result is not None and result.returncode == 0
        print(f"{'PASS' if success else 'FAIL'} import {module} (output suppressed)")
        passed = success and passed
    if not args.modules:
        print("SKIP imports (request --import-module after side-effect review)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
