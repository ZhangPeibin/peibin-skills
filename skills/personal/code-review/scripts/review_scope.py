#!/usr/bin/env python3
"""Resolve a Code Review v2 scope without mutating the reviewed repository."""

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
from pathlib import Path
from typing import Any

from review_contract import SCHEMA_VERSION, ContractError, validate_contract


ZERO_OID_RE = re.compile(r"^0+$")
SECRET_NAMES = {
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
SECRET_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
VENDORED_PARTS = {"node_modules", "third_party", "vendor", "vendors"}
GENERATED_PARTS = {"build", "coverage", "dist", "generated"}


class ScopeError(ValueError):
    pass


class ContainedPathError(ScopeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ScopeError(message or f"git {' '.join(args)} failed")
    return result.stdout


def _repo_root(repo: Path) -> Path:
    root = _git(repo, "rev-parse", "--show-toplevel").decode().strip()
    return Path(root).resolve()


def _head_or_empty(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_git_environment(),
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else _empty_tree(repo)


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


def _diff_bytes(repo: Path, args: list[str], path: str, *, patch: bool) -> bytes:
    option = "--unified=0" if patch else "--numstat"
    return _git(
        repo,
        "--literal-pathspecs",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        option,
        *args,
        "--",
        path,
    )


def _is_binary(repo: Path, args: list[str], path: str) -> bool:
    first_line = _diff_bytes(repo, args, path, patch=False).splitlines()[:1]
    return bool(first_line and first_line[0].startswith(b"-\t-\t"))


def _has_content_change(repo: Path, args: list[str], path: str) -> bool:
    patch = _diff_bytes(repo, args, path, patch=True)
    return bool(re.search(br"^@@ ", patch, re.MULTILINE))


def _line_anchor(repo: Path, args: list[str], path: str) -> dict[str, Any]:
    patch = _diff_bytes(repo, args, path, patch=True).decode("utf-8", "replace")
    match = re.search(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", patch, re.MULTILINE)
    if match is None:
        return {"kind": "change", "path": path, "change_type": "text"}
    old_line, new_line = int(match.group(1)), int(match.group(2))
    hunk_lines: list[str] = []
    for line in patch[match.end() :].splitlines():
        if line.startswith("@@ "):
            break
        hunk_lines.append(line)
    if any(line.startswith("+") and not line.startswith("+++") for line in hunk_lines):
        return {"kind": "line", "side": "new", "path": path, "line": new_line}
    if any(line.startswith("-") and not line.startswith("---") for line in hunk_lines):
        return {"kind": "line", "side": "old", "path": path, "line": old_line}
    return {"kind": "change", "path": path, "change_type": "text"}


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
    repo: Path, relative_path: str, *, max_bytes: int | None = None
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
            return stream.read()
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
    git_dir = Path(_git(path, "rev-parse", "--absolute-git-dir").decode().strip()).resolve()
    if not git_dir.is_relative_to(repo):
        raise ScopeError(f"tracked gitlink metadata escapes repository: {relative_path}")
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
    return _git(repo, "cat-file", "blob", oid).decode("utf-8", "replace")


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

    if "160000" in {old_mode, new_mode}:
        change_type = "gitlink"
    elif "120000" in {old_mode, new_mode}:
        change_type = "symlink"
    elif status in {"R", "C"} and old_oid == new_oid:
        change_type = "rename_only"
    elif status == "D":
        change_type = "binary" if _is_binary(repo, diff_args, path) else "deletion"
    elif (
        status not in {"A", "D"}
        and old_mode != new_mode
        and not _has_content_change(repo, diff_args, path)
    ):
        change_type = "file_mode"
    elif _is_binary(repo, diff_args, path):
        change_type = "binary"
    else:
        change_type = "text"

    if change_type in {"text", "deletion"}:
        anchor = _line_anchor(repo, diff_args, path)
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
    if source == "unstaged" and new_path and ZERO_OID_RE.match(new_oid):
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


def _tracked_records(repo: Path, source: str, diff_args: list[str]) -> list[dict[str, Any]]:
    raw = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--raw",
        "-z",
        "--no-abbrev",
        "--find-renames",
        *diff_args,
    )
    return [
        _typed_record(repo, value, source=source, diff_args=diff_args)
        for value in _parse_raw(raw)
    ]


def _untracked_records(repo: Path, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    raw_paths = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    candidates = [
        value.decode("utf-8", "surrogateescape")
        for value in raw_paths.split(b"\0")
        if value
    ]
    records: list[dict[str, Any]] = []
    zero_oid = _zero_oid(repo)
    ignored_paths = _git(
        repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"
    )
    exclusions: list[dict[str, str]] = [
        {"path": value.decode("utf-8", "surrogateescape"), "reason": "ignored"}
        for value in ignored_paths.split(b"\0")
        if value
    ]
    for relative_path in sorted(candidates):
        path = repo / relative_path
        path_parts = set(Path(relative_path).parts)
        lower_name = path.name.lower()
        if (
            lower_name in SECRET_NAMES
            or lower_name.startswith(".env.")
            or path.suffix.lower() in SECRET_SUFFIXES
        ):
            exclusions.append({"path": relative_path, "reason": "likely-secret"})
            continue
        if path_parts & VENDORED_PARTS:
            exclusions.append({"path": relative_path, "reason": "vendored"})
            continue
        if path_parts & GENERATED_PARTS:
            exclusions.append({"path": relative_path, "reason": "generated"})
            continue
        try:
            payload = _read_contained_regular(
                repo, relative_path, max_bytes=max_bytes
            )
        except ContainedPathError as exc:
            exclusions.append({"path": relative_path, "reason": exc.reason})
            continue
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
    known_paths = {item["path"] for item in exclusions} | set(candidates)
    tracked_paths = {
        value.decode("utf-8", "surrogateescape")
        for value in _git(repo, "ls-files", "-z").split(b"\0")
        if value
    }
    pending_directories = [repo]
    while pending_directories:
        directory = pending_directories.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if directory == repo and entry.name == ".git":
                continue
            entry_path = Path(entry.path)
            relative_path = entry_path.relative_to(repo).as_posix()
            if entry.is_dir(follow_symlinks=False):
                pending_directories.append(entry_path)
                continue
            if relative_path in tracked_paths or relative_path in known_paths:
                continue
            try:
                entry_mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                entry_mode = 0
            if not stat.S_ISREG(entry_mode):
                exclusions.append(
                    {"path": relative_path, "reason": "special-or-symlink"}
                )
    exclusions.sort(key=lambda item: (item["path"], item["reason"]))
    return records, exclusions


def resolve_local(repo_value: Path, max_untracked_bytes: int) -> dict[str, Any]:
    repo = _repo_root(repo_value)
    head = _head_or_empty(repo)
    empty_tree = _empty_tree(repo)
    staged_args = ["--cached"] if head != empty_tree else ["--cached", empty_tree]
    records = _tracked_records(repo, "staged", staged_args)
    records.extend(_tracked_records(repo, "unstaged", []))
    untracked, exclusions = _untracked_records(repo, max_untracked_bytes)
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
            **(extra_target or {}),
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    local = subparsers.add_parser("local", help="resolve staged, unstaged, and safe untracked changes")
    local.add_argument("--repo", type=Path, default=Path.cwd())
    local.add_argument("--max-untracked-bytes", type=int, default=1024 * 1024)
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
    pull_request.add_argument("--number", required=True, type=int)
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
    except (ScopeError, ContractError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
