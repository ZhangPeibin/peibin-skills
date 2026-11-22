#!/usr/bin/env python3
"""Small state controller for local Markdown ticket DAGs."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from github_ticket_store import GitHubStoreError, GitHubTicketStore

READY = "ready-for-agent"
IN_PROGRESS = "in-progress"
PAUSED = "paused"
DONE = "done"
OUTPUT_SCHEMA_VERSION = 2
GITHUB_OUTPUT_SCHEMA_VERSION = 3
CONFIRMATION_SUMMARY_MAX_CHARS = 1_000
CONFIRMATION_EVIDENCE_MAX_CHARS = 4_000
CONFIRMATION_CHECK_NAME_MAX_CHARS = 200
CONFIRMATION_SUBCHECKS_MAX = 20
ENVIRONMENT_ERRORS = (OSError, UnicodeError, subprocess.SubprocessError)

STATE_START = "<!-- run-ticket-plan:state:start -->"
STATE_END = "<!-- run-ticket-plan:state:end -->"
STATE_RE = re.compile(
    rf"{re.escape(STATE_START)}\s*```json\s*(.*?)\s*```\s*{re.escape(STATE_END)}",
    re.DOTALL | re.IGNORECASE,
)
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
TICKET_FILE_RE = re.compile(r"^(\d+)-(.+)\.md$")
HEADING_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class TicketCtlError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "ERROR",
        details: dict[str, Any] | None = None,
        changed: bool = False,
        ticket: str | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.details = details
        self.changed = changed
        self.ticket = ticket


class ValidationFailure(TicketCtlError):
    def __init__(self, errors: Sequence[str]):
        super().__init__(
            "Ticket DAG validation failed",
            kind="VALIDATION_ERROR",
            details={"errors": list(errors)},
        )


class StructuredArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise TicketCtlError(message, kind="USAGE_ERROR")


@dataclasses.dataclass
class Ticket:
    ticket_id: str
    path: Path
    text: str
    title: str
    status: str
    what_to_build: str
    acceptance: list[str]
    dependency_tokens: list[str]
    dependencies: list[str]
    state: dict[str, Any]
    url: str | None = None
    claimed_by_other: tuple[str, ...] = ()
    claimed_by_current: bool = True
    can_resume: bool = True


@dataclasses.dataclass
class Tracker:
    feature_root: Path
    repo_root: Path
    tickets: dict[str, Ticket]
    roadmap_path: Path
    backend: str = "local"
    repository: str | None = None
    feature_id: str | None = None
    store: Any | None = None
    schema_version: int = OUTPUT_SCHEMA_VERSION


@dataclasses.dataclass(frozen=True)
class ExecutionSnapshot:
    active: tuple[str, ...]
    frontier: tuple[str, ...]
    resumable: tuple[str, ...]
    outcome: str


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "waits_for": [],
        "pause_reason": None,
        "handoff": None,
        "last_pause": None,
        "last_confirmation": None,
        "completion": None,
    }


def _exact_fields(
    value: object,
    required: set[str],
    *,
    optional: set[str] | None = None,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    unexpected = sorted(set(value) - allowed)
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"{label} has unknown fields: {', '.join(unexpected)}")
    return value


def _bounded_text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return normalized


def _aggregate_results(results: Sequence[str]) -> str:
    if "BLOCKED" in results:
        return "BLOCKED"
    if "FAIL" in results:
        return "FAIL"
    return "PASS"


def _canonical_criteria(
    value: object, *, expected: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TypeError("confirmation criteria must be an array")
    if expected is not None and len(value) != len(expected):
        raise ValueError("confirmation result must cover every criterion")

    canonical: list[dict[str, Any]] = []
    for index, raw_check in enumerate(value):
        check = _exact_fields(
            raw_check,
            {"criterion", "result", "evidence"},
            optional={"subchecks"},
            label=f"criterion {index + 1}",
        )
        criterion = check["criterion"]
        if not isinstance(criterion, str) or not criterion:
            raise ValueError("criterion text must be a non-empty string")
        if expected is not None and criterion != expected[index]:
            raise ValueError("confirmation criteria must match the ticket in order")
        result = check["result"]
        if result not in {"PASS", "FAIL", "BLOCKED"}:
            raise ValueError("criterion result must be PASS, FAIL, or BLOCKED")
        evidence = _bounded_text(
            check["evidence"],
            label="criterion evidence",
            maximum=CONFIRMATION_EVIDENCE_MAX_CHARS,
        )
        normalized: dict[str, Any] = {
            "criterion": criterion,
            "result": result,
            "evidence": evidence,
        }

        if "subchecks" in check:
            subchecks = check["subchecks"]
            if not isinstance(subchecks, list) or not subchecks:
                raise ValueError("criterion subchecks must be a non-empty array")
            if len(subchecks) > CONFIRMATION_SUBCHECKS_MAX:
                raise ValueError(
                    f"criterion subchecks exceed {CONFIRMATION_SUBCHECKS_MAX} entries"
                )
            names: set[str] = set()
            canonical_subchecks: list[dict[str, str]] = []
            subcheck_results: list[str] = []
            for raw_subcheck in subchecks:
                subcheck = _exact_fields(
                    raw_subcheck,
                    {"check", "result", "evidence"},
                    label="subcheck",
                )
                name = _bounded_text(
                    subcheck["check"],
                    label="subcheck name",
                    maximum=CONFIRMATION_CHECK_NAME_MAX_CHARS,
                )
                if name in names:
                    raise ValueError("subcheck names must be unique")
                subcheck_result = subcheck["result"]
                if subcheck_result not in {"PASS", "FAIL", "BLOCKED"}:
                    raise ValueError("subcheck result must be PASS, FAIL, or BLOCKED")
                subcheck_evidence = _bounded_text(
                    subcheck["evidence"],
                    label="subcheck evidence",
                    maximum=CONFIRMATION_EVIDENCE_MAX_CHARS,
                )
                names.add(name)
                subcheck_results.append(subcheck_result)
                canonical_subchecks.append(
                    {
                        "check": name,
                        "result": subcheck_result,
                        "evidence": subcheck_evidence,
                    }
                )
            if result != _aggregate_results(subcheck_results):
                raise ValueError("criterion result does not match its subchecks")
            normalized["subchecks"] = canonical_subchecks
        canonical.append(normalized)
    return canonical


def _normalize_status(value: str) -> str:
    key = re.sub(r"[\s_]+", "-", value.strip().lower())
    aliases = {
        "ready": READY,
        "ready-for-agent": READY,
        "open": READY,
        "in-progress": IN_PROGRESS,
        "blocked": PAUSED,
        "paused": PAUSED,
        "done": DONE,
        "passed": DONE,
    }
    if key not in aliases:
        raise ValueError("Status must be ready-for-agent, in-progress, paused, or Done")
    return aliases[key]


def _rendered_status(status: str) -> str:
    return "Done" if status == DONE else status


def _metadata_values(text: str, key: str) -> list[str]:
    pattern = re.compile(
        rf"^\*\*{re.escape(key)}:\*\*[ \t]*(.*?)[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    return [value.strip() for value in pattern.findall(text)]


def _metadata(text: str, key: str) -> str | None:
    values = _metadata_values(text, key)
    return values[0] if len(values) == 1 and values[0] else None


def _sections(text: str) -> tuple[dict[str, str], list[str]]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    duplicates: list[str] = []
    for index, match in enumerate(matches):
        name = match.group(1).strip().lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if name in sections:
            duplicates.append(name)
        else:
            sections[name] = body
    return sections, duplicates


def _requirements(body: str) -> list[str]:
    def meaningful(value: str) -> bool:
        return value.strip().lower().rstrip(".!?") not in {
            "",
            "none",
            "n/a",
            "not applicable",
            "no",
        }

    bullets: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        match = re.match(r"^(?:[-*+]\s+)(?:\[[ xX]\]\s*)?(.+)$", line)
        if match:
            value = match.group(1).strip()
            if meaningful(value):
                bullets.append(value)
    if bullets:
        return bullets

    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                value = " ".join(current)
                if meaningful(value):
                    paragraphs.append(value)
                current = []
        elif not line.startswith(("#", "```")):
            current.append(line)
    if current:
        value = " ".join(current)
        if meaningful(value):
            paragraphs.append(value)
    return paragraphs


def _preamble_acceptance(text: str) -> list[str]:
    section = SECTION_RE.search(text)
    end = section.start() if section else len(text)
    state = text.find(STATE_START)
    if state >= 0:
        end = min(end, state)
    criteria: list[str] = []
    for raw_line in text[:end].splitlines():
        match = re.match(r"^\s*[-*+]\s+\[[ xX]\]\s+(.+?)\s*$", raw_line)
        if match:
            criteria.append(match.group(1).strip())
    return criteria


def _dependency_tokens(value: str) -> list[str]:
    if re.match(r"^\s*(?:[-*+]\s*)?(?:none|n/a|no\b)", value, re.IGNORECASE):
        return []

    quoted = re.findall(r"`\s*#?(\d+(?:\s*-\s*\d+)?)\s*(?:PASS)?\s*`", value)
    candidates = quoted
    if not candidates:
        candidates = []
        parts = re.split(
            r"[,;\u3001\uff0c\uff1b]|\band\b|\u548c", value, flags=re.IGNORECASE
        )
        for part in parts:
            match = re.match(
                r"\s*(?:[-*]\s*)?(?:ticket\s*)?#?(\d+)(?:\s*-\s*(\d+))?"
                r"(?=\s*(?:$|[-\u2013\u2014:/()\[\]]|\s))",
                part,
                re.IGNORECASE,
            )
            if match:
                candidates.append(
                    match.group(1) + (f"-{match.group(2)}" if match.group(2) else "")
                )
    if not candidates:
        raise ValueError(f"cannot parse ticket references from {value!r}")

    expanded: list[str] = []
    for candidate in candidates:
        parts = re.split(r"\s*-\s*", candidate)
        if len(parts) == 1:
            expanded.append(str(int(parts[0])))
            continue
        start, end = (int(part) for part in parts)
        if end < start or end - start > 1000:
            raise ValueError(f"invalid dependency range {candidate!r}")
        expanded.extend(str(number) for number in range(start, end + 1))
    return list(dict.fromkeys(expanded))


def _parse_state(text: str) -> dict[str, Any]:
    matches = STATE_RE.findall(text)
    marker_count = text.count(STATE_START) + text.count(STATE_END)
    if not matches:
        if marker_count:
            raise ValueError("managed state marker is incomplete")
        return _default_state()
    if len(matches) != 1 or marker_count != 2:
        raise ValueError("managed state marker is duplicated")
    try:
        raw = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"managed state is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("managed state must be a version 1 JSON object")
    unexpected = sorted(set(raw) - set(_default_state()))
    if unexpected:
        raise ValueError(f"managed state has unknown fields: {', '.join(unexpected)}")
    state = _default_state()
    state.update(raw)
    waits = state.get("waits_for")
    if not isinstance(waits, list) or any(not isinstance(item, str) for item in waits):
        raise ValueError("managed waits_for must be an array of ticket ids")
    if state.get("pause_reason") is not None and not isinstance(
        state["pause_reason"], str
    ):
        raise ValueError("managed pause_reason must be a string or null")
    if state.get("pause_reason") is not None:
        state["pause_reason"] = _bounded_text(
            state["pause_reason"],
            label="managed pause reason",
            maximum=CONFIRMATION_SUMMARY_MAX_CHARS,
        )
    handoff = state.get("handoff")
    if handoff is not None:
        if not isinstance(handoff, str):
            raise ValueError("managed handoff must be a string or null")
        handoff_path = Path(handoff)
        if (
            not handoff_path.parts
            or handoff_path.is_absolute()
            or ".." in handoff_path.parts
            or handoff_path.parts[0] != "handoffs"
        ):
            raise ValueError("managed handoff must be relative to handoffs/")
    last_pause = state.get("last_pause")
    if last_pause is not None:
        last_pause = _exact_fields(
            last_pause, {"reason", "until"}, label="managed last_pause"
        )
        if last_pause["reason"] is not None:
            last_pause["reason"] = _bounded_text(
                last_pause["reason"],
                label="managed last_pause reason",
                maximum=CONFIRMATION_SUMMARY_MAX_CHARS,
            )
        if not isinstance(last_pause["until"], list) or any(
            not isinstance(item, str) for item in last_pause["until"]
        ):
            raise ValueError("managed last_pause until must be an array of ticket ids")

    last_confirmation = state.get("last_confirmation")
    if last_confirmation is not None:
        last_confirmation = _exact_fields(
            last_confirmation,
            {"result", "result_digest", "summary", "criteria"},
            label="managed last_confirmation",
        )
        if last_confirmation["result"] not in {"PASS", "FAIL", "BLOCKED"}:
            raise ValueError("managed confirmation result is invalid")
        if not isinstance(last_confirmation["result_digest"], str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", last_confirmation["result_digest"]
        ):
            raise ValueError("managed confirmation digest is invalid")
        last_confirmation["summary"] = _bounded_text(
            last_confirmation["summary"],
            label="managed confirmation summary",
            maximum=CONFIRMATION_SUMMARY_MAX_CHARS,
        )
        last_confirmation["criteria"] = _canonical_criteria(
            last_confirmation["criteria"]
        )
        if last_confirmation["result"] != _aggregate_results(
            [check["result"] for check in last_confirmation["criteria"]]
        ):
            raise ValueError("managed confirmation result does not match its criteria")

    completion = state.get("completion")
    if completion is not None:
        if not isinstance(completion, dict):
            raise ValueError("managed completion must be an object or null")
        method = completion.get("method")
        required = (
            {"method"}
            if method == "verified"
            else {"method", "note"}
        )
        completion = _exact_fields(completion, required, label="managed completion")
        if method not in {"verified", "manual-pass", "explicit-done"}:
            raise ValueError("managed completion method is invalid")
        if method in {"manual-pass", "explicit-done"}:
            completion["note"] = _bounded_text(
                completion["note"],
                label="managed completion note",
                maximum=CONFIRMATION_SUMMARY_MAX_CHARS,
            )
    return state


def _parse_ticket(path: Path) -> tuple[Ticket | None, list[str]]:
    errors: list[str] = []
    match = TICKET_FILE_RE.fullmatch(path.name)
    if not match:
        return None, [f"{path.name}: filename must match <number>-<slug>.md"]
    ticket_id = match.group(1)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, [f"{path.name}: cannot read UTF-8 Markdown: {exc}"]

    try:
        state = _parse_state(text)
    except (TypeError, ValueError) as exc:
        errors.append(f"{path.name}: {exc}")
        state = _default_state()
    contract_text = STATE_RE.sub("", text)

    headings = HEADING_RE.findall(contract_text)
    if len(headings) != 1:
        errors.append(f"{path.name}: expected exactly one level-one heading")
        title = match.group(2).replace("-", " ")
    else:
        heading = headings[0].strip()
        heading_id = re.match(r"^(\d+)\b", heading)
        if heading_id and int(heading_id.group(1)) != int(ticket_id):
            errors.append(f"{path.name}: heading id does not match filename id")
        title = (
            re.sub(
                r"^\d+\s*(?:[-\u2013\u2014])?\s*",
                "",
                heading,
            ).strip()
            or heading
        )

    status_values = _metadata_values(contract_text, "Status")
    if len(status_values) != 1 or not status_values[0]:
        errors.append(f"{path.name}: expected one non-empty Status field")
        status = READY
    else:
        try:
            status = _normalize_status(status_values[0])
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")
            status = READY

    sections, duplicates = _sections(contract_text)
    if duplicates:
        errors.append(f"{path.name}: duplicate sections: {', '.join(duplicates)}")

    what_to_build = _metadata(contract_text, "What to build")
    if what_to_build is None:
        what_to_build = sections.get("what to build") or sections.get("delivery")
    what_to_build = (what_to_build or title).strip()

    blocked_raw = _metadata(contract_text, "Blocked by")
    if blocked_raw is None:
        blocked_raw = sections.get("blocked by") or _metadata(
            contract_text, "Depends On"
        )
    blocked_raw = (blocked_raw or "None").strip()
    try:
        dependency_tokens = _dependency_tokens(blocked_raw)
    except ValueError as exc:
        errors.append(f"{path.name}: Blocked by: {exc}")
        dependency_tokens = []

    acceptance_body = sections.get("acceptance criteria") or sections.get(
        "core acceptance"
    )
    acceptance = (
        _requirements(acceptance_body)
        if acceptance_body is not None
        else _preamble_acceptance(contract_text)
    )
    if not acceptance:
        errors.append(f"{path.name}: no acceptance criteria found")

    return (
        Ticket(
            ticket_id=ticket_id,
            path=path,
            text=text,
            title=title,
            status=status,
            what_to_build=what_to_build,
            acceptance=acceptance,
            dependency_tokens=dependency_tokens,
            dependencies=[],
            state=state,
        ),
        errors,
    )


def _repo_root(feature_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(feature_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    if feature_root.parent.name == ".scratch":
        return feature_root.parent.parent.resolve()
    return feature_root.resolve()


def _resolve_numeric_id(token: str, aliases: dict[str, str]) -> str | None:
    try:
        return aliases.get(str(int(token)))
    except ValueError:
        return None


def load_tracker(feature_root: Path) -> Tracker:
    root = feature_root.expanduser().resolve()
    issues = root / "issues"
    if not issues.is_dir():
        raise ValidationFailure([f"issues directory does not exist: {issues}"])

    paths = sorted(issues.glob("*.md"), key=lambda path: path.name)
    if not paths:
        raise ValidationFailure([f"no ticket Markdown files found in {issues}"])

    errors: list[str] = []
    tickets: dict[str, Ticket] = {}
    numeric_ids: dict[str, str] = {}
    for path in paths:
        ticket, ticket_errors = _parse_ticket(path)
        errors.extend(ticket_errors)
        if ticket is None:
            continue
        numeric = str(int(ticket.ticket_id))
        if numeric in numeric_ids:
            errors.append(
                f"duplicate numeric ticket id: {numeric_ids[numeric]!r} and {ticket.ticket_id!r}"
            )
            continue
        numeric_ids[numeric] = ticket.ticket_id
        tickets[ticket.ticket_id] = ticket

    for ticket in tickets.values():
        dependencies: list[str] = []
        for token in ticket.dependency_tokens:
            resolved = _resolve_numeric_id(token, numeric_ids)
            if resolved is None:
                errors.append(f"{ticket.path.name}: missing dependency {token!r}")
            elif resolved == ticket.ticket_id:
                errors.append(f"{ticket.path.name}: ticket cannot depend on itself")
            elif resolved not in dependencies:
                dependencies.append(resolved)
        ticket.dependencies = dependencies

        waits: list[str] = []
        for token in ticket.state["waits_for"]:
            resolved = _resolve_numeric_id(token, numeric_ids)
            if resolved is None:
                errors.append(f"{ticket.path.name}: missing waits_for ticket {token!r}")
            elif resolved == ticket.ticket_id:
                errors.append(f"{ticket.path.name}: ticket cannot wait for itself")
            elif resolved not in waits:
                waits.append(resolved)
        ticket.state["waits_for"] = waits

    cycle = _find_cycle(tickets)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    if errors:
        raise ValidationFailure(errors)

    return Tracker(
        feature_root=root,
        repo_root=_repo_root(root),
        tickets=tickets,
        roadmap_path=root / "implementation-roadmap.md",
    )


def load_github_tracker(
    repo_root: Path,
    *,
    repository: str | None = None,
    feature_id: str | None = None,
) -> Tracker:
    root = repo_root.expanduser().resolve()
    store = GitHubTicketStore(
        root,
        repository=repository,
        feature_number=feature_id,
    )
    try:
        feature = store.load()
    except GitHubStoreError as exc:
        raise TicketCtlError(
            str(exc),
            kind="POST_COMMIT_ERROR" if exc.changed else exc.kind,
            details=exc.details,
            changed=exc.changed,
        ) from exc

    store.repository = feature.repository
    store.feature_number = str(feature.feature_number)
    feature_root = root / ".scratch" / f"github-{feature.feature_number}"
    errors: list[str] = []
    tickets: dict[str, Ticket] = {}
    for issue in feature.issues:
        label = f"GitHub issue #{issue.number}"
        sections, duplicates = _sections(issue.body)
        if duplicates:
            errors.append(f"{label}: duplicate sections: {', '.join(duplicates)}")
        what_to_build = (
            sections.get("what to build")
            or sections.get("delivery")
            or issue.title
        ).strip()
        acceptance_body = sections.get("acceptance criteria") or sections.get(
            "core acceptance"
        )
        acceptance = _requirements(acceptance_body or "")
        if not acceptance:
            errors.append(f"{label}: no acceptance criteria found")

        blocked_body = sections.get("blocked by") or "None"
        try:
            dependency_tokens = (
                [str(number) for number in issue.blockers]
                if issue.blockers
                else _dependency_tokens(blocked_body)
            )
        except ValueError as exc:
            errors.append(f"{label}: Blocked by: {exc}")
            dependency_tokens = []

        try:
            state = _parse_state(issue.state_text or "")
        except (TypeError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            state = _default_state()
        other_assignees = tuple(
            assignee
            for assignee in issue.assignees
            if assignee != feature.current_login
        )
        claimed_by_current = feature.current_login in issue.assignees
        can_resume = True
        if issue.state == "CLOSED":
            status = DONE
        elif issue.assignees:
            status = IN_PROGRESS
        elif state.get("pause_reason") is not None:
            status = PAUSED
        elif "ready-for-agent" in issue.labels:
            status = READY
        else:
            status = PAUSED
            can_resume = False
            state = dict(state)
            state["pause_reason"] = "GitHub issue is not labeled ready-for-agent"

        ticket_id = str(issue.number)
        tickets[ticket_id] = Ticket(
            ticket_id=ticket_id,
            path=feature_root / "issues" / f"{ticket_id}-github.md",
            text=issue.body,
            title=issue.title,
            status=status,
            what_to_build=what_to_build,
            acceptance=acceptance,
            dependency_tokens=dependency_tokens,
            dependencies=[],
            state=state,
            url=issue.url,
            claimed_by_other=other_assignees,
            claimed_by_current=claimed_by_current,
            can_resume=can_resume,
        )

    aliases = {str(int(ticket_id)): ticket_id for ticket_id in tickets}
    for ticket in tickets.values():
        dependencies: list[str] = []
        for token in ticket.dependency_tokens:
            resolved = _resolve_numeric_id(token, aliases)
            if resolved is None:
                errors.append(
                    f"GitHub issue #{ticket.ticket_id}: missing dependency {token!r}"
                )
            elif resolved == ticket.ticket_id:
                errors.append(
                    f"GitHub issue #{ticket.ticket_id}: ticket cannot depend on itself"
                )
            elif resolved not in dependencies:
                dependencies.append(resolved)
        ticket.dependencies = dependencies

        waits: list[str] = []
        for token in ticket.state["waits_for"]:
            resolved = _resolve_numeric_id(token, aliases)
            if resolved is None:
                errors.append(
                    f"GitHub issue #{ticket.ticket_id}: missing waits_for ticket {token!r}"
                )
            elif resolved == ticket.ticket_id:
                errors.append(
                    f"GitHub issue #{ticket.ticket_id}: ticket cannot wait for itself"
                )
            elif resolved not in waits:
                waits.append(resolved)
        ticket.state["waits_for"] = waits

    cycle = _find_cycle(tickets)
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))
    if errors:
        raise ValidationFailure(errors)

    return Tracker(
        feature_root=feature_root,
        repo_root=root,
        tickets=tickets,
        roadmap_path=feature_root / "implementation-roadmap.md",
        backend="github",
        repository=feature.repository,
        feature_id=str(feature.feature_number),
        store=store,
        schema_version=GITHUB_OUTPUT_SCHEMA_VERSION,
    )


def _ticket_dependencies(ticket: Ticket) -> list[str]:
    return list(dict.fromkeys(ticket.dependencies + ticket.state["waits_for"]))


def _find_cycle(tickets: dict[str, Ticket]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(ticket_id: str) -> list[str]:
        if ticket_id in visiting:
            return stack[stack.index(ticket_id) :] + [ticket_id]
        if ticket_id in visited:
            return []
        visiting.add(ticket_id)
        stack.append(ticket_id)
        for dependency in _ticket_dependencies(tickets[ticket_id]):
            if dependency in tickets:
                found = visit(dependency)
                if found:
                    return found
        stack.pop()
        visiting.remove(ticket_id)
        visited.add(ticket_id)
        return []

    for ticket_id in sorted(tickets, key=lambda value: (int(value), value)):
        found = visit(ticket_id)
        if found:
            return found
    return []


def _done(tracker: Tracker, ticket_id: str) -> bool:
    return tracker.tickets[ticket_id].status == DONE


def _execution_snapshot(tracker: Tracker) -> ExecutionSnapshot:
    active: list[str] = []
    ready: list[str] = []
    resume: list[str] = []
    complete = True
    for ticket_id in sorted(tracker.tickets, key=lambda value: (int(value), value)):
        ticket = tracker.tickets[ticket_id]
        complete &= ticket.status == DONE
        if ticket.status == IN_PROGRESS:
            active.append(ticket_id)
        elif ticket.status == READY and all(
            _done(tracker, dependency) for dependency in ticket.dependencies
        ):
            ready.append(ticket_id)
        elif (
            ticket.status == PAUSED
            and ticket.can_resume
            and bool(ticket.state["waits_for"])
            and all(
                _done(tracker, dependency)
                for dependency in _ticket_dependencies(ticket)
            )
        ):
            resume.append(ticket_id)
    outcome = (
        "ACTIVE"
        if active
        else "COMPLETE"
        if complete
        else "READY"
        if resume or ready
        else "DEADLOCK"
    )
    return ExecutionSnapshot(tuple(active), tuple(ready), tuple(resume), outcome)


def _single_lane_snapshot(tracker: Tracker) -> ExecutionSnapshot:
    snapshot = _execution_snapshot(tracker)
    if len(snapshot.active) > 1:
        raise TicketCtlError(
            "multiple tickets are in progress",
            kind="ACTIVE_TICKET_CONFLICT",
            details={"tickets": list(snapshot.active)},
        )
    return snapshot


def _require_single_active(
    tracker: Tracker,
    ticket_id: str | None,
    snapshot: ExecutionSnapshot | None = None,
) -> Ticket:
    snapshot = snapshot or _single_lane_snapshot(tracker)
    active = snapshot.active
    ticket = _require_ticket(tracker, ticket_id, snapshot)
    if not active or ticket.ticket_id != active[0]:
        raise TicketCtlError(
            f"ticket {ticket.ticket_id} is not the active ticket",
            kind="TICKET_NOT_ACTIVE",
            details={
                "status": ticket.status,
                "active": active[0] if active else None,
            },
        )
    return ticket


def _active_or_existing(
    tracker: Tracker,
    ticket_id: str | None,
    existing_status: str,
    snapshot: ExecutionSnapshot | None = None,
) -> Ticket:
    if ticket_id is not None:
        requested = _require_ticket(tracker, ticket_id, snapshot)
        if requested.status == existing_status:
            return requested
    return _require_single_active(tracker, ticket_id, snapshot)


def _display_state(
    tracker: Tracker,
    ticket: Ticket,
    snapshot: ExecutionSnapshot | None = None,
) -> str:
    snapshot = snapshot or _execution_snapshot(tracker)
    if ticket.status == READY and ticket.ticket_id not in snapshot.frontier:
        return "blocked"
    if ticket.status == PAUSED and ticket.ticket_id in snapshot.resumable:
        return "resumable"
    return ticket.status


def _roadmap_waves(tracker: Tracker) -> list[list[str]]:
    wave_by_ticket: dict[str, int] = {}

    def resolve(ticket_id: str) -> int:
        if ticket_id not in wave_by_ticket:
            dependencies = _ticket_dependencies(tracker.tickets[ticket_id])
            wave_by_ticket[ticket_id] = (
                max(resolve(dependency) for dependency in dependencies) + 1
                if dependencies
                else 0
            )
        return wave_by_ticket[ticket_id]

    for ticket_id in tracker.tickets:
        resolve(ticket_id)

    waves: list[list[str]] = []
    for ticket_id, wave in sorted(
        wave_by_ticket.items(), key=lambda item: (item[1], int(item[0]), item[0])
    ):
        while len(waves) <= wave:
            waves.append([])
        waves[wave].append(ticket_id)
    return waves


def _roadmap_dependency_groups(
    tracker: Tracker, *, remediation: bool
) -> list[tuple[list[str], tuple[str, ...]]]:
    grouped: dict[tuple[str, ...], list[str]] = {}
    for ticket_id, ticket in sorted(
        tracker.tickets.items(), key=lambda item: (int(item[0]), item[0])
    ):
        dependencies = (
            [
                dependency
                for dependency in ticket.state["waits_for"]
                if dependency not in ticket.dependencies
            ]
            if remediation
            else ticket.dependencies
        )
        if dependencies:
            grouped.setdefault(tuple(dependencies), []).append(ticket_id)
    return [(targets, dependencies) for dependencies, targets in grouped.items()]


def _roadmap_ticket_link(ticket: Ticket) -> str:
    title = (
        ticket.title.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\n", " ")
    )
    target = ticket.url or f"issues/{ticket.path.name}"
    return f"[`{ticket.ticket_id}` {title}]({target})"


def _roadmap_text(tracker: Tracker, snapshot: ExecutionSnapshot | None = None) -> str:
    snapshot = snapshot or _execution_snapshot(tracker)

    def values(items: list[str]) -> str:
        return ", ".join(f"`{item}`" for item in items) or "None"

    def links(items: list[str]) -> str:
        return (
            ", ".join(
                _roadmap_ticket_link(tracker.tickets[ticket_id]) for ticket_id in items
            )
            or "None"
        )

    done_count = sum(ticket.status == DONE for ticket in tracker.tickets.values())
    waves = _roadmap_waves(tracker)
    wave_lines = [
        f"- W{index}: {values(ticket_ids)}" for index, ticket_ids in enumerate(waves)
    ]

    rows: list[str] = []
    for ticket_id in sorted(tracker.tickets, key=lambda value: (int(value), value)):
        ticket = tracker.tickets[ticket_id]
        completion = ticket.state.get("completion") or {}
        state = _display_state(tracker, ticket, snapshot)
        method = completion.get("method") or (
            "legacy" if ticket.status == DONE else None
        )
        if method:
            state += f" ({method})"
        rows.append(f"| {_roadmap_ticket_link(ticket)} | {state} |")

    roots = [
        ticket_id
        for ticket_id in sorted(tracker.tickets, key=lambda value: (int(value), value))
        if not _ticket_dependencies(tracker.tickets[ticket_id])
    ]
    dag_lines = [f"- Roots: {values(roots)}"]
    for targets, dependencies in _roadmap_dependency_groups(tracker, remediation=False):
        dag_lines.append(f"- {values(targets)} <- {values(list(dependencies))}")
    for targets, dependencies in _roadmap_dependency_groups(tracker, remediation=True):
        dag_lines.append(
            f"- {values(targets)} <- {values(list(dependencies))} (remediation)"
        )

    authority = (
        "GitHub Issues are authoritative."
        if tracker.backend == "github"
        else "Ticket files are authoritative."
    )
    return (
        "# Implementation Roadmap\n\n"
        f"<!-- Generated by tickets. {authority} -->\n\n"
        "## Current\n\n"
        f"- Active: {links(list(snapshot.active))}\n"
        f"- Ready queue: {links(list(snapshot.frontier))}\n"
        f"- Resumable: {links(list(snapshot.resumable))}\n"
        f"- Progress: {done_count} / {len(tracker.tickets)} Done\n\n"
        "## Execution Plan\n\n" + "\n".join(wave_lines) + "\n\n"
        "## Tickets\n\n"
        "| Ticket | State |\n"
        "|---|---|\n" + "\n".join(rows) + "\n\n## DAG\n\n" + "\n".join(dag_lines) + "\n"
    )


def _roadmap_status(tracker: Tracker, snapshot: ExecutionSnapshot | None = None) -> str:
    path = tracker.roadmap_path
    if not path.exists():
        return "MISSING"
    try:
        return (
            "CURRENT"
            if path.read_text(encoding="utf-8") == _roadmap_text(tracker, snapshot)
            else "STALE"
        )
    except (OSError, UnicodeError):
        return "UNREADABLE"


def _replace_status(text: str, status: str) -> str:
    pattern = re.compile(r"^(\*\*Status:\*\*)[ \t]*.*$", re.MULTILINE | re.IGNORECASE)
    updated, count = pattern.subn(rf"\g<1> {_rendered_status(status)}", text)
    if count != 1:
        raise TicketCtlError("cannot update the unique Status field")
    return updated


def _replace_state(text: str, state: dict[str, Any]) -> str:
    defaults = _default_state()
    unexpected = sorted(set(state) - set(defaults))
    if unexpected:
        raise ValueError(f"managed state has unknown fields: {', '.join(unexpected)}")
    stored = {
        key: state.get(key, default)
        for key, default in defaults.items()
        if key == "version" or state.get(key, default) != default
    }
    block = (
        f"{STATE_START}\n```json\n"
        f"{json.dumps(stored, indent=2, sort_keys=True, ensure_ascii=False)}\n"
        f"```\n{STATE_END}"
    )
    if STATE_RE.search(text):
        return STATE_RE.sub(lambda _: block, text, count=1)
    return text.rstrip() + "\n\n" + block + "\n"


def _atomic_write(path: Path, text: str, *, expected: str | None) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if expected is None:
            if path.exists():
                raise TicketCtlError(f"refusing to replace concurrently created {path}")
        else:
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise TicketCtlError(f"cannot verify {path}: {exc}") from exc
            if current != expected:
                raise TicketCtlError(
                    f"refusing to overwrite concurrently modified {path}",
                    kind="CONCURRENT_MODIFICATION",
                )
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


@contextlib.contextmanager
def _feature_lock(feature_root: Path) -> Iterator[None]:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(feature_root.resolve()))[-120:]
    path = Path(tempfile.gettempdir()) / f"run-ticket-plan-{safe_name}.lock"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _locked_tracker(
    args: argparse.Namespace,
) -> Iterator[tuple[Tracker, ExecutionSnapshot]]:
    lock_root = (
        Path(args.repo_root or Path.cwd())
        / ".scratch"
        / "github-controller"
        if args.tracker == "github"
        else Path(args.feature_root)
        if args.feature_root is not None
        else Path.cwd() / ".scratch" / "missing-feature"
    )
    with _feature_lock(lock_root):
        tracker = _load_tracker_from_args(args)
        yield tracker, _single_lane_snapshot(tracker)


def _sync_roadmap(
    tracker: Tracker, snapshot: ExecutionSnapshot | None = None
) -> dict[str, Any]:
    path = tracker.roadmap_path
    rendered = _roadmap_text(tracker, snapshot)
    try:
        if tracker.backend == "github":
            path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _atomic_write(path, rendered, expected=None)
            return {"state": "CURRENT", "changed": True, "created": True}
        original = path.read_text(encoding="utf-8")
        if original == rendered:
            return {"state": "CURRENT", "changed": False, "created": False}
        if "# Implementation Roadmap" not in original:
            return {"state": "CONFLICT", "changed": False, "created": False}
        _atomic_write(path, rendered, expected=original)
        return {"state": "CURRENT", "changed": True, "created": False}
    except (OSError, UnicodeError, TicketCtlError) as exc:
        return {
            "state": "STALE",
            "changed": False,
            "created": False,
            "error": str(exc),
        }


def _write_ticket(
    ticket: Ticket, *, status: str, state: dict[str, Any] | None = None
) -> None:
    updated = _replace_status(ticket.text, status)
    if state is not None:
        updated = _replace_state(updated, state)
    _atomic_write(ticket.path, updated, expected=ticket.text)


def _persist_ticket(
    tracker: Tracker,
    ticket: Ticket,
    *,
    status: str,
    state: dict[str, Any] | None = None,
) -> bool:
    if tracker.backend == "local":
        _write_ticket(ticket, status=status, state=state)
        return True
    state_text = _replace_state("", state).strip() if state is not None else None
    summary = None
    if status == PAUSED and state is not None:
        summary = f"Paused by `$tickets`: {state.get('pause_reason') or 'No reason supplied.'}"
    elif status == IN_PROGRESS and state is not None:
        summary = "Resumed by `$tickets`."
    elif status == DONE and state is not None:
        completion = state.get("completion") or {}
        summary = f"Completed by `$tickets`: {completion.get('note') or 'Explicit user completion.'}"
    try:
        return bool(
            tracker.store.persist(
                int(ticket.ticket_id),
                status=status,
                state_text=state_text,
                summary=summary,
            )
        )
    except GitHubStoreError as exc:
        raise TicketCtlError(
            str(exc),
            kind="POST_COMMIT_ERROR" if exc.changed else exc.kind,
            details=exc.details,
            changed=exc.changed,
            ticket=ticket.ticket_id,
        ) from exc


def _reload_after_write(args: argparse.Namespace, ticket_id: str) -> Tracker:
    try:
        tracker = _load_tracker_from_args(args)
        _single_lane_snapshot(tracker)
        return tracker
    except TicketCtlError as exc:
        cause = exc.kind
    except ENVIRONMENT_ERRORS as exc:
        cause = type(exc).__name__
    raise TicketCtlError(
        "ticket state changed but the controller could not reload the Feature",
        kind="POST_COMMIT_ERROR",
        details={"cause": cause},
        changed=True,
        ticket=ticket_id,
    )


def _require_ticket(
    tracker: Tracker,
    ticket_id: str | None,
    snapshot: ExecutionSnapshot | None = None,
) -> Ticket:
    if ticket_id is None:
        active = (snapshot or _execution_snapshot(tracker)).active
        if len(active) != 1:
            raise TicketCtlError(
                "ticket is required when there is not exactly one active ticket",
                kind="TICKET_REQUIRED",
            )
        return tracker.tickets[active[0]]
    aliases = {str(int(value)): value for value in tracker.tickets}
    resolved = _resolve_numeric_id(ticket_id, aliases)
    if resolved is None:
        raise TicketCtlError(f"unknown ticket {ticket_id!r}", kind="UNKNOWN_TICKET")
    return tracker.tickets[resolved]


def _work_packet(tracker: Tracker, ticket: Ticket) -> dict[str, Any]:
    handoff = ticket.state.get("handoff")
    source = (
        {
            "tracker": "github",
            "feature_root": str(tracker.feature_root),
            "repository_root": str(tracker.repo_root),
            "repository": tracker.repository,
            "feature_issue": tracker.feature_id,
            "ticket_url": ticket.url,
        }
        if tracker.backend == "github"
        else {
            "feature_root": str(tracker.feature_root),
            "repository_root": str(tracker.repo_root),
            "ticket_path": str(ticket.path),
        }
    )
    return {
        "source": source,
        "ticket": {
            "id": ticket.ticket_id,
            "title": ticket.title,
            "state": _display_state(tracker, ticket),
            "blocked_by": ticket.dependencies,
        },
        "objective": ticket.what_to_build,
        "acceptance_criteria": ticket.acceptance,
        "boundaries": {
            "implementation": "Implement only this ticket's objective and acceptance criteria.",
            "control_plane": (
                "Do not edit ticket status, managed state, or the roadmap directly."
            ),
        },
        "history": {
            "last_pause": ticket.state.get("last_pause"),
            "last_confirmation": ticket.state.get("last_confirmation"),
            "handoff": str(tracker.feature_root / handoff) if handoff else None,
        },
    }


def _response(
    command: str,
    outcome: str,
    *,
    ticket: str | None = None,
    changed: bool = False,
    data: dict[str, Any] | None = None,
    next_action: dict[str, str] | None = None,
    error: dict[str, Any] | None = None,
    schema_version: int = OUTPUT_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "command": command,
        "outcome": outcome,
        "ticket": ticket,
        "changed": changed,
        "data": data or {},
        "next": next_action,
        "error": error,
    }


def _roadmap_data(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        return {"state": value, "changed": False, "created": False}
    return value


def _ticket_action(mode: str, ticket_id: str, reason: str) -> dict[str, str]:
    return {
        "command": f"tickets {mode} {ticket_id}",
        "reason": reason,
    }


def _next_ready_action(ticket_ids: Sequence[str]) -> dict[str, str] | None:
    return (
        _ticket_action("next", ticket_ids[0], "oldest executable ticket")
        if ticket_ids
        else None
    )


def _mutation_response(
    command: str,
    outcome: str,
    tracker: Tracker,
    ticket: Ticket,
    *,
    changed: bool,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = _execution_snapshot(tracker)
    roadmap = _roadmap_data(_sync_roadmap(tracker, snapshot))
    if command == "next":
        data = {"roadmap": roadmap, "work_packet": _work_packet(tracker, ticket)}
        next_action = {
            "command": "$implement",
            "reason": "implement the active ticket",
        }
    elif command == "pause":
        ready = list(snapshot.frontier)
        data = {**(payload or {}), "roadmap": roadmap, "frontier": ready}
        next_action = _next_ready_action(ready)
    else:
        ready = list(snapshot.resumable + snapshot.frontier)
        data = {
            **(payload or {}),
            "roadmap": roadmap,
            "frontier": list(snapshot.frontier),
            "resumable": list(snapshot.resumable),
        }
        next_action = _next_ready_action(ready)
    return _response(
        command,
        outcome,
        ticket=ticket.ticket_id,
        changed=changed,
        data=data,
        next_action=next_action,
        schema_version=tracker.schema_version,
    )


def _load_tracker_from_args(args: argparse.Namespace) -> Tracker:
    if args.tracker == "github":
        return load_github_tracker(
            Path(args.repo_root or Path.cwd()),
            repository=args.repo,
            feature_id=args.feature,
        )
    if args.feature_root is None:
        raise TicketCtlError(
            "the following arguments are required: --feature-root",
            kind="USAGE_ERROR",
        )
    return load_tracker(Path(args.feature_root))


def _schema_version_from_args(args: argparse.Namespace | None) -> int:
    return (
        GITHUB_OUTPUT_SCHEMA_VERSION
        if args is not None and getattr(args, "tracker", "local") == "github"
        else OUTPUT_SCHEMA_VERSION
    )


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    snapshot = _single_lane_snapshot(tracker)
    active = list(snapshot.active)
    ready = list(snapshot.frontier)
    resume = list(snapshot.resumable)
    by_state: dict[str, list[str]] = {}
    blocked_by: dict[str, list[str]] = {}
    waits_for: dict[str, list[str]] = {}
    handoffs: dict[str, str] = {}
    completion_methods: dict[str, str] = {}
    for ticket_id, ticket in sorted(
        tracker.tickets.items(), key=lambda item: (int(item[0]), item[0])
    ):
        by_state.setdefault(_display_state(tracker, ticket, snapshot), []).append(
            ticket_id
        )
        if ticket.dependencies:
            blocked_by[ticket_id] = ticket.dependencies
        if ticket.state["waits_for"]:
            waits_for[ticket_id] = ticket.state["waits_for"]
        if ticket.state.get("handoff"):
            handoffs[ticket_id] = str(tracker.feature_root / ticket.state["handoff"])
        completion = ticket.state.get("completion") or {}
        if completion.get("method"):
            completion_methods[ticket_id] = completion["method"]
    active_is_controllable = not active or not (
        tracker.backend == "github"
        and not tracker.tickets[active[0]].claimed_by_current
    )
    selected = (
        active[0]
        if active and active_is_controllable
        else (resume + ready)[0]
        if not active and (resume or ready)
        else None
    )
    next_action = None
    if selected is not None:
        next_action = _ticket_action(
            "next",
            selected,
            (
                "resume active ticket"
                if active
                else "oldest resumable ticket"
                if selected in resume
                else "oldest executable ticket"
            ),
        )
    return _response(
        "status",
        snapshot.outcome,
        ticket=active[0] if active else None,
        data={
            "active": active[0] if active else None,
            "frontier": ready,
            "resumable": resume,
            "roadmap": _roadmap_data(_roadmap_status(tracker, snapshot)),
            "by_state": by_state,
            "blocked_by": blocked_by,
            "waits_for": waits_for,
            "handoffs": handoffs,
            "completion_methods": completion_methods,
        },
        next_action=next_action,
        schema_version=tracker.schema_version,
    )


def _handoff_reference(tracker: Tracker, path_value: str) -> str:
    path = Path(path_value).expanduser().resolve()
    try:
        relative = path.relative_to(tracker.feature_root)
    except ValueError as exc:
        raise TicketCtlError(
            "handoff file must be inside the feature root", kind="HANDOFF_INVALID"
        ) from exc
    if not relative.parts or relative.parts[0] != "handoffs":
        raise TicketCtlError(
            "handoff file must be inside the feature handoffs directory",
            kind="HANDOFF_INVALID",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TicketCtlError(
            f"cannot read handoff file: {exc}", kind="HANDOFF_INVALID"
        ) from exc
    if not re.search(
        r'(?m)^handoff_schema:\s*["\']?2\.0["\']?\s*$',
        text.split("---", 2)[1]
        if text.startswith("---") and text.count("---") >= 2
        else "",
    ):
        raise TicketCtlError(
            "handoff file must use handoff_schema 2.0", kind="HANDOFF_INVALID"
        )
    return relative.as_posix()


def command_next(args: argparse.Namespace) -> dict[str, Any]:
    with _locked_tracker(args) as (tracker, snapshot):
        if snapshot.active:
            ticket = tracker.tickets[snapshot.active[0]]
            requested = (
                _require_ticket(tracker, args.ticket, snapshot)
                if args.ticket is not None
                else ticket
            )
            if requested.ticket_id != ticket.ticket_id:
                raise TicketCtlError(
                    f"ticket {ticket.ticket_id} is already in progress",
                    kind="ACTIVE_TICKET_EXISTS",
                )
            if tracker.backend == "github" and not ticket.claimed_by_current:
                raise TicketCtlError(
                    f"ticket {ticket.ticket_id} is active for another user",
                    kind="ACTIVE_TICKET_EXISTS",
                    details={"assignees": list(ticket.claimed_by_other)},
                )
            if tracker.backend == "github" and ticket.state.get("pause_reason") is not None:
                state = dict(ticket.state)
                state["last_pause"] = {
                    "reason": state.get("pause_reason"),
                    "until": state.get("waits_for", []),
                }
                state["waits_for"] = []
                state["pause_reason"] = None
                changed = _persist_ticket(
                    tracker, ticket, status=IN_PROGRESS, state=state
                )
                updated = _reload_after_write(args, ticket.ticket_id)
                return _mutation_response(
                    "next",
                    "WORK_PACKET",
                    updated,
                    updated.tickets[ticket.ticket_id],
                    changed=changed,
                )
            return _mutation_response(
                "next", "WORK_PACKET", tracker, ticket, changed=False
            )

        if args.ticket is None:
            candidates = snapshot.resumable + snapshot.frontier
            if not candidates:
                raise TicketCtlError(
                    "feature is complete"
                    if snapshot.outcome == "COMPLETE"
                    else "feature is incomplete but no ticket is executable",
                    kind=(
                        "FEATURE_COMPLETE"
                        if snapshot.outcome == "COMPLETE"
                        else "DEADLOCK"
                    ),
                )
            ticket = tracker.tickets[candidates[0]]
        else:
            ticket = _require_ticket(tracker, args.ticket, snapshot)
            if ticket.claimed_by_other:
                raise TicketCtlError(
                    f"ticket {ticket.ticket_id} is claimed by another user",
                    kind="TICKET_NOT_READY",
                    details={"assignees": list(ticket.claimed_by_other)},
                )
            eligible = (
                ticket.status == PAUSED
                and ticket.can_resume
                and all(
                    _done(tracker, dependency)
                    for dependency in _ticket_dependencies(ticket)
                )
            ) or (ticket.status == READY and ticket.ticket_id in snapshot.frontier)
            if not eligible:
                error_kind = (
                    "TICKET_PAUSED" if ticket.status == PAUSED else "TICKET_NOT_READY"
                )
                raise TicketCtlError(
                    f"ticket {ticket.ticket_id} is not ready",
                    kind=error_kind,
                    details={
                        "status": ticket.status,
                        "frontier": list(snapshot.frontier),
                        "resumable": list(snapshot.resumable),
                    },
                )

        state: dict[str, Any] | None = None
        if ticket.status == PAUSED:
            state = dict(ticket.state)
            state["last_pause"] = {
                "reason": state.get("pause_reason"),
                "until": state.get("waits_for", []),
            }
            state["waits_for"] = []
            state["pause_reason"] = None
        _persist_ticket(tracker, ticket, status=IN_PROGRESS, state=state)
        updated = _reload_after_write(args, ticket.ticket_id)
        return _mutation_response(
            "next",
            "WORK_PACKET",
            updated,
            updated.tickets[ticket.ticket_id],
            changed=True,
        )


def command_pause(args: argparse.Namespace) -> dict[str, Any]:
    with _locked_tracker(args) as (tracker, snapshot):
        ticket = _active_or_existing(tracker, args.ticket, PAUSED, snapshot)
        if (
            tracker.backend == "github"
            and ticket.status == IN_PROGRESS
            and not ticket.claimed_by_current
        ):
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is active for another user",
                kind="TICKET_NOT_ACTIVE",
                details={"assignees": list(ticket.claimed_by_other)},
            )
        reason = args.reason.strip()
        if not reason:
            raise TicketCtlError(
                "pause requires a non-empty reason", kind="REASON_REQUIRED"
            )
        if len(reason) > CONFIRMATION_SUMMARY_MAX_CHARS:
            raise TicketCtlError(
                f"pause reason exceeds {CONFIRMATION_SUMMARY_MAX_CHARS} characters",
                kind="REASON_INVALID",
            )
        waits: list[str] = []
        for raw in args.wait_for or []:
            for value in raw.split(","):
                target = _require_ticket(tracker, value.strip())
                if target.ticket_id == ticket.ticket_id:
                    raise TicketCtlError(
                        "ticket cannot wait for itself", kind="DEPENDENCY_CYCLE"
                    )
                if target.ticket_id not in waits:
                    waits.append(target.ticket_id)

        handoff = (
            _handoff_reference(tracker, args.handoff_file)
            if args.handoff_file is not None
            else None
        )
        pause_data = {
            "reason": reason,
            "waits_for": waits,
            "handoff": str(tracker.feature_root / handoff) if handoff else None,
        }
        if ticket.status == PAUSED:
            if (
                ticket.state["waits_for"] == waits
                and ticket.state.get("pause_reason") == reason
                and ticket.state.get("handoff") == handoff
            ):
                changed = False
                current = tracker
                current_ticket = ticket
                if tracker.backend == "github":
                    changed = _persist_ticket(
                        tracker,
                        ticket,
                        status=PAUSED,
                        state=ticket.state,
                    )
                    if changed:
                        current = _reload_after_write(args, ticket.ticket_id)
                        current_ticket = current.tickets[ticket.ticket_id]
                return _mutation_response(
                    "pause",
                    "PAUSED",
                    current,
                    current_ticket,
                    changed=changed,
                    payload=pause_data,
                )
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is already paused with different state",
                kind="INVALID_TRANSITION",
            )

        state = dict(ticket.state)
        state["waits_for"] = waits
        state["pause_reason"] = reason
        state["handoff"] = handoff
        ticket.state = state
        cycle = _find_cycle(tracker.tickets)
        if cycle:
            raise TicketCtlError(
                "pause would create a dependency cycle",
                kind="DEPENDENCY_CYCLE",
                details={"cycle": cycle},
            )
        _persist_ticket(tracker, ticket, status=PAUSED, state=state)
        updated = _reload_after_write(args, ticket.ticket_id)
        return _mutation_response(
            "pause",
            "PAUSED",
            updated,
            updated.tickets[ticket.ticket_id],
            changed=True,
            payload=pause_data,
        )


def command_done(args: argparse.Namespace) -> dict[str, Any]:
    with _locked_tracker(args) as (tracker, snapshot):
        ticket = (
            _require_ticket(tracker, args.ticket, snapshot)
            if args.ticket is not None
            else _require_single_active(tracker, None, snapshot)
        )
        if (
            tracker.backend == "github"
            and ticket.status == IN_PROGRESS
            and not ticket.claimed_by_current
        ):
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is active for another user",
                kind="TICKET_NOT_ACTIVE",
                details={"assignees": list(ticket.claimed_by_other)},
            )
        note = (
            args.note if args.note is not None else "Explicit user completion."
        ).strip()
        if not note or len(note) > CONFIRMATION_SUMMARY_MAX_CHARS:
            raise TicketCtlError(
                "done note must contain 1 to "
                f"{CONFIRMATION_SUMMARY_MAX_CHARS} characters",
                kind="NOTE_INVALID",
            )
        requested_completion = {"method": "explicit-done", "note": note}
        if ticket.status == DONE:
            if ticket.state.get("completion") == requested_completion:
                return _mutation_response(
                    "done",
                    "DONE",
                    tracker,
                    ticket,
                    changed=False,
                    payload={"completion": requested_completion},
                )
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is already Done with different completion evidence",
                kind="INVALID_TRANSITION",
            )
        if ticket.status == PAUSED:
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is paused",
                kind="TICKET_PAUSED",
                details={
                    "status": ticket.status,
                    "pause_reason": ticket.state.get("pause_reason"),
                    "waits_for": ticket.state.get("waits_for", []),
                },
            )
        if ticket.status != IN_PROGRESS:
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is not the active ticket",
                kind="TICKET_NOT_ACTIVE",
                details={"status": ticket.status},
            )
        if snapshot.active != (ticket.ticket_id,):
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is not the active ticket",
                kind="TICKET_NOT_ACTIVE",
                details={"status": ticket.status},
            )
        state = dict(ticket.state)
        state["completion"] = requested_completion
        _persist_ticket(tracker, ticket, status=DONE, state=state)
        updated = _reload_after_write(args, ticket.ticket_id)
        return _mutation_response(
            "done",
            "DONE",
            updated,
            updated.tickets[ticket.ticket_id],
            changed=True,
            payload={"completion": requested_completion},
        )


def _parser() -> argparse.ArgumentParser:
    parser = StructuredArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    handlers = {
        "status": command_status,
        "next": command_next,
        "done": command_done,
        "pause": command_pause,
    }
    for name, handler in handlers.items():
        child = subparsers.add_parser(name)
        child.add_argument("--feature-root")
        child.add_argument("--tracker", choices=("local", "github"), default="local")
        child.add_argument("--repo-root")
        child.add_argument("--repo")
        child.add_argument("--feature")
        if name in {"next", "done", "pause"}:
            child.add_argument("--ticket")
        if name == "pause":
            child.add_argument("--reason", required=True)
            child.add_argument("--wait-for", dest="wait_for", action="append")
            child.add_argument(
                "--until",
                dest="wait_for",
                action="append",
                help=argparse.SUPPRESS,
            )
            child.add_argument("--handoff-file")
        if name == "done":
            child.add_argument("--note")
        child.set_defaults(handler=handler)
    return parser


def _emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=stream,
    )


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    command_hint = (
        raw_args[0] if raw_args and not raw_args[0].startswith("-") else "unknown"
    )
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(raw_args)
        _emit(args.handler(args))
        return 0
    except TicketCtlError as exc:
        payload = _response(
            args.command if args is not None else command_hint,
            "ERROR",
            ticket=(
                exc.ticket
                if exc.ticket is not None
                else getattr(args, "ticket", None)
                if args is not None
                else None
            ),
            changed=exc.changed,
            error={
                "code": exc.kind,
                "message": str(exc),
                "details": exc.details if exc.details is not None else {},
            },
            schema_version=_schema_version_from_args(args),
        )
        _emit(payload)
        return 2
    except ENVIRONMENT_ERRORS as exc:
        payload = _response(
            args.command if args is not None else command_hint,
            "ERROR",
            ticket=getattr(args, "ticket", None) if args is not None else None,
            error={
                "code": "ENVIRONMENT_ERROR",
                "message": "controller environment operation failed",
                "details": {"type": type(exc).__name__, "message": str(exc)},
            },
            schema_version=_schema_version_from_args(args),
        )
        _emit(payload)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
