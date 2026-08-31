#!/usr/bin/env python3
"""Launch the repository release check across a sanitized Make boundary."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


_REMOVED_ENVIRONMENT = {
    "BASH_ENV",
    "CDPATH",
    "ENV",
    "GNUMAKEFLAGS",
    "MAKEFILES",
    "MAKEFLAGS",
    "MFLAGS",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "SHELLOPTS",
    "VIRTUAL_ENV",
}
_TRUSTED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_RELEASE_PHASES = (
    "check-environment",
    "check-interpreter",
    "validate",
    "test",
    "release-gate",
)


class ReleaseCheckError(RuntimeError):
    """Raised when the release-check launcher cannot establish its boundary."""


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _trusted_executable(
    path: Path, name: str, *, preserve_entrypoint: bool = False
) -> Path:
    if not path.is_absolute():
        raise ReleaseCheckError(f"{name} path must be absolute")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise ReleaseCheckError(f"{name} is unavailable: {exc}") from exc
    allowed_owners = {0}
    if hasattr(os, "geteuid"):
        allowed_owners.add(os.geteuid())
    if (
        not stat.S_ISREG(info.st_mode)
        or not os.access(resolved, os.X_OK)
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or (hasattr(info, "st_uid") and info.st_uid not in allowed_owners)
    ):
        raise ReleaseCheckError(
            f"{name} must be a trusted, non-writable regular executable"
        )
    if preserve_entrypoint:
        try:
            after = path.lstat()
            final_resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ReleaseCheckError(f"{name} entrypoint is unstable: {exc}") from exc
        if _file_identity(before) != _file_identity(after) or final_resolved != resolved:
            raise ReleaseCheckError(f"{name} entrypoint changed during validation")
        return path
    return resolved


def _python_binding(path: Path) -> tuple[tuple[int, ...], str, tuple[int, ...]]:
    _trusted_executable(path, "release-check Python", preserve_entrypoint=True)
    try:
        before_entrypoint = path.lstat()
        before_target = path.resolve(strict=True)
        before_target_info = before_target.stat()
        _trusted_executable(path, "release-check Python", preserve_entrypoint=True)
        after_entrypoint = path.lstat()
        after_target = path.resolve(strict=True)
        after_target_info = after_target.stat()
    except OSError as exc:
        raise ReleaseCheckError(f"release-check Python binding is unavailable: {exc}") from exc
    before = (
        _file_identity(before_entrypoint),
        str(before_target),
        _file_identity(before_target_info),
    )
    after = (
        _file_identity(after_entrypoint),
        str(after_target),
        _file_identity(after_target_info),
    )
    if before != after:
        raise ReleaseCheckError("release-check Python binding changed during validation")
    return before


def _repository_root() -> Path:
    root = Path.cwd().resolve()
    makefile = root / "Makefile"
    try:
        info = makefile.lstat()
    except OSError as exc:
        raise ReleaseCheckError(f"Makefile is unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseCheckError("Makefile must be a nofollow regular release input")
    return root


def main() -> int:
    if os.name != "posix":
        print("ERROR: release checks require a POSIX runtime", file=sys.stderr)
        return 2
    try:
        root = _repository_root()
        python = Path(sys.executable)
        binding = _python_binding(python)
        resolved_python = Path(binding[1])
        if python.is_relative_to(root) or resolved_python.is_relative_to(root):
            raise ReleaseCheckError(
                "release-check Python must be outside the repository"
            )
        make = _trusted_executable(Path("/usr/bin/make"), "system make")
    except ReleaseCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    for name in _REMOVED_ENVIRONMENT:
        environment.pop(name, None)
    environment.update(
        {
            "CODE_REVIEW_CHECK_LAUNCHED": "1",
            "CODE_REVIEW_CHECK_PYTHON": str(python),
            "PATH": _TRUSTED_PATH,
        }
    )
    try:
        for phase in _RELEASE_PHASES:
            if _python_binding(python) != binding:
                raise ReleaseCheckError(
                    f"release-check Python binding changed before {phase}"
                )
            result = subprocess.run(
                [str(make), "--no-print-directory", phase],
                env=environment,
                check=False,
            )
            if _python_binding(python) != binding:
                raise ReleaseCheckError(
                    f"release-check Python binding changed during {phase}"
                )
            if result.returncode != 0:
                return result.returncode
    except ReleaseCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
