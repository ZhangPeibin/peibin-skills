#!/usr/bin/env python3
"""Run unittest targets from one explicit, contained test root."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True


def _test_cases(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _test_cases(test)
        else:
            yield test


def _verify_test_origins(suite: unittest.TestSuite, test_root: Path) -> None:
    for test in _test_cases(suite):
        module_name = test.__class__.__module__
        if module_name == "unittest.loader":
            continue
        module = sys.modules.get(module_name)
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str) or not _contained(Path(origin), test_root):
            raise RuntimeError(
                f"loaded test module {module_name!r} is outside the test root"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("discover", "run"))
    parser.add_argument("test_root", type=Path)
    parser.add_argument("test", nargs="?")
    args = parser.parse_args()

    if args.mode == "run" and args.test is None:
        parser.error("run requires a fully qualified test name")
    if args.mode == "discover" and args.test is not None:
        parser.error("discover does not accept a test name")

    test_root = args.test_root.resolve(strict=True)
    if not test_root.is_dir():
        parser.error("test_root must be a directory")

    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    sys.path.insert(0, str(test_root))
    loader = unittest.defaultTestLoader
    suite = (
        loader.discover(str(test_root), top_level_dir=str(test_root))
        if args.mode == "discover"
        else loader.loadTestsFromName(args.test)
    )
    _verify_test_origins(suite, test_root)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    print("CODE_REVIEW_UNITTEST_RUNNER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
