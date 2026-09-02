#!/usr/bin/env python3
"""Resolve a To-Audit v2 scope without mutating the reviewed repository."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

from review_contract import SCHEMA_VERSION, ContractError, validate_contract
from trusted_executables import (
    TrustedExecutableError,
    trusted_command_path,
    trusted_git_executable,
)


ZERO_OID_RE = re.compile(r"^0+$")
SECRET_NAMES = {
    ".env",
    ".env.local",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "_netrc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
SECRET_SUFFIXES = {
    ".jks",
    ".kdbx",
    ".key",
    ".keychain",
    ".keychain-db",
    ".keystore",
    ".p12",
    ".pfx",
    ".pem",
}
SECRET_DATA_SUFFIXES = {
    ".cfg",
    ".conf",
    ".db",
    ".env",
    ".ini",
    ".json",
    ".properties",
    ".sqlite",
    ".sqlite3",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_DATA_STEM_RE = re.compile(
    r"^(?:"
    r"secrets?(?:_|$)|"
    r"password|passwd|"
    r"auth(?:_|$)|oauth|"
    r"api_?key|client_?secrets?|"
    r"credentials?|tokens?|"
    r"keychain|keystore|"
    r"service_?accounts?"
    r")"
)
SECRET_DIRECTORY_NAMES = {"credential", "credentials", "secret", "secrets"}
VENDORED_PARTS = {"node_modules", "third_party", "vendor", "vendors"}
GENERATED_PARTS = {"build", "coverage", "dist", "generated"}
RENAME_LIMIT = 1000
SNAPSHOT_WORKTREE_ENTRY_LIMIT = 100_000
GITFILE_MAX_BYTES = 4096
OUTPUT_INDICATOR_OPTIONS = (
    "--output-indicator-new=+",
    "--output-indicator-old=-",
    "--output-indicator-context= ",
)
_OUTPUT_INDICATOR_SUPPORT: dict[str, bool] = {}


class ScopeError(ValueError):
    pass


class SnapshotBudgetExceeded(ScopeError):
    def __init__(self, budget: str, limit: int, observed: int) -> None:
        self.budget = budget
        self.limit = limit
        self.observed = observed
        super().__init__(
            f"snapshot {budget} budget exceeded: observed {observed}, limit {limit}"
        )

    def coverage_gap(self) -> dict[str, Any]:
        return {
            "kind": "snapshot_budget_exceeded",
            "budget": self.budget,
            "limit": self.limit,
            "observed": self.observed,
            "reason": "repository snapshot exceeded its deterministic inspection budget",
        }


def _entry_is_type(directory_fd: int, name: str, predicate: Any) -> bool:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return bool(predicate(info.st_mode))


def _is_git_admin_layout_fd(directory_fd: int) -> bool:
    if not _entry_is_type(directory_fd, "HEAD", stat.S_ISREG):
        return False
    full_layout = _entry_is_type(
        directory_fd, "objects", stat.S_ISDIR
    ) and _entry_is_type(directory_fd, "refs", stat.S_ISDIR)
    linked_layout = _entry_is_type(
        directory_fd, "commondir", stat.S_ISREG
    ) and _entry_is_type(directory_fd, "gitdir", stat.S_ISREG)
    return full_layout or linked_layout


def _read_gitfile_target(directory_fd: int, directory: Path) -> Path | None:
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    try:
        before = os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size > GITFILE_MAX_BYTES:
            return None
        descriptor = os.open(".git", flags, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > GITFILE_MAX_BYTES
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            return None
        payload = os.read(descriptor, GITFILE_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        current = os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identity_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if len(payload) > GITFILE_MAX_BYTES or any(
        getattr(opened, field) != getattr(after, field)
        or getattr(opened, field) != getattr(current, field)
        for field in identity_fields
    ):
        return None
    marker = payload.decode("utf-8", "surrogateescape")
    marker = marker.removesuffix("\n").removesuffix("\r")
    if "\n" in marker or not marker.startswith("gitdir: "):
        return None
    raw_target = marker.removeprefix("gitdir: ")
    if not raw_target:
        return None
    target = Path(raw_target)
    return target if target.is_absolute() else directory / target


def _open_git_admin_layout(path: Path) -> bool:
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    try:
        path_info = path.lstat()
        if not stat.S_ISDIR(path_info.st_mode):
            return False
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        return (
            opened.st_dev == path_info.st_dev
            and opened.st_ino == path_info.st_ino
            and _is_git_admin_layout_fd(descriptor)
        )
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _nested_repository_boundary_fd(directory_fd: int, directory: Path) -> bool:
    try:
        marker = os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        marker = None
    except OSError:
        return False
    if marker is not None and stat.S_ISDIR(marker.st_mode):
        return _open_git_admin_layout(directory / ".git")
    if marker is not None and stat.S_ISREG(marker.st_mode):
        target = _read_gitfile_target(directory_fd, directory)
        return target is not None and _open_git_admin_layout(target)
    if marker is not None:
        return False
    return _is_git_admin_layout_fd(directory_fd)


def _require_posix() -> None:
    if os.name != "posix":
        raise ScopeError("scope resolution requires a POSIX runtime")


def validate_positive_integer(
    value: object,
    name: str,
    *,
    error_type: type[ValueError] = ScopeError,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise error_type(f"{name} must be a positive integer")
    return value


def validate_path_text(
    value: object,
    name: str,
    *,
    error_type: type[ValueError] = ScopeError,
) -> str:
    if not isinstance(value, str) or not value:
        raise error_type(f"{name} must be a non-empty path string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise error_type(f"{name} must not contain control characters")
    return value


def _positive_integer_argument(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    return validate_positive_integer(
        parsed,
        "value",
        error_type=argparse.ArgumentTypeError,
    )


class ContainedPathError(ScopeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _likely_secret_path(relative_path: str) -> bool:
    path = Path(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    normalized_parent_parts = {
        re.sub(r"[-.]+", "_", part).strip("_") for part in parts[:-1]
    }
    name = path.name.lower()
    normalized_stem = re.sub(r"[-.]+", "_", path.stem.lower())
    if (
        normalized_parent_parts & SECRET_DIRECTORY_NAMES
        or name in SECRET_NAMES
        or name.startswith(".env.")
        or name == ".envrc"
        or name.startswith(".envrc.")
        or name == ".dev.vars"
        or name.startswith(".dev.vars.")
        or name.endswith(".tfvars")
        or name.endswith(".tfvars.json")
        or path.suffix.lower() in SECRET_SUFFIXES
        or (
            (path.suffix.lower() in SECRET_DATA_SUFFIXES or not path.suffix)
            and SECRET_DATA_STEM_RE.match(normalized_stem) is not None
        )
    ):
        return True
    cloud_paths = {
        (".aws", "config"),
        (".aws", "credentials"),
        (".azure", "accesstokens.json"),
        (".azure", "azureprofile.json"),
        (".azure", "msal_token_cache.json"),
        (".azure", "service_principal_entries.json"),
        (".config", "gcloud", "access_tokens.db"),
        (".config", "gcloud", "application_default_credentials.json"),
        (".config", "gcloud", "credentials.db"),
        (".docker", "config.json"),
        (".kube", "config"),
    }
    if any(parts[-len(candidate) :] == candidate for candidate in cloud_paths):
        return True
    return any(
        parts[index : index + 2] == (".config", "gcloud")
        and "legacy_credentials" in parts[index + 2 :]
        for index in range(max(0, len(parts) - 1))
    )


def _git_environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in (
            "COMSPEC",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WINDIR",
        )
        if name in os.environ
    }
    environment["GIT_ATTR_NOSYSTEM"] = "1"
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["LC_ALL"] = "C"
    environment["PATH"] = trusted_command_path()
    return environment


def _git(
    repo: Path,
    *args: str,
    input_data: bytes | None = None,
    reject_incomplete_renames: bool = False,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    try:
        executable = trusted_git_executable()
    except TrustedExecutableError as exc:
        raise ScopeError(str(exc)) from exc
    result = subprocess.run(
        [
            executable,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.fileMode=true",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            "diff.ignoreSubmodules=none",
            "-C",
            str(repo),
            *args,
        ],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if result.returncode not in accepted_returncodes:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ScopeError(message or f"git {' '.join(args)} failed")
    if reject_incomplete_renames and b"exhaustive rename detection was skipped" in result.stderr:
        raise ScopeError(
            f"rename detection exceeded the canonical limit of {RENAME_LIMIT}; reduce the scope"
        )
    return result.stdout


def _repo_root(repo: Path) -> Path:
    validate_path_text(os.fspath(repo), "repository path")
    requested = repo.resolve()
    root = Path(
        _git(requested, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    if not requested.is_relative_to(root):
        raise ScopeError(
            "repository worktree resolution escaped the requested repository path"
        )
    try:
        (requested / ".git").lstat()
        requested_has_git_marker = True
    except FileNotFoundError:
        requested_has_git_marker = False
    except OSError as exc:
        raise ScopeError(f"repository Git root marker is unavailable: {exc}") from exc
    if requested_has_git_marker and requested != root:
        raise ScopeError(
            "repository core.worktree redirected an explicit Git root"
        )
    requested_git_dir = Path(
        _git(requested, "rev-parse", "--absolute-git-dir").decode().strip()
    ).resolve()
    root_git_dir = Path(
        _git(root, "rev-parse", "--absolute-git-dir").decode().strip()
    ).resolve()
    if requested_git_dir != root_git_dir:
        raise ScopeError(
            "repository worktree resolution changed Git directory identity"
        )
    requested_common_value = Path(
        _git(requested, "rev-parse", "--git-common-dir").decode().strip()
    )
    root_common_value = Path(
        _git(root, "rev-parse", "--git-common-dir").decode().strip()
    )
    requested_common_dir = (
        requested_common_value.resolve()
        if requested_common_value.is_absolute()
        else (requested / requested_common_value).resolve()
    )
    root_common_dir = (
        root_common_value.resolve()
        if root_common_value.is_absolute()
        else (root / root_common_value).resolve()
    )
    if requested_common_dir != root_common_dir:
        raise ScopeError(
            "repository worktree resolution changed Git common-directory identity"
        )
    return root


def _head_or_empty(repo: Path) -> str:
    try:
        executable = trusted_git_executable()
    except TrustedExecutableError as exc:
        raise ScopeError(str(exc)) from exc
    result = subprocess.run(
        [executable, "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_git_environment(),
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    symbolic = subprocess.run(
        [executable, "-C", str(repo), "symbolic-ref", "-q", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_git_environment(),
        text=True,
        check=False,
    )
    if symbolic.returncode != 0:
        raise ScopeError("HEAD does not resolve to a commit")
    reference = symbolic.stdout.strip()
    present = subprocess.run(
        [executable, "-C", str(repo), "show-ref", "--verify", "--quiet", reference],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_git_environment(),
        check=False,
    )
    if present.returncode == 1:
        return _empty_tree(repo)
    raise ScopeError("HEAD references an invalid commit")


def _empty_tree(repo: Path) -> str:
    return _git(
        repo, "hash-object", "-t", "tree", "--stdin", input_data=b""
    ).decode().strip()


def _zero_oid(repo: Path) -> str:
    return "0" * len(_empty_tree(repo))


def _resolve_commit(repo: Path, value: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").decode().strip()


def _parse_raw(raw: bytes) -> list[dict[str, str | None]]:
    chunks = raw.split(b"\0")
    records: list[dict[str, str | None]] = []
    index = 0
    while index < len(chunks) and chunks[index]:
        header = chunks[index].decode("ascii")
        index += 1
        fields = header.split()
        if len(fields) != 5 or not fields[0].startswith(":"):
            raise ScopeError("unexpected git raw-diff output")
        old_mode = fields[0][1:]
        new_mode, old_oid, new_oid, status_score = fields[1:]
        status = status_score[0]
        old_path = chunks[index].decode("utf-8", "surrogateescape")
        index += 1
        new_path: str | None = old_path
        if status in {"R", "C"}:
            new_path = chunks[index].decode("utf-8", "surrogateescape")
            index += 1
        if status == "A":
            old_path = None
        elif status == "D":
            new_path = None
        records.append(
            {
                "status": status,
                "status_score": status_score,
                "old_mode": old_mode,
                "new_mode": new_mode,
                "old_oid": old_oid,
                "new_oid": new_oid,
                "old_path": old_path,
                "new_path": new_path,
            }
        )
    return records


def _output_indicator_options(repo: Path) -> list[str]:
    try:
        executable = trusted_git_executable()
    except TrustedExecutableError as exc:
        raise ScopeError(str(exc)) from exc
    supported = _OUTPUT_INDICATOR_SUPPORT.get(executable)
    if supported is None:
        result = subprocess.run(
            [
                executable,
                "-C",
                str(repo),
                "diff",
                "--no-index",
                *OUTPUT_INDICATOR_OPTIONS,
                "--",
                os.devnull,
                os.devnull,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            check=False,
        )
        supported = result.returncode in {0, 1}
        _OUTPUT_INDICATOR_SUPPORT[executable] = supported
    return list(OUTPUT_INDICATOR_OPTIONS) if supported else []


def _diff_bytes(
    repo: Path,
    args: list[str],
    paths: str | list[str],
    *,
    patch: bool,
) -> bytes:
    pathspecs = [paths] if isinstance(paths, str) else list(dict.fromkeys(paths))
    options = (
        [
            "--patch",
            "--text",
            "--no-color",
            *_output_indicator_options(repo),
            "--unified=0",
            "--inter-hunk-context=0",
            "--diff-algorithm=myers",
            "--no-indent-heuristic",
            "--find-renames",
            f"-l{RENAME_LIMIT}",
        ]
        if patch
        else ["--numstat", "--text", "--find-renames", f"-l{RENAME_LIMIT}"]
    )
    return _git(
        repo,
        "--literal-pathspecs",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        *options,
        *args,
        "--",
        *pathspecs,
    )


def _blob_prefix(repo: Path, oid: str, limit: int = 8192) -> bytes:
    try:
        executable = trusted_git_executable()
    except TrustedExecutableError as exc:
        raise ScopeError(str(exc)) from exc
    process = subprocess.Popen(
        [
            executable,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.fileMode=true",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            "diff.ignoreSubmodules=none",
            "-C",
            str(repo),
            "cat-file",
            "blob",
            oid,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        payload = process.stdout.read(limit)
        if len(payload) == limit and process.poll() is None:
            process.terminate()
            process.wait()
            return payload
        error = process.stderr.read()
        return_code = process.wait()
        if return_code != 0:
            message = error.decode("utf-8", "replace").strip()
            raise ScopeError(message or f"git cat-file blob {oid} failed")
        return payload
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def _tracked_record_is_binary(
    repo: Path, raw: dict[str, str | None], *, source: str
) -> bool:
    endpoints = (
        (str(raw["old_mode"]), str(raw["old_oid"]), raw["old_path"], False),
        (
            str(raw["new_mode"]),
            str(raw["new_oid"]),
            raw["new_path"],
            source == "unstaged",
        ),
    )
    for mode, oid, path, allow_worktree in endpoints:
        if mode not in {"100644", "100755"}:
            continue
        if ZERO_OID_RE.match(oid):
            if not allow_worktree or path is None:
                continue
            payload = _read_contained_regular(
                repo, path, read_limit=8192
            )
        else:
            payload = _blob_prefix(repo, oid)
        if b"\0" in payload:
            return True
    return False


def _has_content_change(repo: Path, args: list[str], path: str) -> bool:
    patch = _diff_bytes(repo, args, path, patch=True)
    return bool(re.search(br"^@@ ", patch, re.MULTILINE))


def _line_anchor(
    repo: Path,
    args: list[str],
    old_path: str | None,
    new_path: str | None,
) -> dict[str, Any]:
    hunks = _diff_hunks(repo, args, old_path, new_path)
    if hunks:
        return hunks[0]["anchor"]
    return {
        "kind": "change",
        "path": new_path or old_path,
        "change_type": "text",
    }


def _diff_hunks(
    repo: Path,
    args: list[str],
    old_path: str | None,
    new_path: str | None,
) -> list[dict[str, Any]]:
    paths = [path for path in (old_path, new_path) if path is not None]
    patch = _diff_bytes(repo, args, paths, patch=True)
    lines = patch.split(b"\n")
    hunks: list[dict[str, Any]] = []
    index = 0
    header_pattern = re.compile(
        br"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
    )
    while index < len(lines):
        match = header_pattern.match(lines[index])
        if match is None:
            index += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2) or b"1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or b"1")
        old_cursor = old_start
        new_cursor = new_start
        consumed_old = 0
        consumed_new = 0
        first_old_line: int | None = None
        first_new_line: int | None = None
        index += 1
        while index < len(lines) and not lines[index].startswith(b"@@ "):
            line = lines[index]
            if line.startswith(b"+"):
                if first_new_line is None:
                    first_new_line = new_cursor
                new_cursor += 1
                consumed_new += 1
            elif line.startswith(b"-"):
                if first_old_line is None:
                    first_old_line = old_cursor
                old_cursor += 1
                consumed_old += 1
            elif line.startswith(b" "):
                old_cursor += 1
                new_cursor += 1
                consumed_old += 1
                consumed_new += 1
            elif line == b"" or line.startswith(b"\\ No newline at end of file"):
                pass
            else:
                raise ScopeError("unexpected git patch hunk body")
            index += 1
        if consumed_old != old_count or consumed_new != new_count:
            raise ScopeError("git patch hunk line counts do not match its header")
        anchor = (
            {
                "kind": "line",
                "side": "new",
                "path": new_path,
                "line": first_new_line,
            }
            if first_new_line is not None
            else {
                "kind": "line",
                "side": "old",
                "path": old_path,
                "line": first_old_line,
            }
            if first_old_line is not None
            else {
                "kind": "change",
                "path": new_path or old_path,
                "change_type": "text",
            }
        )
        hunks.append(
            {
                "region": {
                    "kind": "diff_hunk",
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                },
                "anchor": anchor,
            }
        )
    return hunks


def _open_contained_parent(repo: Path, relative_path: str) -> tuple[int, str]:
    relative = Path(relative_path)
    parts = relative.parts
    if relative.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContainedPathError("outside-repository")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(repo, directory_flags)
    try:
        for part in parts[:-1]:
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        return directory_fd, parts[-1]
    except OSError as exc:
        os.close(directory_fd)
        reason = (
            "outside-repository"
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}
            else "unreadable"
        )
        raise ContainedPathError(reason) from exc


def _read_contained_regular(
    repo: Path,
    relative_path: str,
    *,
    max_bytes: int | None = None,
    read_limit: int | None = None,
) -> bytes:
    directory_fd, name = _open_contained_parent(repo, relative_path)
    file_fd: int | None = None
    try:
        file_fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ContainedPathError("special-or-symlink")
        if max_bytes is not None and info.st_size > max_bytes:
            raise ContainedPathError("oversized")
        with os.fdopen(file_fd, "rb") as stream:
            file_fd = None
            return stream.read() if read_limit is None else stream.read(read_limit)
    except OSError as exc:
        reason = (
            "special-or-symlink"
            if exc.errno in {errno.ELOOP, errno.ENXIO}
            else "unreadable"
        )
        raise ContainedPathError(reason) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _read_contained_link(repo: Path, relative_path: str) -> str:
    directory_fd, name = _open_contained_parent(repo, relative_path)
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISLNK(info.st_mode):
            raise ContainedPathError("special-or-symlink")
        return os.readlink(name, dir_fd=directory_fd)
    except OSError as exc:
        raise ContainedPathError("unreadable") from exc
    finally:
        os.close(directory_fd)


def _safe_gitlink_identity(repo: Path, relative_path: str) -> str:
    path = (repo / relative_path).resolve()
    if not path.is_relative_to(repo) or not path.is_dir():
        raise ScopeError(f"tracked gitlink is not a contained directory: {relative_path}")
    try:
        worktree_root = _repo_root(path)
    except ScopeError as exc:
        raise ScopeError(
            f"tracked gitlink worktree is not safely contained: {relative_path}: {exc}"
        ) from exc
    if worktree_root != path:
        raise ScopeError(
            f"tracked gitlink worktree does not match its contained path: {relative_path}"
        )
    git_dir = Path(_git(path, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()
    if not git_dir.is_relative_to(repo):
        raise ScopeError(f"tracked gitlink metadata escapes repository: {relative_path}")
    common_dir_value = Path(
        _git(path, "rev-parse", "--git-common-dir").decode().strip()
    )
    common_dir = (
        common_dir_value.resolve()
        if common_dir_value.is_absolute()
        else (path / common_dir_value).resolve()
    )
    if not common_dir.is_relative_to(repo):
        raise ScopeError(
            f"tracked gitlink common metadata escapes repository: {relative_path}"
        )
    head = _resolve_commit(path, "HEAD")
    status = _git(path, "status", "--porcelain=v2", "-z", "--untracked-files=no")
    payload = head.encode() + b"\0" + status
    return f"gitlink:sha256:{hashlib.sha256(payload).hexdigest()}"


def _safe_worktree_identity(repo: Path, relative_path: str, mode: str) -> str:
    if mode == "120000":
        payload = _read_contained_link(repo, relative_path).encode(
            "utf-8", "surrogateescape"
        )
    elif mode == "160000":
        return _safe_gitlink_identity(repo, relative_path)
    else:
        payload = _read_contained_regular(repo, relative_path)
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _link_target_from_blob(repo: Path, oid: str) -> str | None:
    if ZERO_OID_RE.match(oid):
        return None
    return _git(repo, "cat-file", "blob", oid).decode("utf-8", "surrogateescape")


def _worktree_link_target(repo: Path, relative_path: str) -> str | None:
    return _read_contained_link(repo, relative_path)


def _typed_record(
    repo: Path, raw: dict[str, str | None], *, source: str, diff_args: list[str]
) -> dict[str, Any]:
    status = str(raw["status"])
    old_mode = str(raw["old_mode"])
    new_mode = str(raw["new_mode"])
    old_path = raw["old_path"]
    new_path = raw["new_path"]
    path = str(new_path or old_path)
    old_oid = str(raw["old_oid"])
    new_oid = str(raw["new_oid"])
    binary = _tracked_record_is_binary(repo, raw, source=source)

    if "160000" in {old_mode, new_mode}:
        change_type = "gitlink"
    elif "120000" in {old_mode, new_mode}:
        change_type = "symlink"
    elif status in {"R", "C"} and old_oid == new_oid:
        change_type = "rename_only"
    elif status == "D":
        change_type = "binary" if binary else "deletion"
    elif (
        status not in {"A", "D"}
        and old_mode != new_mode
        and not _has_content_change(repo, diff_args, path)
    ):
        change_type = "file_mode"
    elif binary:
        change_type = "binary"
    else:
        change_type = "text"

    if change_type in {"text", "deletion"}:
        anchor = _line_anchor(repo, diff_args, old_path, new_path)
        if change_type == "deletion" and anchor.get("kind") == "line":
            anchor["side"] = "old"
            anchor["path"] = old_path
        elif change_type == "deletion":
            anchor = {
                "kind": "change",
                "change_type": "deletion",
                "path": old_path,
            }
    else:
        anchor = {
            "kind": "change",
            "change_type": change_type,
            "path": path,
            "old_path": old_path,
            "new_path": new_path,
            "old_mode": old_mode,
            "new_mode": new_mode,
            "old_oid": old_oid,
            "new_oid": new_oid,
        }

    identity_parts = [source, status, old_path or "", new_path or "", old_oid, new_oid]
    content_identity = new_oid
    if source == "unstaged" and new_path and new_mode == "160000":
        content_identity = _safe_gitlink_identity(repo, new_path)
    elif source == "unstaged" and new_path and ZERO_OID_RE.match(new_oid):
        content_identity = _safe_worktree_identity(repo, new_path, new_mode)
    identity_parts.append(content_identity)
    record_id = "record-" + hashlib.sha256(
        "\0".join(identity_parts).encode("utf-8", "surrogateescape")
    ).hexdigest()[:20]
    record = {
        "record_id": record_id,
        "source": source,
        "change_type": change_type,
        "status": status,
        "old_path": old_path,
        "new_path": new_path,
        "old_mode": old_mode,
        "new_mode": new_mode,
        "old_oid": old_oid,
        "new_oid": new_oid,
        "content_identity": content_identity,
        "anchor": anchor,
    }
    if change_type == "symlink":
        record["old_link_target"] = _link_target_from_blob(repo, old_oid)
        if source == "unstaged" and new_path and ZERO_OID_RE.match(new_oid):
            record["new_link_target"] = _worktree_link_target(repo, new_path)
        else:
            record["new_link_target"] = _link_target_from_blob(repo, new_oid)
    return record


def _split_text_record_by_hunk(
    repo: Path,
    record: dict[str, Any],
    *,
    diff_args: list[str],
) -> list[dict[str, Any]]:
    if record["change_type"] not in {"text", "deletion"}:
        return [record]
    hunks = _diff_hunks(
        repo,
        diff_args,
        record["old_path"],
        record["new_path"],
    )
    if not hunks:
        return [record]
    split_records: list[dict[str, Any]] = []
    for hunk in hunks:
        anchor = hunk["anchor"]
        if record["change_type"] == "deletion" and anchor["kind"] == "line":
            anchor = {**anchor, "side": "old", "path": record["old_path"]}
        identity = json.dumps(
            {"record_id": record["record_id"], **hunk},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        split_records.append(
            {
                **record,
                "record_id": "record-" + hashlib.sha256(identity).hexdigest()[:20],
                "anchor": anchor,
                "region": hunk["region"],
            }
        )
    return split_records


def _tracked_records(repo: Path, source: str, diff_args: list[str]) -> list[dict[str, Any]]:
    raw = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "--raw",
        "-z",
        "--no-abbrev",
        "--find-renames",
        f"-l{RENAME_LIMIT}",
        *diff_args,
        reject_incomplete_renames=True,
    )
    records: list[dict[str, Any]] = []
    for value in _parse_raw(raw):
        record = _typed_record(repo, value, source=source, diff_args=diff_args)
        records.extend(
            _split_text_record_by_hunk(repo, record, diff_args=diff_args)
        )
    return records


def _ignored_paths(repo: Path, relative_paths: list[str]) -> set[str]:
    if not relative_paths:
        return set()
    payload = b"\0".join(
        ("./" + path).encode("utf-8", "surrogateescape") for path in relative_paths
    ) + b"\0"
    return {
        value.decode("utf-8", "surrogateescape").removeprefix("./")
        for value in _git(
            repo,
            "check-ignore",
            "-z",
            "--stdin",
            input_data=payload,
            accepted_returncodes=(0, 1),
        ).split(b"\0")
        if value
    }


def _gitlink_paths(payload: bytes) -> set[str]:
    paths: set[str] = set()
    for raw_entry in payload.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        if separator and metadata.startswith(b"160000 "):
            paths.add(raw_path.decode("utf-8", "surrogateescape"))
    return paths


def _untracked_records(
    repo: Path,
    max_bytes: int,
    *,
    entry_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if entry_limit is None:
        entry_limit = SNAPSHOT_WORKTREE_ENTRY_LIMIT
    entry_limit = validate_positive_integer(
        entry_limit, "snapshot worktree entry limit"
    )
    records: list[dict[str, Any]] = []
    zero_oid = _zero_oid(repo)
    exclusions: list[dict[str, str]] = []
    tracked_paths = {
        value.decode("utf-8", "surrogateescape")
        for value in _git(repo, "ls-files", "-z").split(b"\0")
        if value
    }
    if len(tracked_paths) > entry_limit:
        raise SnapshotBudgetExceeded(
            "worktree_entries", entry_limit, len(tracked_paths)
        )
    tracked_prefixes = {
        "/".join(Path(path).parts[:index])
        for path in tracked_paths
        for index in range(1, len(Path(path).parts))
    }
    gitlink_paths = _gitlink_paths(_git(repo, "ls-files", "--stage", "-z"))
    entry_count = 0
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )

    def add_regular(relative_path: str) -> None:
        path_parts = set(Path(relative_path).parts)
        if _likely_secret_path(relative_path):
            exclusions.append({"path": relative_path, "reason": "likely-secret"})
            return
        if path_parts & VENDORED_PARTS:
            exclusions.append({"path": relative_path, "reason": "vendored"})
            return
        if path_parts & GENERATED_PARTS:
            exclusions.append({"path": relative_path, "reason": "generated"})
            return
        try:
            payload = _read_contained_regular(
                repo, relative_path, max_bytes=max_bytes
            )
        except ContainedPathError as exc:
            exclusions.append({"path": relative_path, "reason": exc.reason})
            return
        digest = hashlib.sha256(payload).hexdigest()
        binary = b"\0" in payload[:8192]
        change_type = "binary" if binary else "text"
        record_id = "record-" + hashlib.sha256(
            f"untracked\0{relative_path}\0{digest}".encode(
                "utf-8", "surrogateescape"
            )
        ).hexdigest()[:20]
        if binary:
            anchor = {
                "kind": "change",
                "change_type": "binary",
                "path": relative_path,
            }
        elif payload:
            anchor = {"kind": "line", "side": "new", "path": relative_path, "line": 1}
        else:
            anchor = {
                "kind": "change",
                "change_type": "text",
                "path": relative_path,
            }
        records.append(
            {
                "record_id": record_id,
                "source": "untracked",
                "change_type": change_type,
                "status": "A",
                "old_path": None,
                "new_path": relative_path,
                "old_mode": "000000",
                "new_mode": "100644",
                "old_oid": zero_oid,
                "new_oid": zero_oid,
                "content_identity": f"sha256:{digest}",
                "anchor": anchor,
            }
        )

    def visit(directory: Path, prefix: str) -> None:
        nonlocal entry_count
        directory_fd: int | None = None
        try:
            directory_fd = os.open(directory, directory_flags)
            if prefix and _nested_repository_boundary_fd(directory_fd, directory):
                exclusions.append({"path": prefix, "reason": "nested-repository"})
                return
            with os.scandir(directory_fd) as scanner:
                entries = []
                for entry in scanner:
                    if not prefix and entry.name == ".git":
                        continue
                    entry_count += 1
                    if entry_count > entry_limit:
                        raise SnapshotBudgetExceeded(
                            "worktree_entries", entry_limit, entry_count
                        )
                    relative_path = (
                        f"{prefix}/{entry.name}" if prefix else entry.name
                    )
                    try:
                        mode = entry.stat(follow_symlinks=False).st_mode
                    except OSError:
                        mode = 0
                    entries.append((entry.name, relative_path, mode))
        except SnapshotBudgetExceeded:
            raise
        except OSError as exc:
            location = prefix or "."
            raise ScopeError(
                f"local scope could not list worktree directory {location}: {exc}"
            ) from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

        entries.sort(key=lambda item: os.fsencode(item[1]))
        ignored = _ignored_paths(repo, [relative for _name, relative, _mode in entries])
        for name, relative_path, mode in entries:
            if stat.S_ISDIR(mode):
                if relative_path in gitlink_paths:
                    continue
                prune_reason = None
                if relative_path in ignored:
                    prune_reason = "ignored"
                elif name in VENDORED_PARTS:
                    prune_reason = "vendored"
                elif name in GENERATED_PARTS:
                    prune_reason = "generated"
                if prune_reason is not None and relative_path not in tracked_prefixes:
                    exclusions.append(
                        {"path": relative_path, "reason": prune_reason}
                    )
                    continue
                visit(directory / name, relative_path)
                continue
            if relative_path in tracked_paths:
                continue
            if relative_path in ignored:
                exclusions.append({"path": relative_path, "reason": "ignored"})
            elif stat.S_ISREG(mode):
                add_regular(relative_path)
            else:
                exclusions.append(
                    {"path": relative_path, "reason": "special-or-symlink"}
                )

    visit(repo, "")
    exclusions.sort(key=lambda item: (item["path"], item["reason"]))
    return records, exclusions


def resolve_local(
    repo_value: Path,
    max_untracked_bytes: int,
    *,
    snapshot_worktree_entry_limit: int | None = None,
) -> dict[str, Any]:
    _require_posix()
    max_untracked_bytes = validate_positive_integer(
        max_untracked_bytes, "max_untracked_bytes"
    )
    repo = _repo_root(repo_value)
    unmerged_entries = {
        raw_entry.partition(b"\t")[2].decode("utf-8", "surrogateescape")
        for raw_entry in _git(repo, "ls-files", "--unmerged", "-z").split(b"\0")
        if raw_entry
    }
    if unmerged_entries:
        raise ScopeError(
            "local scope contains an unmerged index: "
            + ", ".join(sorted(unmerged_entries))
        )
    hidden_entries: list[str] = []
    for raw_entry in _git(repo, "ls-files", "-v", "-z").split(b"\0"):
        if not raw_entry:
            continue
        tag = chr(raw_entry[0])
        if tag.islower() or tag == "S":
            hidden_entries.append(raw_entry[2:].decode("utf-8", "surrogateescape"))
    if hidden_entries:
        raise ScopeError(
            "local scope contains tracked paths with hidden index flags: "
            + ", ".join(sorted(hidden_entries))
        )
    head = _head_or_empty(repo)
    empty_tree = _empty_tree(repo)
    staged_args = ["--cached"] if head != empty_tree else ["--cached", empty_tree]
    records = _tracked_records(repo, "staged", staged_args)
    records.extend(_tracked_records(repo, "unstaged", []))
    untracked, exclusions = _untracked_records(
        repo,
        max_untracked_bytes,
        entry_limit=snapshot_worktree_entry_limit,
    )
    records.extend(untracked)
    records.sort(key=lambda record: (record["source"], record["new_path"] or record["old_path"] or ""))
    identity_payload = {
        "target": {"kind": "local", "base_oid": head, "head": "WORKTREE"},
        "changed_records": records,
        "exclusions": exclusions,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": f"local:sha256:{fingerprint}",
        **identity_payload,
        "trusted_requirement_sources": [],
        "candidate_validation_commands": [],
    }
    validate_contract("scope_manifest", manifest)
    return manifest


def _immutable_manifest(
    repo: Path,
    *,
    kind: str,
    source: str,
    base_oid: str,
    head_oid: str,
    requested: str,
    semantics: str,
    extra_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = _tracked_records(repo, source, [base_oid, head_oid])
    target = {
        "kind": kind,
        "requested": requested,
        "base_oid": base_oid,
        "head_oid": head_oid,
        "semantics": semantics,
        **(extra_target or {}),
    }
    identity = hashlib.sha256(
        json.dumps(target, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": f"{kind}:sha256:{identity}",
        "target": target,
        "changed_records": records,
        "exclusions": [],
        "trusted_requirement_sources": [],
        "candidate_validation_commands": [],
    }
    validate_contract("scope_manifest", manifest)
    return manifest


def resolve_commit(repo_value: Path, revision: str, parent: str | None) -> dict[str, Any]:
    _require_posix()
    repo = _repo_root(repo_value)
    head_oid = _resolve_commit(repo, revision)
    parent_line = _git(repo, "show", "-s", "--format=%P", head_oid).decode().strip()
    parents = parent_line.split() if parent_line else []
    if not parents:
        if parent is not None:
            raise ScopeError("a root commit has no selectable parent")
        base_oid = _empty_tree(repo)
        semantics = "empty-tree"
    elif len(parents) == 1:
        if parent is not None:
            selected = _resolve_commit(repo, parent)
            if selected != parents[0]:
                raise ScopeError("the selected parent is not a parent of the commit")
        base_oid = parents[0]
        semantics = "single-parent"
    else:
        if parent is None:
            raise ScopeError(
                "merge commit has multiple parents; select one explicitly with --parent"
            )
        if parent.isdigit():
            position = int(parent)
            if position < 1 or position > len(parents):
                raise ScopeError(f"parent number must be between 1 and {len(parents)}")
            base_oid = parents[position - 1]
        else:
            base_oid = _resolve_commit(repo, parent)
            if base_oid not in parents:
                raise ScopeError("the selected parent is not a parent of the commit")
        semantics = "explicit-parent"
    return _immutable_manifest(
        repo,
        kind="commit",
        source="commit",
        base_oid=base_oid,
        head_oid=head_oid,
        requested=revision,
        semantics=semantics,
        extra_target={"parent_count": len(parents)},
    )


def resolve_range(repo_value: Path, expression: str) -> dict[str, Any]:
    _require_posix()
    repo = _repo_root(repo_value)
    match = re.fullmatch(r"(.+?)(\.\.\.?)(.+)", expression)
    if match is None:
        raise ScopeError("explicit range must contain exactly two endpoints joined by .. or ...")
    left, operator, right = match.groups()
    requested_base_oid = _resolve_commit(repo, left)
    head_oid = _resolve_commit(repo, right)
    if operator == "...":
        base_oid = _unique_merge_base(repo, requested_base_oid, head_oid)
        semantics = "explicit-three-dot"
    else:
        base_oid = requested_base_oid
        semantics = "explicit-two-dot"
    return _immutable_manifest(
        repo,
        kind="range",
        source="range",
        base_oid=base_oid,
        head_oid=head_oid,
        requested=expression,
        semantics=semantics,
        extra_target={
            "operator": operator,
            "requested_base_oid": requested_base_oid,
        },
    )


def _unique_merge_base(repo: Path, left_oid: str, right_oid: str) -> str:
    candidates = _git(repo, "merge-base", "--all", left_oid, right_oid).decode().splitlines()
    if len(candidates) != 1:
        raise ScopeError(
            "target has multiple best merge bases; select an unambiguous explicit range"
        )
    return candidates[0]


def resolve_merge_base_target(
    repo_value: Path,
    *,
    kind: str,
    source: str,
    base: str,
    head: str,
    requested: str,
    extra_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_posix()
    normalized_extra_target = dict(extra_target or {})
    if kind == "pull_request":
        normalized_extra_target["pull_request_number"] = validate_positive_integer(
            normalized_extra_target.get("pull_request_number"),
            "pull_request_number",
        )
    repo = _repo_root(repo_value)
    requested_base_oid = _resolve_commit(repo, base)
    head_oid = _resolve_commit(repo, head)
    base_oid = _unique_merge_base(repo, requested_base_oid, head_oid)
    return _immutable_manifest(
        repo,
        kind=kind,
        source=source,
        base_oid=base_oid,
        head_oid=head_oid,
        requested=requested,
        semantics="merge-base",
        extra_target={
            "requested_base": base,
            "requested_head": head,
            "requested_base_oid": requested_base_oid,
            **normalized_extra_target,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    local = subparsers.add_parser("local", help="resolve staged, unstaged, and safe untracked changes")
    local.add_argument("--repo", type=Path, default=Path.cwd())
    local.add_argument(
        "--max-untracked-bytes",
        type=_positive_integer_argument,
        default=1024 * 1024,
    )
    commit = subparsers.add_parser("commit", help="resolve one commit against its selected parent")
    commit.add_argument("revision")
    commit.add_argument("--parent")
    commit.add_argument("--repo", type=Path, default=Path.cwd())
    range_parser = subparsers.add_parser("range", help="resolve an explicit two- or three-dot range")
    range_parser.add_argument("expression")
    range_parser.add_argument("--repo", type=Path, default=Path.cwd())
    branch = subparsers.add_parser("branch", help="resolve a branch against a base merge point")
    branch.add_argument("--base", required=True)
    branch.add_argument("--head", required=True)
    branch.add_argument("--repo", type=Path, default=Path.cwd())
    pull_request = subparsers.add_parser(
        "pr", help="freeze locally available refs retrieved for an explicit pull request"
    )
    pull_request.add_argument(
        "--number", required=True, type=_positive_integer_argument
    )
    pull_request.add_argument("--base", required=True)
    pull_request.add_argument("--head", required=True)
    pull_request.add_argument("--repo", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "local":
            output = resolve_local(args.repo, args.max_untracked_bytes)
        elif args.command == "commit":
            output = resolve_commit(args.repo, args.revision, args.parent)
        elif args.command == "range":
            output = resolve_range(args.repo, args.expression)
        elif args.command == "branch":
            output = resolve_merge_base_target(
                args.repo,
                kind="branch",
                source="branch",
                base=args.base,
                head=args.head,
                requested=f"{args.base}...{args.head}",
            )
        elif args.command == "pr":
            output = resolve_merge_base_target(
                args.repo,
                kind="pull_request",
                source="pull_request",
                base=args.base,
                head=args.head,
                requested=f"PR #{args.number}",
                extra_target={"pull_request_number": args.number},
            )
        else:
            raise ScopeError(f"unsupported command: {args.command}")
    except (ScopeError, ContractError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
