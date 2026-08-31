#!/usr/bin/env python3
"""Resolve host tools without consulting repository-influenced search paths."""

from __future__ import annotations

import functools
import os
import stat
from pathlib import Path


class TrustedExecutableError(RuntimeError):
    """Raised when a required executable has no trusted system installation."""


def _trusted_directories() -> tuple[Path, ...]:
    raw_directories: list[str] = []
    if hasattr(os, "confstr"):
        try:
            configured = os.confstr("CS_PATH")
        except (OSError, ValueError):
            configured = None
        if configured:
            raw_directories.extend(configured.split(os.pathsep))
    raw_directories.extend(
        (
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/local/bin",
        )
    )
    if os.name == "nt":
        raw_directories.extend(
            (
                r"C:\Program Files\Git\cmd",
                r"C:\Program Files\Git\bin",
                r"C:\Program Files (x86)\Git\cmd",
                r"C:\Program Files (x86)\Git\bin",
            )
        )
    directories: list[Path] = []
    seen: set[str] = set()
    for raw_directory in raw_directories:
        if not raw_directory:
            continue
        directory = Path(raw_directory)
        if not directory.is_absolute():
            continue
        normalized = os.path.normcase(str(directory))
        if normalized in seen:
            continue
        seen.add(normalized)
        directories.append(directory)
    return tuple(directories)


@functools.lru_cache(maxsize=1)
def trusted_command_path() -> str:
    return os.pathsep.join(str(directory) for directory in _trusted_directories())


@functools.lru_cache(maxsize=1)
def trusted_git_executable() -> str:
    names = ("git.exe", "git") if os.name == "nt" else ("git",)
    allowed_owners = {0}
    if hasattr(os, "geteuid"):
        allowed_owners.add(os.geteuid())
    for directory in _trusted_directories():
        for name in names:
            candidate = directory / name
            try:
                resolved = candidate.resolve(strict=True)
                info = resolved.stat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
                continue
            if hasattr(info, "st_uid") and info.st_uid not in allowed_owners:
                continue
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                continue
            return str(resolved)
    raise TrustedExecutableError(
        "Git is unavailable from the trusted operating-system command path"
    )
