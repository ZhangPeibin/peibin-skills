#!/usr/bin/env python3
"""State-backed fake GitHub CLI used at the tickets process boundary."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    return Path(os.environ["FAKE_GH_STATE"])


def _load() -> dict[str, Any]:
    return json.loads(_state_path().read_text(encoding="utf-8"))


def _save(state: dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(state), encoding="utf-8")


def _emit(value: object) -> None:
    print(json.dumps(value, separators=(",", ":")))


def _emit_api(state: dict[str, Any], arguments: list[str], value: object) -> None:
    _emit([value] if "--slurp" in arguments and state.get("slurp_pages") else value)


def _argument(arguments: list[str], name: str) -> str | None:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        return None


def _issue(state: dict[str, Any], number: int) -> dict[str, Any]:
    return next(issue for issue in state["issues"] if issue["number"] == number)


def main() -> int:
    arguments = sys.argv[1:]
    state = _load()
    for failure in state.get("failures", []):
        required = failure.get("arguments", [])
        if failure.get("remaining", 0) > 0 and all(
            argument in arguments for argument in required
        ):
            failure["remaining"] -= 1
            _save(state)
            print(failure.get("message", "injected gh failure"), file=sys.stderr)
            return int(failure.get("exit_code", 1))
    if arguments[:2] == ["repo", "view"]:
        _emit({"nameWithOwner": state["repository"]})
        return 0
    if arguments[:2] == ["api", "user"]:
        _emit({"login": state["login"]})
        return 0
    if arguments[:2] == ["api", "graphql"]:
        payload = {
            "data": {
                "repository": {
                    "issues": {
                        "nodes": [
                            {
                                "number": issue["number"],
                                "state": issue["state"],
                                "parent": (
                                    {"number": issue["native_parent"]}
                                    if issue.get("native_parent") is not None
                                    else None
                                ),
                            }
                            for issue in state["issues"]
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
        _emit([payload] if "--slurp" in arguments and state.get("slurp_pages") else payload)
        return 0
    if arguments[:2] == ["issue", "edit"]:
        issue = _issue(state, int(arguments[2]))
        if "--add-assignee" in arguments:
            value = _argument(arguments, "--add-assignee")
            login = state["login"] if value == "@me" else value
            if login not in [assignee["login"] for assignee in issue["assignees"]]:
                issue["assignees"].append({"login": login})
            concurrent = state.pop("claim_on_add", None)
            if isinstance(concurrent, dict):
                other = _issue(state, int(concurrent["number"]))
                other_login = str(concurrent.get("login") or "other-user")
                if other_login not in [
                    assignee["login"] for assignee in other["assignees"]
                ]:
                    other["assignees"].append({"login": other_login})
        if "--remove-assignee" in arguments:
            value = _argument(arguments, "--remove-assignee")
            login = state["login"] if value == "@me" else value
            issue["assignees"] = [
                assignee
                for assignee in issue["assignees"]
                if assignee["login"] != login
            ]
        _save(state)
        return 0
    if arguments[:2] == ["issue", "comment"]:
        issue = _issue(state, int(arguments[2]))
        comments = issue.setdefault("comments", [])
        comments.append(
            {
                "id": len(comments) + 1,
                "body": _argument(arguments, "--body") or "",
                "user": {"login": state["login"]},
            }
        )
        _save(state)
        return 0
    if arguments[:2] == ["issue", "close"]:
        _issue(state, int(arguments[2]))["state"] = "CLOSED"
        _save(state)
        return 0
    if arguments and arguments[0] == "api":
        path = next(
            (argument for argument in arguments[1:] if not argument.startswith("-")),
            "",
        )
        parts = path.split("?", 1)[0].strip("/").split("/")
        if len(parts) == 4 and parts[-1] == "issues":
            _emit_api(state, arguments, state["issues"])
            return 0
        if len(parts) >= 6 and parts[-3] == "collaborators" and parts[-1] == "permission":
            login = parts[-2]
            permission = state.get("permissions", {}).get(login, "read")
            _emit({"permission": permission, "user": {"login": login}})
            return 0
        if parts[-1:] == ["sub_issues"]:
            parent = int(parts[-2])
            _emit_api(
                state,
                arguments,
                [
                    issue
                    for issue in state["issues"]
                    if issue.get("native_parent") == parent
                ],
            )
            return 0
        if parts[-2:] == ["dependencies", "blocked_by"]:
            number = int(parts[-3])
            issue = _issue(state, number)
            blockers = [
                _issue(state, blocker)
                for blocker in issue.get("native_blocked_by", [])
            ]
            _emit_api(state, arguments, blockers)
            return 0
        if parts[-1:] == ["comments"]:
            number = int(parts[-2])
            _emit_api(state, arguments, _issue(state, number).get("comments", []))
            return 0
    print(
        f"fake gh does not support: {' '.join(arguments)}",
        file=sys.stderr,
    )
    return 9


if __name__ == "__main__":
    raise SystemExit(main())
