#!/usr/bin/env python3
"""GitHub issue source and state store for the tickets controller."""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from pathlib import Path
from typing import Any

STATE_START = "<!-- run-ticket-plan:state:start -->"
STATE_END = "<!-- run-ticket-plan:state:end -->"
PARENT_SECTION_RE = re.compile(
    r"^##[ \t]+Parent[ \t]*$\s*(.*?)(?=^##[ \t]|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
MANAGE_ISSUE_PERMISSIONS = {"admin", "maintain", "write", "triage"}
NATIVE_PARENT_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor) {
      nodes { number state parent { number } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()


class GitHubStoreError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "ENVIRONMENT_ERROR",
        details: dict[str, Any] | None = None,
        changed: bool = False,
    ):
        super().__init__(message)
        self.kind = kind
        self.details = details or {}
        self.changed = changed


@dataclasses.dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    body: str
    state: str
    url: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    blockers: tuple[int, ...]
    state_text: str | None


@dataclasses.dataclass(frozen=True)
class GitHubFeature:
    repository: str
    feature_number: int
    current_login: str
    issues: tuple[GitHubIssue, ...]


def _parent_number(body: str) -> int | None:
    section = PARENT_SECTION_RE.search(body)
    if section is None:
        return None
    reference = re.search(r"(?<!\w)#(\d+)\b", section.group(1))
    return int(reference.group(1)) if reference is not None else None


class GitHubTicketStore:
    def __init__(
        self,
        repo_root: Path,
        *,
        repository: str | None = None,
        feature_number: str | None = None,
    ):
        self.repo_root = repo_root.expanduser().resolve()
        self.repository = repository
        self.feature_number = feature_number
        self.current_login: str | None = None
        self.issues: dict[int, GitHubIssue] = {}
        self._permission_cache: dict[str, bool] = {}

    def _run(self, arguments: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["gh", *arguments],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise GitHubStoreError(
                "GitHub CLI operation failed",
                details={"type": type(exc).__name__, "message": str(exc)},
            ) from exc
        if completed.returncode != 0:
            raise GitHubStoreError(
                "GitHub CLI operation failed",
                details={
                    "arguments": arguments,
                    "exit_code": completed.returncode,
                    "stderr": completed.stderr.strip(),
                },
            )
        return completed.stdout

    def _json(self, arguments: list[str]) -> Any:
        output = self._run(arguments)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise GitHubStoreError(
                "GitHub CLI returned invalid JSON",
                details={"arguments": arguments, "message": str(exc)},
            ) from exc

    def _optional_json(self, arguments: list[str]) -> Any | None:
        try:
            return self._json(arguments)
        except GitHubStoreError as exc:
            message = str(exc.details.get("stderr") or "").lower()
            if "404" in message or "not found" in message:
                return None
            raise

    def _resolve_repository(self) -> str:
        if self.repository:
            return self.repository
        payload = self._json(["repo", "view", "--json", "nameWithOwner"])
        repository = payload.get("nameWithOwner") if isinstance(payload, dict) else None
        if not isinstance(repository, str) or not repository:
            raise GitHubStoreError("GitHub CLI did not identify a repository")
        return repository

    @staticmethod
    def _records(payload: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            raise GitHubStoreError(f"{label} returned an invalid payload")
        values: list[Any] = (
            [item for page in payload for item in page]
            if payload and all(isinstance(page, list) for page in payload)
            else payload
        )
        if any(not isinstance(value, dict) for value in values):
            raise GitHubStoreError(f"{label} returned an invalid payload")
        return values

    def _resolve_login(self) -> str:
        payload = self._json(["api", "user"])
        login = payload.get("login") if isinstance(payload, dict) else None
        if not isinstance(login, str) or not login:
            raise GitHubStoreError("GitHub CLI did not identify the current user")
        return login

    def _list_issues(self, repository: str) -> list[dict[str, Any]]:
        payload = self._json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/issues?state=all&per_page=100",
            ]
        )
        records = self._records(payload, "GitHub issue list")
        issues = [issue for issue in records if "pull_request" not in issue]
        for issue in issues:
            if not issue.get("url") and isinstance(issue.get("html_url"), str):
                issue["url"] = issue["html_url"]
        return issues

    def _native_parent_links(self, repository: str) -> dict[int, int]:
        try:
            owner, name = repository.split("/", 1)
        except ValueError as exc:
            raise GitHubStoreError(
                "GitHub repository must use owner/name form", kind="USAGE_ERROR"
            ) from exc
        payload = self._json(
            [
                "api",
                "graphql",
                "--paginate",
                "--slurp",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-f",
                f"query={NATIVE_PARENT_QUERY}",
            ]
        )
        pages = payload if isinstance(payload, list) else [payload]
        links: dict[int, int] = {}
        for page in pages:
            try:
                nodes = page["data"]["repository"]["issues"]["nodes"]
            except (KeyError, TypeError) as exc:
                raise GitHubStoreError(
                    "GitHub native parent discovery returned an invalid payload"
                ) from exc
            if not isinstance(nodes, list):
                raise GitHubStoreError(
                    "GitHub native parent discovery returned an invalid payload"
                )
            for node in nodes:
                parent = node.get("parent") if isinstance(node, dict) else None
                number = node.get("number") if isinstance(node, dict) else None
                parent_number = parent.get("number") if isinstance(parent, dict) else None
                if isinstance(number, int) and isinstance(parent_number, int):
                    links[number] = parent_number
        return links

    def _can_manage_issues(self, repository: str, login: str) -> bool:
        cached = self._permission_cache.get(login)
        if cached is not None:
            return cached
        payload = self._optional_json(
            ["api", f"repos/{repository}/collaborators/{login}/permission"]
        )
        permission = payload.get("permission") if isinstance(payload, dict) else None
        allowed = isinstance(permission, str) and permission.lower() in MANAGE_ISSUE_PERMISSIONS
        self._permission_cache[login] = allowed
        return allowed

    def _body_parent_links(
        self, repository: str, issues: list[dict[str, Any]]
    ) -> dict[int, int]:
        links: dict[int, int] = {}
        for issue in issues:
            number = issue.get("number")
            parent = _parent_number(str(issue.get("body") or ""))
            author = issue.get("user")
            login = author.get("login") if isinstance(author, dict) else None
            if (
                isinstance(number, int)
                and parent is not None
                and isinstance(login, str)
                and self._can_manage_issues(repository, login)
            ):
                links[number] = parent
        return links

    def _resolve_feature(
        self, issues: list[dict[str, Any]], parent_by_child: dict[int, int]
    ) -> int:
        if self.feature_number is not None:
            try:
                feature = int(self.feature_number.removeprefix("#"))
            except ValueError as exc:
                raise GitHubStoreError(
                    "GitHub feature must be an issue number",
                    kind="USAGE_ERROR",
                ) from exc
            if not any(issue.get("number") == feature for issue in issues):
                raise GitHubStoreError(
                    f"unknown GitHub feature #{feature}", kind="UNKNOWN_FEATURE"
                )
            return feature

        by_number = {
            int(issue["number"]): issue
            for issue in issues
            if isinstance(issue.get("number"), int)
        }
        all_candidates = sorted(set(parent_by_child.values()))
        open_candidates = sorted(
            {
                parent
                for child, parent in parent_by_child.items()
                if child in by_number
                and str(by_number[child].get("state") or "OPEN").upper() != "CLOSED"
            }
        )
        candidates = open_candidates or all_candidates
        if len(candidates) != 1:
            raise GitHubStoreError(
                "GitHub feature is required unless exactly one parent DAG is discoverable",
                kind="FEATURE_REQUIRED",
                details={"candidates": candidates},
            )
        return candidates[0]

    def _native_children(self, repository: str, feature: int) -> set[int]:
        payload = self._optional_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/issues/{feature}/sub_issues",
            ]
        )
        if payload is None:
            return set()
        records = self._records(payload, "GitHub sub-issues")
        return {
            int(issue["number"])
            for issue in records
            if isinstance(issue.get("number"), int)
        }

    def _native_blockers(self, repository: str, number: int) -> tuple[int, ...]:
        payload = self._optional_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/issues/{number}/dependencies/blocked_by",
            ]
        )
        if payload is None:
            return ()
        records = self._records(payload, "GitHub dependencies")
        return tuple(
            int(issue["number"])
            for issue in records
            if isinstance(issue.get("number"), int)
        )

    def _state_text(
        self, repository: str, number: int, current_login: str
    ) -> str | None:
        payload = self._json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/issues/{number}/comments",
            ]
        )
        records = self._records(payload, "GitHub issue comments")
        state_comments: list[str] = []
        for comment in records:
            body = str(comment.get("body") or "")
            author = comment.get("user")
            login = author.get("login") if isinstance(author, dict) else None
            if (
                not isinstance(login, str)
                or STATE_START not in body
                or STATE_END not in body
                or not self._can_manage_issues(repository, login)
            ):
                continue
            state_comments.append(
                body[body.index(STATE_START) : body.index(STATE_END) + len(STATE_END)]
            )
        return state_comments[-1] if state_comments else None

    def load(self) -> GitHubFeature:
        repository = self._resolve_repository()
        current_login = self._resolve_login()
        raw_issues = self._list_issues(repository)
        body_parents = self._body_parent_links(repository, raw_issues)
        native_parents = self._native_parent_links(repository)
        parent_by_child = dict(body_parents)
        parent_by_child.update(native_parents)
        feature = self._resolve_feature(raw_issues, parent_by_child)
        native_children = self._native_children(repository, feature)
        child_numbers = native_children or {
            child for child, parent in parent_by_child.items() if parent == feature
        }
        if not child_numbers:
            raise GitHubStoreError(
                f"GitHub feature #{feature} has no tickets", kind="VALIDATION_ERROR"
            )

        issues: list[GitHubIssue] = []
        for raw in raw_issues:
            number = raw.get("number")
            if not isinstance(number, int) or number not in child_numbers:
                continue
            labels = raw.get("labels") if isinstance(raw.get("labels"), list) else []
            assignees = (
                raw.get("assignees") if isinstance(raw.get("assignees"), list) else []
            )
            issues.append(
                GitHubIssue(
                    number=number,
                    title=str(raw.get("title") or f"Issue {number}"),
                    body=str(raw.get("body") or ""),
                    state=str(raw.get("state") or "OPEN").upper(),
                    url=str(raw.get("url") or ""),
                    labels=tuple(
                        str(label["name"])
                        for label in labels
                        if isinstance(label, dict) and isinstance(label.get("name"), str)
                    ),
                    assignees=tuple(
                        str(assignee["login"])
                        for assignee in assignees
                        if isinstance(assignee, dict)
                        and isinstance(assignee.get("login"), str)
                    ),
                    blockers=self._native_blockers(repository, number),
                    state_text=self._state_text(repository, number, current_login),
                )
            )
        missing = sorted(child_numbers - {issue.number for issue in issues})
        if missing:
            raise GitHubStoreError(
                "GitHub sub-issues were missing from the issue list",
                kind="VALIDATION_ERROR",
                details={"issues": missing},
            )
        result = GitHubFeature(
            repository=repository,
            feature_number=feature,
            current_login=current_login,
            issues=tuple(sorted(issues, key=lambda issue: issue.number)),
        )
        self.current_login = current_login
        self.issues = {issue.number: issue for issue in result.issues}
        return result

    def persist(
        self,
        number: int,
        *,
        status: str,
        state_text: str | None = None,
        summary: str | None = None,
    ) -> bool:
        if self.repository is None or self.current_login is None:
            raise GitHubStoreError("GitHub ticket store must be loaded before writing")
        issue = self.issues.get(number)
        if issue is None:
            raise GitHubStoreError(
                f"unknown GitHub ticket #{number}", kind="UNKNOWN_TICKET"
            )

        changed = False
        try:
            if status == "in-progress" and self.current_login not in issue.assignees:
                self._run(
                    [
                        "issue",
                        "edit",
                        str(number),
                        "--repo",
                        self.repository,
                        "--add-assignee",
                        "@me",
                    ]
                )
                changed = True
                try:
                    refreshed = self.load()
                except GitHubStoreError as verification_error:
                    rollback_error: GitHubStoreError | None = None
                    try:
                        self._run(
                            [
                                "issue",
                                "edit",
                                str(number),
                                "--repo",
                                self.repository,
                                "--remove-assignee",
                                "@me",
                            ]
                        )
                    except GitHubStoreError as exc:
                        rollback_error = exc
                    raise GitHubStoreError(
                        "GitHub ticket claim could not be verified",
                        kind="CONCURRENT_MODIFICATION",
                        details={
                            "verification_error": verification_error.details,
                            "rolled_back": rollback_error is None,
                            "rollback_error": (
                                rollback_error.details if rollback_error is not None else None
                            ),
                        },
                        changed=True,
                    ) from verification_error
                active = [
                    candidate
                    for candidate in refreshed.issues
                    if candidate.state != "CLOSED" and candidate.assignees
                ]
                selected = self.issues.get(number)
                conflict = (
                    selected is None
                    or len(active) != 1
                    or active[0].number != number
                    or any(
                        assignee != self.current_login for assignee in selected.assignees
                    )
                )
                if conflict:
                    rollback_error: GitHubStoreError | None = None
                    try:
                        self._run(
                            [
                                "issue",
                                "edit",
                                str(number),
                                "--repo",
                                self.repository,
                                "--remove-assignee",
                                "@me",
                            ]
                        )
                    except GitHubStoreError as exc:
                        rollback_error = exc
                    raise GitHubStoreError(
                        "GitHub ticket claim conflicted with another active assignment",
                        kind="CONCURRENT_MODIFICATION",
                        details={
                            "active": [candidate.number for candidate in active],
                            "rolled_back": rollback_error is None,
                            "rollback_error": (
                                rollback_error.details if rollback_error is not None else None
                            ),
                        },
                        changed=True,
                    )

            if state_text is not None and issue.state_text != state_text:
                body = f"{summary}\n\n{state_text}" if summary else state_text
                self._run(
                    [
                        "issue",
                        "comment",
                        str(number),
                        "--repo",
                        self.repository,
                        "--body",
                        body,
                    ]
                )
                changed = True
            if status == "paused" and self.current_login in issue.assignees:
                self._run(
                    [
                        "issue",
                        "edit",
                        str(number),
                        "--repo",
                        self.repository,
                        "--remove-assignee",
                        "@me",
                    ]
                )
                changed = True
            elif status == "done" and issue.state != "CLOSED":
                self._run(
                    [
                        "issue",
                        "close",
                        str(number),
                        "--repo",
                        self.repository,
                    ]
                )
                changed = True
        except GitHubStoreError as exc:
            exc.changed = exc.changed or changed
            raise
        return changed
