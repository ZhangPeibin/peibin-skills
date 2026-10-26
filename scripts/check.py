#!/usr/bin/env python3
"""Run the repository checks: validate metadata and every personal Skill test suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    validate = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts" / "validate_repo.py")],
    )
    if validate.returncode != 0:
        return validate.returncode

    test_dirs = sorted((ROOT / "skills" / "personal").glob("**/tests"))
    if not test_dirs:
        print("ERROR: no personal skill test suites found", file=sys.stderr)
        return 1

    for test_dir in test_dirs:
        print(f"==> {test_dir.relative_to(ROOT)}")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "unittest",
                "discover",
                "-s",
                str(test_dir),
                "-t",
                str(test_dir),
            ],
        )
        if result.returncode != 0:
            return result.returncode

    print("CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
