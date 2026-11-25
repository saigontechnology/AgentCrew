"""Profile explicitly approved AgentCrew imports, not live application startup."""

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

MARKER = "AGENTCREW_IMPORT_PROFILE_V1 "
IMPORT_ROW = re.compile(r"^import time:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\S+)\s*$")
CHILD = '''import importlib
import json
import sys
import time

name = sys.argv[1]
already_loaded = name in sys.modules
started = time.perf_counter_ns()
importlib.import_module(name)
first_ns = time.perf_counter_ns() - started
started = time.perf_counter_ns()
importlib.import_module(name)
cached_ns = time.perf_counter_ns() - started
print("AGENTCREW_IMPORT_PROFILE_V1 " + json.dumps({
    "already_loaded_before": already_loaded,
    "first_import_ms": first_ns / 1_000_000,
    "cached_import_ms": cached_ns / 1_000_000,
}), flush=True)
'''


def parse_measurement(output: str) -> dict:
    """Accept timing fields without forwarding unrelated module output."""
    lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ValueError("Expected one measurement")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise TypeError("Expected measurement object")
    if type(value.get("already_loaded_before")) is not bool:
        raise TypeError("Expected preload flag")
    result = {"already_loaded_before": value["already_loaded_before"]}
    for field in ("first_import_ms", "cached_import_ms"):
        number = value.get(field)
        if type(number) not in (int, float):
            raise TypeError("Expected numeric timing")
        if not math.isfinite(number) or number < 0:
            raise ValueError("Invalid timing")
        result[field] = number
    return result


def parse_importtime(output: str, top: int) -> list[dict]:
    """Keep recognized CPython rows only, ordered by exclusive self time."""
    rows = []
    for line in output.splitlines():
        match = IMPORT_ROW.fullmatch(line)
        if match:
            rows.append({
                "module": match[3],
                "self_us": int(match[1]),
                "cumulative_us": int(match[2]),
            })
    return sorted(rows, key=lambda row: row["self_us"], reverse=True)[:top]


def measure(module: str, root: Path, timeout: float, trace: bool, top: int) -> dict:
    """Run one fresh interpreter, capturing rather than echoing child output."""
    command = [sys.executable, "-B"]
    if trace:
        command.extend(["-X", "importtime"])
    command.extend(["-c", CHILD, module])
    started = time.perf_counter_ns()
    result = subprocess.run(
        command,
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    wall_ms = (time.perf_counter_ns() - started) / 1_000_000
    if result.returncode:
        raise RuntimeError("Import process failed")
    sample = parse_measurement(result.stdout)
    sample["process_wall_ms"] = wall_ms
    sample["top_imports"] = parse_importtime(result.stderr, top) if trace else []
    return sample


def summarize(samples: list[dict]) -> dict:
    """Summarize durations without silently dropping unsuccessful samples."""
    result = {}
    for field in ("first_import_ms", "cached_import_ms", "process_wall_ms"):
        numbers = [sample[field] for sample in samples]
        result[field] = {
            "median": statistics.median(numbers),
            "min": min(numbers),
            "max": max(numbers),
        }
    return result


def main() -> int:
    """Validate explicit targets and emit one structured import-only report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="+", help="Reviewed AgentCrew module names")
    parser.add_argument("--allow-imports", action="store_true")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--importtime", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    if not args.allow_imports:
        parser.error("Review import side effects, then explicitly pass --allow-imports")
    if not 1 <= args.repeat <= 20 or not 1 <= args.top <= 50:
        parser.error("--repeat must be 1..20 and --top must be 1..50")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        parser.error("--timeout must be finite and greater than 0, at most 300 seconds")
    root = Path.cwd().resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "AgentCrew").is_dir():
        parser.error("Run from an AgentCrew repository root")
    for module in args.modules:
        if not re.fullmatch(r"AgentCrew(?:\.[A-Za-z_][A-Za-z_0-9]*)*", module):
            parser.error(f"Expected an AgentCrew module name: {module}")
    report = {
        "schema_version": 1,
        "python": sys.version.split()[0],
        "scope": "imports-only; NOT console startup",
        "importtime_enabled": args.importtime,
        "bytecode_writes": False,
        "samples_per_module": args.repeat,
        "limitations": [
            "Fresh interpreter, not cold filesystem or bytecode caches",
            "Cached import is not warm application startup",
            "Process wall time excludes outer uv launcher",
            "Tracing adds overhead and includes harness/interpreter imports",
            "Imports execute code with inherited environment; not sandboxed",
        ],
        "modules": {},
    }
    for module in dict.fromkeys(args.modules):
        try:
            samples = [
                measure(module, root, args.timeout, args.importtime, args.top)
                for _ in range(args.repeat)
            ]
        except (OSError, RuntimeError, ValueError, TypeError, subprocess.TimeoutExpired):
            print(f"FAIL import profiling: {module}; child output suppressed", file=sys.stderr)
            return 1
        report["modules"][module] = {
            "samples": samples,
            "summary": summarize(samples),
        }
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
