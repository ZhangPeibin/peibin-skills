#!/usr/bin/env python3
"""Deterministic controller for Local Markdown implementation tickets."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUSES = ("Open", "In Progress", "Blocked", "Done")
CHECK_KINDS = (
    "command",
    "file",
    "assertion",
    "artifact",
    "external",
    "manual-review",
)
REQUIRED_METADATA = (
    "Status",
    "Wave / Layer",
    "Owner Repo",
    "Depends On",
    "Unblocks",
)
REQUIRED_SECTIONS = (
    "Delivery",
    "Core Acceptance",
    "External Integration Evidence",
    "Invariants / Out Of Scope",
    "Evidence",
    "Comments",
)
REQUIREMENT_SECTIONS = (
    ("Delivery", "DEL"),
    ("Core Acceptance", "AC"),
    ("External Integration Evidence", "EXT"),
    ("Invariants / Out Of Scope", "INV"),
    ("Evidence", "EVD"),
)
REQUIREMENT_SECTION_BY_PREFIX = {
    prefix: section for section, prefix in REQUIREMENT_SECTIONS
}
CORE_COMPLETION_GATES_METADATA = "Core Completion Gates"
CONTROLLER_VERSION = 6
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
TICKET_FILE_RE = re.compile(r"^(\d{2,})-(.+)\.md$")
HEADING_RE = re.compile(
    r"^#[ \t]+(\d{2,})(?:[ \t]+(?:-|\u2014)?[ \t]*.+)?$", re.MULTILINE
)
REFERENCE_RE = re.compile(
    r"^(E?\d{2,})(?:\s*-\s*(E?\d{2,}))?(?:\s+(PASS))?$", re.IGNORECASE
)
BARE_REFERENCE_LIST_RE = re.compile(
    r"\s*E?\d{2,}(?:\s*-\s*E?\d{2,})?(?:\s+PASS)?"
    r"(?:\s*[,;\u3001\uff0c\uff1b]\s*E?\d{2,}(?:\s*-\s*E?\d{2,})?(?:\s+PASS)?)*\s*",
    re.IGNORECASE,
)
ROADMAP_LIST_ITEM_RE = re.compile(
    r"^(?P<prefix>\s*[-*]\s+\[)(?P<mark>[ xX])(?P<suffix>\]\s+)"
    r"(?P<label>\[?)(?P<quote>`?)(?P<id>\d{2,})(?P=quote)(?P<rest>.*)$"
)
ROADMAP_HEADING_ITEM_RE = re.compile(
    r"^(?P<prefix>\s*#{1,6}\s+\[)(?P<mark>[ xX])(?P<suffix>\]\s+)"
    r"(?P<label>\[?)(?P<quote>`?)(?P<id>\d{2,})(?P=quote)(?P<rest>.*)$"
)
FRONTIER_MARKER_START = "<!-- ticketctl:frontier:start -->"
FRONTIER_MARKER_END = "<!-- ticketctl:frontier:end -->"
COMPLETION_RECEIPT_MARKER_START = "<!-- ticketctl:completion-receipts:start -->"
COMPLETION_RECEIPT_MARKER_END = "<!-- ticketctl:completion-receipts:end -->"
EXTERNAL_CHECKPOINT_MARKER_START = "<!-- ticketctl:external-checkpoints:start -->"
EXTERNAL_CHECKPOINT_MARKER_END = "<!-- ticketctl:external-checkpoints:end -->"
EXTERNAL_CHECKPOINT_ANCHOR_SECTION = "External Checkpoint Anchor"
EXTERNAL_CHECKPOINT_RESULT_SECTION = "External Checkpoint Result"
EXTERNAL_EXECUTION_SECTIONS = frozenset(
    {EXTERNAL_CHECKPOINT_RESULT_SECTION, EXTERNAL_CHECKPOINT_ANCHOR_SECTION}
)
SUPPORTED_EXTERNAL_CONTROLLER_VERSIONS = frozenset({3, 4, 5, CONTROLLER_VERSION})
SHA256_RE = r"(?:sha256:)?[0-9a-f]{64}"
COMPLETION_RECEIPT_RE = re.compile(
    r"^- Verification PASS at `sha256:[0-9a-f]{64}` "
    r"with plan `[0-9a-f]{64}` and contract `[0-9a-f]{64}` "
    r"for checks: (?:None|`[^`\n]+`(?:, `[^`\n]+`)*?)\.$"
)
EXTERNAL_CHECKPOINT_ANCHOR_RE = re.compile(
    r"^- External checkpoint `(?P<check>[^`\n]+)` record "
    r"`(?P<record_hash>sha256:[0-9a-f]{64})` bound to Core result "
    r"`(?P<core_result_hash>sha256:[0-9a-f]{64})`\.$"
)
EXTERNAL_CHECKPOINT_ANCHOR_SHAPE_RE = re.compile(
    r"^- External checkpoint `[^`\n]+` record `[^`\n]+` "
    r"bound to Core result `[^`\n]+`\.$"
)
EXTERNAL_ANCHOR_CHECK_HINT_RE = re.compile(
    r"^- External checkpoint `(?P<check>[^`\n]+)`"
)
SHELL_OPERATORS = {"&&", "||", ";", "|", ">", ">>", "<", "2>", "2>>", "&"}
DENIED_EXECUTABLES = {
    "busybox",
    "chmod",
    "chown",
    "curl",
    "dd",
    "env",
    "gh",
    "kill",
    "mkfs",
    "mount",
    "mv",
    "nc",
    "netcat",
    "reboot",
    "rm",
    "scp",
    "shutdown",
    "ssh",
    "sudo",
    "wget",
}
DENIED_SHELLS = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "zsh",
}
INTERPRETER_EVAL_FLAGS = {
    "node": {"-e", "--eval", "-p", "--print"},
    "nodejs": {"-e", "--eval", "-p", "--print"},
    "perl": {"-e", "-E"},
    "php": {"-r"},
    "python": {"-c"},
    "python2": {"-c"},
    "python3": {"-c"},
    "ruby": {"-e"},
}


class TicketCtlError(Exception):
    """Expected command failure with structured details."""

    def __init__(self, message: str, *, kind: str = "ERROR", details: Any = None):
        super().__init__(message)
        self.kind = kind
        self.details = details


class ValidationFailure(TicketCtlError):
    def __init__(self, errors: Sequence[str]):
        super().__init__(
            "Ticket validation failed",
            kind="VALIDATION_ERROR",
            details={"errors": list(errors)},
        )


class StateDriftFailure(TicketCtlError):
    def __init__(self, message: str, details: Any):
        super().__init__(message, kind="STATE_DRIFT", details=details)


@dataclasses.dataclass(frozen=True)
class Dependency:
    ticket_id: str
    external: bool
    condition: str | None = None


@dataclasses.dataclass
class Ticket:
    ticket_id: str
    path: Path
    text: str
    title: str
    metadata: dict[str, str]
    sections: dict[str, str]
    dependencies: list[Dependency]
    unblocks: list[Dependency]
    completion_gates: frozenset[str]

    @property
    def status(self) -> str:
        return self.metadata["Status"]

    @property
    def owner_repo(self) -> str:
        return self.metadata["Owner Repo"]


@dataclasses.dataclass
class Tracker:
    feature_root: Path
    repo_root: Path
    repo_name: str
    tickets: dict[str, Ticket]
    spec_path: Path
    roadmap_path: Path | None
    config_path: Path | None


@dataclasses.dataclass
class VerificationSandbox:
    tracker: Tracker
    original_repo_root: Path
    snapshot_root: Path | None
    runtime_root: Path | None
    backend: str | None
    contaminated: bool = False


@dataclasses.dataclass(frozen=True)
class NextPreflight:
    ticket: Ticket
    baseline: dict[str, Any]
    frontier: list[str]
    recovered_activation: bool
    drift_summary: dict[str, int]


def _ticket_sort_key(ticket_id: str) -> tuple[int, str]:
    return int(ticket_id), ticket_id


def _sections(text: str) -> tuple[dict[str, str], list[str]]:
    matches = list(SECTION_RE.finditer(text))
    found: dict[str, str] = {}
    duplicates: list[str] = []
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if name in found:
            duplicates.append(name)
        else:
            found[name] = text[match.end() : end].strip()
    return found, duplicates


def _section_bodies(text: str, name: str) -> list[str]:
    matches = list(SECTION_RE.finditer(text))
    return [
        text[
            match.end() : (
                matches[index + 1].start() if index + 1 < len(matches) else len(text)
            )
        ].strip()
        for index, match in enumerate(matches)
        if match.group(1).strip() == name
    ]


def _metadata_value(text: str, key: str) -> str | None:
    pattern = re.compile(rf"^\*\*{re.escape(key)}:\*\*[ \t]*(.*?)[ \t]*$", re.MULTILINE)
    matches = pattern.findall(text)
    if len(matches) != 1:
        return None
    return matches[0].strip()


def _optional_metadata_value(text: str, key: str) -> tuple[str | None, str | None]:
    pattern = re.compile(rf"^\*\*{re.escape(key)}:\*\*[ \t]*(.*?)[ \t]*$", re.MULTILINE)
    matches = pattern.findall(text)
    if len(matches) > 1:
        return None, f"duplicate optional metadata {key!r}"
    if not matches:
        return None, None
    value = matches[0].strip()
    if not value:
        return None, f"optional metadata {key!r} cannot be empty"
    return value, None


def _parse_completion_gates(value: str | None) -> frozenset[str]:
    if value is None or value.strip().lower() == "none":
        return frozenset()
    normalized = re.sub(r"[;\u3001\uff0c\uff1b]", ",", value)
    values = [item.strip().upper() for item in normalized.split(",") if item.strip()]
    if not values or any(
        not re.fullmatch(r"(?:DEL|AC|EXT|INV|EVD)-[1-9]\d*", item) for item in values
    ):
        raise ValueError(
            "Core Completion Gates must be None or a comma-delimited list of requirement ids"
        )
    if len(values) != len(set(values)):
        raise ValueError("Core Completion Gates contains duplicate requirement ids")
    return frozenset(values)


def _expand_reference(reference: str) -> list[Dependency]:
    match = REFERENCE_RE.fullmatch(reference.strip())
    if not match:
        raise ValueError(f"invalid ticket reference: {reference!r}")
    start_raw, end_raw, condition = match.groups()
    start_external = start_raw.upper().startswith("E")
    start_digits = start_raw[1:] if start_external else start_raw
    normalized_condition = condition.upper() if condition else None
    if end_raw is None:
        return [
            Dependency(
                ticket_id=("E" if start_external else "") + start_digits,
                external=start_external,
                condition=normalized_condition,
            )
        ]

    end_external = end_raw.upper().startswith("E")
    if start_external != end_external:
        raise ValueError(f"mixed Core/External dependency range: {reference!r}")
    end_digits = end_raw[1:] if end_external else end_raw
    start_number = int(start_digits)
    end_number = int(end_digits)
    if end_number < start_number or end_number - start_number > 1000:
        raise ValueError(f"invalid ticket dependency range: {reference!r}")
    width = max(len(start_digits), len(end_digits))
    prefix = "E" if start_external else ""
    return [
        Dependency(
            ticket_id=f"{prefix}{number:0{width}d}",
            external=start_external,
            condition=normalized_condition,
        )
        for number in range(start_number, end_number + 1)
    ]


def parse_references(value: str, *, strict: bool = True) -> list[Dependency]:
    if value.strip().lower() == "none":
        return []
    references: list[str] = []
    quoted = list(re.finditer(r"`([^`\n]+)`", value))
    for match in quoted:
        candidate = match.group(1).strip()
        if REFERENCE_RE.fullmatch(candidate):
            suffix = value[match.end() :]
            if not candidate.upper().endswith(" PASS") and re.match(
                r"^\s+PASS\b", suffix, flags=re.IGNORECASE
            ):
                candidate += " PASS"
            references.append(candidate)
        elif re.match(r"^E?\d", candidate, flags=re.IGNORECASE):
            raise ValueError(f"invalid explicit ticket reference: {candidate!r}")

    if not quoted and BARE_REFERENCE_LIST_RE.fullmatch(value):
        normalized = re.sub(r"[;\u3001\uff0c\uff1b]", ",", value)
        references = [item.strip() for item in normalized.split(",") if item.strip()]
    elif not references and strict and not re.search(r"\bnone\b", value, re.IGNORECASE):
        raise ValueError("no explicit ticket references found")

    result: list[Dependency] = []
    seen: set[str] = set()
    for reference in references:
        for dependency in _expand_reference(reference):
            if dependency.ticket_id in seen:
                raise ValueError(
                    f"duplicate ticket reference: {dependency.ticket_id}"
                    + (f" {dependency.condition}" if dependency.condition else "")
                )
            result.append(dependency)
            seen.add(dependency.ticket_id)
    return result


def _load_feature_config(feature_root: Path) -> tuple[dict[str, Any], Path | None]:
    path = feature_root / ".ticketctl.json"
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TicketCtlError(
            f"cannot read feature config {path}: {exc}", kind="CONFIG_INVALID"
        ) from exc
    if not isinstance(value, dict):
        raise TicketCtlError(
            "feature config must contain a JSON object", kind="CONFIG_INVALID"
        )
    unknown = sorted(set(value) - {"roadmap", "canonical_spec"})
    if unknown:
        raise TicketCtlError(
            f"feature config contains unknown keys: {', '.join(unknown)}",
            kind="CONFIG_INVALID",
        )
    return value, path


def _resolve_feature_file(feature_root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = feature_root / path
    path = path.resolve()
    if not _is_within(path, feature_root):
        raise TicketCtlError(f"{label} path escapes feature root: {value!r}")
    return path


def _discover_roadmap(
    feature_root: Path, override: str | None, config: dict[str, Any]
) -> Path | None:
    configured = override if override is not None else config.get("roadmap")
    if configured is not None:
        if not isinstance(configured, str) or not configured.strip():
            raise TicketCtlError("configured roadmap path must be a non-empty string")
        path = _resolve_feature_file(feature_root, configured, "roadmap")
        return path
    candidates = [
        feature_root / name
        for name in ("roadmap.md", "implementation-roadmap.md")
        if (feature_root / name).is_file()
    ]
    if len(candidates) > 1:
        raise TicketCtlError(
            "multiple roadmap candidates found; pass --roadmap or configure .ticketctl.json",
            kind="ROADMAP_AMBIGUOUS",
            details={"candidates": [str(path) for path in candidates]},
        )
    return candidates[0].resolve() if candidates else None


def _find_repo_root(feature_root: Path) -> Path:
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
    return feature_root.parent.resolve()


def _parse_ticket(path: Path) -> tuple[Ticket | None, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, [f"{path.name}: cannot read UTF-8 Markdown: {exc}"]
    return _parse_ticket_text(path, text)


def _parse_ticket_text(path: Path, text: str) -> tuple[Ticket | None, list[str]]:
    errors: list[str] = []
    filename_match = TICKET_FILE_RE.fullmatch(path.name)
    if not filename_match:
        return None, [f"{path.name}: filename must match <NN>-<slug>.md"]
    ticket_id = filename_match.group(1)
    if not filename_match.group(2).strip():
        errors.append(f"{path.name}: ticket slug is empty")
    heading_matches = HEADING_RE.findall(text)
    if len(heading_matches) != 1:
        errors.append(f"{path.name}: expected exactly one level-1 ticket heading")
        heading_id = ticket_id
        title = ""
    else:
        heading_id = heading_matches[0]
        heading_line = HEADING_RE.search(text)
        assert heading_line is not None
        title = heading_line.group(0).lstrip("#").strip()
        if heading_id != ticket_id:
            errors.append(
                f"{path.name}: heading ticket id {heading_id!r} does not match filename id {ticket_id!r}"
            )

    metadata: dict[str, str] = {}
    for key in REQUIRED_METADATA:
        value = _metadata_value(text, key)
        if value is None or not value:
            errors.append(f"{path.name}: missing or duplicate metadata {key!r}")
        else:
            metadata[key] = value

    section_map, duplicate_sections = _sections(text)
    for name in duplicate_sections:
        if name not in EXTERNAL_EXECUTION_SECTIONS:
            errors.append(f"{path.name}: duplicate section {name!r}")
    for name in REQUIRED_SECTIONS:
        if name not in section_map:
            errors.append(f"{path.name}: missing section {name!r}")
    dependencies: list[Dependency] = []
    unblocks: list[Dependency] = []
    completion_gates: frozenset[str] = frozenset()
    if "Depends On" in metadata:
        try:
            dependencies = parse_references(metadata["Depends On"])
        except ValueError as exc:
            errors.append(f"{path.name}: Depends On: {exc}")
    if "Unblocks" in metadata:
        try:
            unblocks = parse_references(metadata["Unblocks"], strict=False)
        except ValueError as exc:
            errors.append(f"{path.name}: Unblocks: {exc}")

    gate_value, gate_error = _optional_metadata_value(
        text, CORE_COMPLETION_GATES_METADATA
    )
    if gate_error:
        errors.append(f"{path.name}: {gate_error}")
    elif gate_value is not None:
        metadata[CORE_COMPLETION_GATES_METADATA] = gate_value
        try:
            completion_gates = _parse_completion_gates(gate_value)
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")

    if metadata.get("Status") not in STATUSES:
        errors.append(
            f"{path.name}: Status must be one of {', '.join(STATUSES)}; got {metadata.get('Status')!r}"
        )

    if any(key not in metadata for key in REQUIRED_METADATA):
        return None, errors
    return (
        Ticket(
            ticket_id=ticket_id,
            path=path,
            text=text,
            title=title,
            metadata=metadata,
            sections=section_map,
            dependencies=dependencies,
            unblocks=unblocks,
            completion_gates=completion_gates,
        ),
        errors,
    )


def load_tracker(feature_root: Path, roadmap_override: str | None = None) -> Tracker:
    root = feature_root.expanduser().resolve()
    issues = root / "issues"
    errors: list[str] = []
    if not root.is_dir():
        raise ValidationFailure([f"feature root does not exist: {root}"])
    if not issues.is_dir():
        raise ValidationFailure([f"issues directory does not exist: {issues}"])

    config, config_path = _load_feature_config(root)
    roadmap_path = _discover_roadmap(root, roadmap_override, config)
    spec_value = config.get("canonical_spec", "spec.md")
    if not isinstance(spec_value, str) or not spec_value.strip():
        raise TicketCtlError("configured canonical_spec must be a non-empty string")
    spec_path = _resolve_feature_file(root, spec_value, "canonical spec")
    if not spec_path.is_file():
        errors.append(f"canonical spec does not exist: {spec_path}")

    paths = sorted(issues.glob("*.md"), key=lambda item: item.name)
    if not paths:
        raise ValidationFailure([f"no ticket Markdown files found in {issues}"])

    tickets: dict[str, Ticket] = {}
    seen_paths: dict[str, Path] = {}
    for path in paths:
        filename_match = TICKET_FILE_RE.fullmatch(path.name)
        if filename_match:
            possible_id = filename_match.group(1)
            if possible_id in seen_paths:
                errors.append(
                    f"duplicate ticket id {possible_id!r}: {seen_paths[possible_id].name}, {path.name}"
                )
            else:
                seen_paths[possible_id] = path
        ticket, ticket_errors = _parse_ticket(path)
        errors.extend(ticket_errors)
        if ticket is not None and ticket.ticket_id not in tickets:
            tickets[ticket.ticket_id] = ticket

    ids = set(seen_paths)
    for ticket in tickets.values():
        for dependency in ticket.dependencies:
            if dependency.external:
                continue
            if dependency.ticket_id == ticket.ticket_id:
                errors.append(f"{ticket.path.name}: ticket cannot depend on itself")
            elif dependency.ticket_id not in ids:
                errors.append(
                    f"{ticket.path.name}: missing Core dependency {dependency.ticket_id!r}"
                )
        requirement_ids = set(_requirement_units(ticket))
        unknown_gates = sorted(ticket.completion_gates - requirement_ids)
        if unknown_gates:
            errors.append(
                f"{ticket.path.name}: Core Completion Gates references unknown requirements: "
                + ", ".join(unknown_gates)
            )

    cycle = _find_cycle(tickets)
    if cycle:
        errors.append(f"dependency cycle: {' -> '.join(cycle)}")
    if errors:
        raise ValidationFailure(errors)

    repo_root = _find_repo_root(root)
    return Tracker(
        feature_root=root,
        repo_root=repo_root,
        repo_name=repo_root.name,
        tickets=tickets,
        spec_path=spec_path,
        roadmap_path=roadmap_path,
        config_path=config_path,
    )


def _normalize_contract_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(line.rstrip() for line in normalized.splitlines())


def _is_completion_receipt_candidate(line: str) -> bool:
    return COMPLETION_RECEIPT_RE.fullmatch(line) is not None


def _is_external_anchor_candidate(line: str) -> bool:
    return EXTERNAL_CHECKPOINT_ANCHOR_SHAPE_RE.fullmatch(line) is not None


def _frozen_evidence(body: str) -> str:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    frozen_lines: list[str] = []
    active_marker: str | None = None
    for raw_line in normalized.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == COMPLETION_RECEIPT_MARKER_START:
            active_marker = "completion"
            continue
        if stripped == EXTERNAL_CHECKPOINT_MARKER_START:
            active_marker = "external"
            continue
        if stripped in (
            COMPLETION_RECEIPT_MARKER_END,
            EXTERNAL_CHECKPOINT_MARKER_END,
        ):
            active_marker = None
            continue
        if _is_completion_receipt_candidate(stripped) and active_marker in (
            None,
            "completion",
        ):
            # Revision 2 receipts were exact, markerless entries in Evidence.
            continue
        if _is_external_anchor_candidate(stripped) and active_marker == "external":
            continue
        frozen_lines.append(line)
    frozen = _normalize_contract_text("\n".join(frozen_lines))
    return "" if not frozen or _is_empty_requirement(frozen) else frozen


def _completion_receipt_data(body: str) -> dict[str, Any]:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    completion_receipts: list[str] = []
    errors: list[str] = []
    active_marker = False
    marker_count = 0

    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        if stripped == COMPLETION_RECEIPT_MARKER_START:
            if active_marker or marker_count:
                errors.append("completion receipt marker block is nested or duplicated")
            active_marker = True
            marker_count += 1
            continue
        if stripped == COMPLETION_RECEIPT_MARKER_END:
            if not active_marker:
                errors.append("completion receipt marker block is incomplete")
            active_marker = False
            continue

        if active_marker:
            if not stripped:
                continue
            if not COMPLETION_RECEIPT_RE.fullmatch(stripped):
                errors.append("completion receipt marker contains an invalid entry")
            elif stripped in completion_receipts:
                errors.append("completion receipt marker contains a duplicate entry")
            else:
                completion_receipts.append(stripped)
            continue

        # Version 2 wrote exact completion receipts without a marker block.
        if COMPLETION_RECEIPT_RE.fullmatch(stripped):
            if stripped in completion_receipts:
                errors.append("completion receipt is duplicated")
            else:
                completion_receipts.append(stripped)

    if active_marker:
        errors.append("completion receipt marker block is not closed")
    return {
        "completion_receipts": completion_receipts,
        "errors": errors,
    }


def _external_anchor_data(
    body: str, *, label: str = "external anchor"
) -> dict[str, Any]:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    anchors: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    invalid_check_ids: set[str] = set()
    active_marker = False
    marker_count = 0
    boundary_invalid = False

    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        if stripped == EXTERNAL_CHECKPOINT_MARKER_START:
            if active_marker or marker_count:
                errors.append(f"{label} marker block is nested or duplicated")
                boundary_invalid = True
            active_marker = True
            marker_count += 1
            continue
        if stripped == EXTERNAL_CHECKPOINT_MARKER_END:
            if not active_marker:
                errors.append(f"{label} marker block is incomplete")
                boundary_invalid = True
            active_marker = False
            continue
        if not stripped:
            continue
        if not active_marker:
            hint = EXTERNAL_ANCHOR_CHECK_HINT_RE.match(stripped)
            if hint is not None:
                check_id = hint.group("check")
                invalid_check_ids.add(check_id)
                errors.append(
                    f"external checkpoint {check_id} anchor is outside its marker block"
                )
            continue
        match = EXTERNAL_CHECKPOINT_ANCHOR_RE.fullmatch(stripped)
        if match is None:
            hint = EXTERNAL_ANCHOR_CHECK_HINT_RE.match(stripped)
            if hint is not None:
                invalid_check_ids.add(hint.group("check"))
            errors.append(f"{label} marker contains an invalid entry")
            continue
        check_id = match.group("check")
        if check_id in anchors:
            invalid_check_ids.add(check_id)
            errors.append(f"{label} marker contains duplicate check {check_id!r}")
            continue
        anchors[check_id] = match.groupdict()

    if active_marker:
        errors.append(f"{label} marker block is not closed")
        boundary_invalid = True
    if boundary_invalid:
        invalid_check_ids.update(anchors)
    for check_id in invalid_check_ids:
        anchors.pop(check_id, None)
    return {
        "anchors": anchors,
        "errors": errors,
        "invalid_check_ids": invalid_check_ids,
        "marker_count": marker_count,
    }


def _external_anchor_channels(ticket: Ticket) -> dict[str, Any]:
    bodies = _section_bodies(ticket.text, EXTERNAL_CHECKPOINT_ANCHOR_SECTION)
    legacy = not bodies
    if legacy:
        bodies = [ticket.sections.get("Evidence", "")]

    anchors: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    invalid_check_ids: set[str] = set()
    if not legacy and len(bodies) > 1:
        errors.append(f"duplicate section {EXTERNAL_CHECKPOINT_ANCHOR_SECTION!r}")

    for index, body in enumerate(bodies, start=1):
        label = (
            "legacy Evidence external anchor"
            if legacy
            else f"external anchor section {index}"
        )
        parsed = _external_anchor_data(body, label=label)
        errors.extend(parsed["errors"])
        invalid_check_ids.update(parsed["invalid_check_ids"])
        for check_id, anchor in parsed["anchors"].items():
            if check_id in anchors:
                invalid_check_ids.add(check_id)
                errors.append(f"external checkpoint {check_id} anchor is duplicated")
            else:
                anchors[check_id] = anchor

    for check_id in invalid_check_ids:
        anchors.pop(check_id, None)
    return {
        "anchors": anchors,
        "errors": errors,
        "invalid_check_ids": invalid_check_ids,
        "legacy": legacy,
    }


def _append_completion_receipt(body: str, entry: str) -> str:
    data = _completion_receipt_data(body)
    if data["errors"]:
        raise ValueError("; ".join(data["errors"]))
    if entry in data["completion_receipts"]:
        return body
    if COMPLETION_RECEIPT_MARKER_START in body:
        return body.replace(
            COMPLETION_RECEIPT_MARKER_END,
            f"{entry}\n{COMPLETION_RECEIPT_MARKER_END}",
            1,
        )
    prefix = body.rstrip()
    block = (
        f"{COMPLETION_RECEIPT_MARKER_START}\n{entry}\n{COMPLETION_RECEIPT_MARKER_END}"
    )
    return f"{prefix}\n\n{block}".strip() if prefix else block


def _normalized_ticket_contract(text: str) -> dict[str, Any]:
    section_map, duplicates = _sections(text)
    frozen_duplicates = sorted(set(duplicates) - EXTERNAL_EXECUTION_SECTIONS)
    if frozen_duplicates:
        raise ValueError(f"duplicate frozen sections: {', '.join(frozen_duplicates)}")
    metadata: dict[str, str | None] = {}
    for key in ("Wave / Layer", "Owner Repo", "Depends On", "Unblocks"):
        value = _metadata_value(text, key)
        if value is None:
            raise ValueError(f"missing frozen metadata {key!r}")
        metadata[key] = re.sub(r"\s+", " ", value.strip())
    gate_value, gate_error = _optional_metadata_value(
        text, CORE_COMPLETION_GATES_METADATA
    )
    if gate_error:
        raise ValueError(gate_error)
    completion_gates = sorted(_parse_completion_gates(gate_value))
    dependencies = parse_references(metadata["Depends On"] or "None")
    frozen_sections = {}
    for name in (
        "Delivery",
        "Core Acceptance",
        "External Integration Evidence",
        "Invariants / Out Of Scope",
        "Evidence",
    ):
        if name not in section_map:
            raise ValueError(f"missing frozen section {name!r}")
        frozen_sections[name] = (
            _frozen_evidence(section_map[name])
            if name == "Evidence"
            else _normalize_contract_text(section_map[name])
        )
    return {
        "metadata": metadata,
        "completion_gates": completion_gates,
        "dependencies": [
            {
                "ticket": dependency.ticket_id,
                "external": dependency.external,
                "condition": dependency.condition,
            }
            for dependency in dependencies
        ],
        "sections": frozen_sections,
    }


def _git_repository_root(path: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return Path(completed.stdout.strip()).resolve()


def _git_head_content(git_root: Path, relative: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "-C", str(git_root), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def tracker_baseline(tracker: Tracker) -> dict[str, Any]:
    git_root = _git_repository_root(tracker.repo_root)
    if git_root is None:
        return {
            "applicable": False,
            "status": "NOT_A_GIT_REPOSITORY",
            "files": [],
        }

    required = [tracker.spec_path]
    required.extend(ticket.path for ticket in tracker.tickets.values())
    if tracker.config_path is not None:
        required.append(tracker.config_path)
    required = sorted(set(required), key=lambda path: path.as_posix())
    untracked: list[str] = []
    no_baseline_commit: list[str] = []
    missing: list[str] = []
    unpublished_spec_files: list[str] = []
    frozen_contract_drift: list[str] = []
    config_drift: list[str] = []
    relative_files: list[str] = []
    for path in required:
        if not path.is_file():
            missing.append(str(path))
            continue
        if not _is_within(path, git_root):
            no_baseline_commit.append(str(path))
            continue
        relative = path.relative_to(git_root).as_posix()
        relative_files.append(relative)
        tracked = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            check=False,
        )
        if tracked.returncode != 0:
            untracked.append(relative)
            continue
        committed = subprocess.run(
            ["git", "-C", str(git_root), "cat-file", "-e", f"HEAD:{relative}"],
            capture_output=True,
            check=False,
        )
        if committed.returncode != 0:
            no_baseline_commit.append(relative)

    def relative_if_published(path: Path) -> str | None:
        if not _is_within(path, git_root):
            return None
        relative = path.relative_to(git_root).as_posix()
        if relative in untracked or relative in no_baseline_commit:
            return None
        return relative

    spec_relative = relative_if_published(tracker.spec_path)
    if spec_relative is not None:
        head_content = _git_head_content(git_root, spec_relative)
        try:
            current_content = tracker.spec_path.read_bytes()
        except OSError:
            current_content = None
        if head_content is None or current_content != head_content:
            unpublished_spec_files.append(spec_relative)

    if tracker.config_path is not None:
        config_relative = relative_if_published(tracker.config_path)
        if config_relative is not None:
            head_content = _git_head_content(git_root, config_relative)
            try:
                current_content = tracker.config_path.read_bytes()
            except OSError:
                current_content = None
            if head_content is None or current_content != head_content:
                config_drift.append(config_relative)

    for ticket in tracker.tickets.values():
        relative = relative_if_published(ticket.path)
        if relative is None:
            continue
        head_content = _git_head_content(git_root, relative)
        try:
            baseline_contract = (
                _normalized_ticket_contract(head_content.decode("utf-8"))
                if head_content is not None
                else None
            )
            current_contract = _normalized_ticket_contract(ticket.text)
        except (UnicodeError, ValueError):
            frozen_contract_drift.append(relative)
            continue
        if baseline_contract != current_contract:
            frozen_contract_drift.append(relative)

    head = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    ok = (
        not (
            untracked
            or no_baseline_commit
            or missing
            or unpublished_spec_files
            or frozen_contract_drift
            or config_drift
        )
        and head.returncode == 0
    )
    return {
        "applicable": True,
        "status": "PASS" if ok else "TRACKER_BASELINE_MISSING",
        "revision": head.stdout.strip() if head.returncode == 0 else None,
        "files": relative_files,
        "untracked_files": untracked,
        "no_baseline_commit": no_baseline_commit,
        "missing_files": missing,
        "unpublished_spec_files": sorted(unpublished_spec_files),
        "frozen_contract_drift": sorted(frozen_contract_drift),
        "config_drift": sorted(config_drift),
    }


def _require_tracker_baseline(tracker: Tracker) -> dict[str, Any]:
    baseline = tracker_baseline(tracker)
    if baseline["applicable"] and baseline["status"] != "PASS":
        raise TicketCtlError(
            "canonical tracker artifacts do not have a committed Git baseline",
            kind="TRACKER_BASELINE_MISSING",
            details={
                **baseline,
                "state": "STATE_DRIFT",
                "repair": (
                    "Publish the canonical spec, feature config, every normalized frozen ticket "
                    "contract, then retry. The roadmap is derived and ticketctl will not commit any files."
                ),
            },
        )
    return baseline


def _find_cycle(tickets: dict[str, Ticket]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(ticket_id: str) -> list[str]:
        if ticket_id in visiting:
            start = stack.index(ticket_id)
            return stack[start:] + [ticket_id]
        if ticket_id in visited:
            return []
        visiting.add(ticket_id)
        stack.append(ticket_id)
        for dependency in tickets[ticket_id].dependencies:
            if dependency.external or dependency.ticket_id not in tickets:
                continue
            cycle = visit(dependency.ticket_id)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(ticket_id)
        visited.add(ticket_id)
        return []

    for ticket_id in sorted(tickets, key=_ticket_sort_key):
        cycle = visit(ticket_id)
        if cycle:
            return cycle
    return []


def frontier(tracker: Tracker) -> list[str]:
    ready: list[str] = []
    for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key):
        ticket = tracker.tickets[ticket_id]
        if ticket.status != "Open" or ticket.owner_repo != tracker.repo_name:
            continue
        core_dependencies = [dep for dep in ticket.dependencies if not dep.external]
        if all(_dependency_satisfied(tracker, dep)[0] for dep in core_dependencies):
            ready.append(ticket_id)
    return ready


def _dependency_satisfied(
    tracker: Tracker, dependency: Dependency
) -> tuple[bool, str | None]:
    ticket = tracker.tickets[dependency.ticket_id]
    if ticket.status != "Done":
        return False, f"ticket {dependency.ticket_id} is {ticket.status}, not Done"
    historical = _historical_completion_integrity(tracker, ticket)
    if historical["state"] == "INVALID":
        return (
            False,
            f"ticket {dependency.ticket_id} has invalid historical completion integrity",
        )
    if dependency.condition == "PASS" and not _dependency_has_valid_pass(
        tracker, ticket
    ):
        return (
            False,
            f"ticket {dependency.ticket_id} lacks a current-source valid PASS result",
        )
    return True, None


def _replace_metadata(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^(\*\*{re.escape(key)}:\*\*)[ \t]*.*$", re.MULTILINE)
    replaced, count = pattern.subn(rf"\g<1> {value}", text)
    if count != 1:
        raise TicketCtlError(f"cannot replace unique metadata field {key!r}")
    return replaced


def _replace_or_insert_section(text: str, name: str, body: str) -> str:
    matches = list(SECTION_RE.finditer(text))
    named = [
        (index, match)
        for index, match in enumerate(matches)
        if match.group(1).strip() == name
    ]
    if len(named) > 1:
        raise TicketCtlError(f"cannot update duplicate section {name!r}")
    replacement = f"## {name}\n\n{body.rstrip()}\n\n"
    if named:
        index, match = named[0]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[: match.start()] + replacement + text[end:]

    comments = [match for match in matches if match.group(1).strip() == "Comments"]
    if len(comments) != 1:
        raise TicketCtlError("cannot insert section before unique 'Comments' section")
    position = comments[0].start()
    prefix = text[:position].rstrip() + "\n\n"
    return prefix + replacement + text[position:]


def _replace_or_collapse_sections(text: str, name: str, body: str) -> str:
    matches = list(SECTION_RE.finditer(text))
    named = [
        (index, match)
        for index, match in enumerate(matches)
        if match.group(1).strip() == name
    ]
    if len(named) <= 1:
        return _replace_or_insert_section(text, name, body)

    replacement = f"## {name}\n\n{body.rstrip()}\n\n"
    rendered: list[str] = []
    cursor = 0
    for occurrence, (index, match) in enumerate(named):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        rendered.append(text[cursor : match.start()])
        if occurrence == 0:
            rendered.append(replacement)
        cursor = end
    rendered.append(text[cursor:])
    return "".join(rendered)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_NO_EXPECTED_CONTENT = object()


def _atomic_write(
    path: Path,
    text: str,
    *,
    expected_text: str | None | object = _NO_EXPECTED_CONTENT,
) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        if expected_text is None:
            if path.exists():
                raise TicketCtlError(
                    f"refusing to replace concurrently created file {path}",
                    kind="CONCURRENT_MODIFICATION",
                    details={
                        "path": str(path),
                        "repair": "Review and preserve the concurrent file, then rerun the command.",
                    },
                )
        elif expected_text is not _NO_EXPECTED_CONTENT:
            assert isinstance(expected_text, str)
            try:
                current_text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise TicketCtlError(
                    f"cannot verify current content before writing {path}: {exc}",
                    kind="CONCURRENT_MODIFICATION",
                ) from exc
            if current_text != expected_text:
                raise TicketCtlError(
                    f"refusing to overwrite concurrently modified file {path}",
                    kind="CONCURRENT_MODIFICATION",
                    details={
                        "path": str(path),
                        "expected_content_hash": _content_hash(expected_text),
                        "current_content_hash": _content_hash(current_text),
                        "repair": "Review and preserve the concurrent edit, then rerun the command.",
                    },
                )
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()


@contextlib.contextmanager
def _feature_lock(feature_root: Path) -> Iterator[None]:
    digest = hashlib.sha256(str(feature_root.resolve()).encode("utf-8")).hexdigest()[
        :24
    ]
    lock_path = Path(tempfile.gettempdir()) / f"ticketctl-{digest}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_ticket(tracker: Tracker, ticket_id: str) -> Ticket:
    ticket = tracker.tickets.get(ticket_id)
    if ticket is None:
        raise TicketCtlError(f"unknown Core ticket {ticket_id!r}")
    return ticket


def _flush_requirement_paragraph(paragraph: list[str], candidates: list[str]) -> None:
    if paragraph:
        value = " ".join(paragraph).strip()
        if value and not _is_empty_requirement(value):
            candidates.append(value)
        paragraph.clear()


def _is_empty_requirement(value: str) -> bool:
    normalized = value.strip().lower().rstrip(".!?。！？")
    return normalized in {"none", "n/a", "not applicable", "not yet", "no", "无"}


def _requirement_units(ticket: Ticket) -> dict[str, str]:
    units: dict[str, str] = {}
    for section_name, prefix in REQUIREMENT_SECTIONS:
        body = ticket.sections.get(section_name, "")
        if section_name == "Evidence":
            body = _frozen_evidence(body)
        candidates: list[str] = []
        paragraph: list[str] = []
        in_fence = False

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("```"):
                _flush_requirement_paragraph(paragraph, candidates)
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if not line:
                _flush_requirement_paragraph(paragraph, candidates)
                continue
            if line.startswith("#"):
                _flush_requirement_paragraph(paragraph, candidates)
                continue
            bullet = re.match(r"^(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)(.+)$", line)
            if bullet:
                _flush_requirement_paragraph(paragraph, candidates)
                value = bullet.group(1).strip()
                if value and not _is_empty_requirement(value):
                    candidates.append(value)
            else:
                paragraph.append(line)
        _flush_requirement_paragraph(paragraph, candidates)
        for index, requirement in enumerate(candidates, start=1):
            units[f"{prefix}-{index}"] = requirement
    return units


def _json_blocks(section_body: str) -> list[dict[str, Any]]:
    blocks = re.findall(
        r"```json\s*\n(.*?)\n```", section_body, flags=re.DOTALL | re.IGNORECASE
    )
    parsed: list[dict[str, Any]] = []
    for block in blocks:
        try:
            value = json.loads(block)
        except json.JSONDecodeError as exc:
            raise TicketCtlError(f"invalid JSON fenced block: {exc}") from exc
        if not isinstance(value, dict):
            raise TicketCtlError("JSON fenced block must contain an object")
        parsed.append(value)
    return parsed


def _load_plan(ticket: Ticket) -> dict[str, Any]:
    body = ticket.sections.get("Verification Plan")
    if body is None:
        raise TicketCtlError(f"ticket {ticket.ticket_id} has no Verification Plan")
    blocks = _json_blocks(body)
    if len(blocks) != 1:
        raise TicketCtlError(
            f"ticket {ticket.ticket_id} Verification Plan must contain exactly one JSON fenced block"
        )
    return blocks[0]


def _validate_plan(ticket: Ticket, plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if plan.get("version") != 1:
        errors.append("version must equal 1")
    if plan.get("ticket") != ticket.ticket_id:
        errors.append(f"ticket must equal {ticket.ticket_id!r}")
    checks = plan.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty array")
        checks = []

    seen_ids: set[str] = set()
    coverage: set[str] = set()
    blocking_coverage: set[str] = set()
    normalized_checks: list[dict[str, Any]] = []
    for index, raw_check in enumerate(checks):
        location = f"checks[{index}]"
        if not isinstance(raw_check, dict):
            errors.append(f"{location} must be an object")
            continue
        check = dict(raw_check)
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id.strip():
            errors.append(f"{location}.id must be a non-empty string")
            check_id = ""
        elif check_id in seen_ids:
            errors.append(f"duplicate check id {check_id!r}")
        else:
            seen_ids.add(check_id)
        if not isinstance(check.get("source"), str) or not check["source"].strip():
            errors.append(f"{location}.source must be a non-empty string")
        if (
            not isinstance(check.get("requirement"), str)
            or not check["requirement"].strip()
        ):
            errors.append(f"{location}.requirement must be a non-empty string")
        kind = check.get("kind")
        if kind not in CHECK_KINDS:
            errors.append(f"{location}.kind must be one of {', '.join(CHECK_KINDS)}")
        if not isinstance(check.get("expected"), str) or not check["expected"].strip():
            errors.append(f"{location}.expected must be a non-empty string")
        if not isinstance(check.get("blocking"), bool):
            errors.append(f"{location}.blocking must be a boolean")
        if kind == "command" and not isinstance(check.get("command"), (str, list)):
            errors.append(f"{location}.command is required for command checks")
        if kind == "command" and check.get("expected") != "exit_code == 0":
            errors.append(
                f"{location}.expected must equal 'exit_code == 0' for command checks"
            )
        if kind in ("command", "assertion") and "command" in check:
            timeout = check.get("timeout_seconds", 900)
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or timeout <= 0
            ):
                errors.append(f"{location}.timeout_seconds must be a positive number")
        if kind in ("file", "artifact") and not isinstance(check.get("path"), str):
            errors.append(f"{location}.path is required for {kind} checks")

        raw_covers = check.get("covers")
        if raw_covers is None and check_id:
            raw_covers = [check_id]
        if not isinstance(raw_covers, list) or not all(
            isinstance(item, str) and item for item in raw_covers
        ):
            errors.append(f"{location}.covers must be an array of requirement ids")
        else:
            coverage.update(raw_covers)
            if check.get("blocking") is True:
                blocking_coverage.update(raw_covers)
        normalized_checks.append(check)

    requirements = _requirement_units(ticket)
    missing = sorted(set(requirements) - coverage)
    unknown = sorted(coverage - set(requirements))
    if missing:
        errors.append(f"unmapped requirements: {', '.join(missing)}")
    if unknown:
        errors.append(f"unknown coverage ids: {', '.join(unknown)}")
    required_blocking = {
        requirement_id
        for requirement_id in requirements
        if requirement_id.startswith(("AC-", "INV-"))
    }
    required_blocking.update(ticket.completion_gates)
    missing_blocking = sorted(required_blocking - blocking_coverage)
    if missing_blocking:
        errors.append(
            "requirements lack blocking coverage: " + ", ".join(missing_blocking)
        )
    for check in normalized_checks:
        raw_covers = check.get("covers")
        covered = raw_covers if isinstance(raw_covers, list) else [check.get("id")]
        covered_ids = {str(requirement_id) for requirement_id in covered}
        bound_ids = [
            requirement_id
            for requirement_id in covered_ids
            if requirement_id in requirements
            and check.get("source")
            == REQUIREMENT_SECTION_BY_PREFIX.get(requirement_id.split("-", 1)[0])
            and check.get("requirement") == requirements[requirement_id]
        ]
        if not bound_ids:
            errors.append(
                f"check {check.get('id')!r} source and requirement must exactly match "
                "a covered frozen requirement"
            )
        external_ids = {
            requirement_id
            for requirement_id in covered_ids
            if requirement_id.startswith("EXT-")
        }
        if external_ids and check.get("kind") not in ("external", "manual-review"):
            errors.append(
                f"check {check.get('id')!r} covers External Integration Evidence "
                "but is not external/manual-review"
            )
        if check.get("kind") in ("external", "manual-review"):
            is_core_gate = bool(covered_ids & required_blocking)
            if is_core_gate and check.get("blocking") is not True:
                errors.append(
                    f"check {check.get('id')!r} covers a Core completion gate but is not blocking"
                )
            if external_ids and not is_core_gate and check.get("blocking") is not False:
                errors.append(
                    f"check {check.get('id')!r} is an EXT-only handoff checkpoint and must be non-blocking"
                )
    if errors:
        raise TicketCtlError(
            f"ticket {ticket.ticket_id} Verification Plan is invalid",
            kind="PLAN_INVALID",
            details={"errors": errors, "requirements": requirements},
        )
    return {"plan": plan, "requirements": requirements, "checks": normalized_checks}


def _plan_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_plan(ticket: Ticket, plan: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_plan(ticket, plan)
    normalized = dict(plan)
    normalized["checks"] = validated["checks"]
    return normalized


def _plan_markdown(plan: dict[str, Any]) -> str:
    return f"```json\n{json.dumps(plan, indent=2, sort_keys=True)}\n```"


def _load_plan_file(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TicketCtlError(
            f"cannot read Verification Plan JSON from {path}: {exc}",
            kind="PLAN_INVALID",
        ) from exc
    if not isinstance(value, dict):
        raise TicketCtlError(
            "Verification Plan input must contain one JSON object", kind="PLAN_INVALID"
        )
    return value


def _verification_plan_state(ticket: Ticket) -> str:
    if "Verification Plan" not in ticket.sections:
        return "MISSING"
    try:
        _validate_plan(ticket, _load_plan(ticket))
    except TicketCtlError:
        return "INVALID"
    return "VALID"


def _dependency_packet(dependencies: Sequence[Dependency]) -> list[dict[str, Any]]:
    return [
        {
            "ticket": dependency.ticket_id,
            "external": dependency.external,
            "condition": dependency.condition,
        }
        for dependency in dependencies
    ]


def _work_packet(tracker: Tracker, ticket: Ticket) -> dict[str, Any]:
    plan = _normalized_plan(ticket, _load_plan(ticket))
    roadmap_path = (
        str(tracker.roadmap_path)
        if tracker.roadmap_path is not None and tracker.roadmap_path.is_file()
        else None
    )
    return {
        "feature_root": str(tracker.feature_root),
        "repository_root": str(tracker.repo_root),
        "canonical_spec_path": str(tracker.spec_path),
        "ticket_id": ticket.ticket_id,
        "ticket_path": str(ticket.path),
        "status": ticket.status,
        "wave_layer": ticket.metadata["Wave / Layer"],
        "owner_repo": ticket.owner_repo,
        "depends_on": _dependency_packet(ticket.dependencies),
        "unblocks": _dependency_packet(ticket.unblocks),
        "delivery": ticket.sections["Delivery"],
        "core_acceptance": ticket.sections["Core Acceptance"],
        "external_integration_evidence": ticket.sections[
            "External Integration Evidence"
        ],
        "invariants_out_of_scope": ticket.sections["Invariants / Out Of Scope"],
        "verification_plan_hash": _plan_hash(plan),
        "verification_check_ids": [check["id"] for check in plan["checks"]],
        "roadmap_path": roadmap_path,
        "implementation_agent_boundaries": [
            "May modify only product source and tests required by this ticket.",
            "Preserve unrelated dirty-worktree changes.",
            "Do not edit tracker status, plans, results, receipts, external evidence, dependencies, frozen contract sections, or roadmap.",
            "Do not mark the ticket complete or commit without separate authorization.",
            "Return changed files, checks run, failures, and blockers to the controller.",
        ],
        "handoff_command": f"run-ticket-plan confirm {ticket.ticket_id}",
    }


def _immutable_contract_hash(tracker: Tracker, ticket: Ticket) -> str:
    contract = {
        "version": 3,
        "canonical_spec": {
            "path": tracker.spec_path.relative_to(tracker.feature_root).as_posix()
            if _is_within(tracker.spec_path, tracker.feature_root)
            else tracker.spec_path.name,
            "sha256": _hash_path(tracker.spec_path),
        },
        "ticket": ticket.ticket_id,
        "frozen_contract": _normalized_ticket_contract(ticket.text),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    candidate = (repo_root / value).resolve()
    if not _is_within(candidate, repo_root):
        raise TicketCtlError(f"path escapes repository root: {value!r}")
    return candidate


def _hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.readlink(path).encode("utf-8"))
    elif path.is_file():
        digest.update(b"file\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    elif path.is_dir():
        digest.update(b"directory\0")
        for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
            relative = child.relative_to(path).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            if child.is_symlink():
                digest.update(os.readlink(child).encode("utf-8"))
            elif child.is_file():
                with child.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
    else:
        raise TicketCtlError(f"evidence path is not a file or directory: {path}")
    return digest.hexdigest()


def source_revision(tracker: Tracker) -> str:
    digest = hashlib.sha256()
    head = subprocess.run(
        ["git", "-C", str(tracker.repo_root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    digest.update(
        (head.stdout.strip() if head.returncode == 0 else "NO_HEAD").encode("utf-8")
    )
    digest.update(b"\0")

    listed = subprocess.run(
        [
            "git",
            "-C",
            str(tracker.repo_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        check=False,
    )
    if listed.returncode == 0:
        relative_paths = {
            Path(os.fsdecode(item)) for item in listed.stdout.split(b"\0") if item
        }
        paths = [tracker.repo_root / relative for relative in relative_paths]
    else:
        paths = list(tracker.repo_root.rglob("*"))

    excluded_paths = {
        tracker.spec_path,
        *(ticket.path for ticket in tracker.tickets.values()),
    }
    if tracker.roadmap_path is not None:
        excluded_paths.add(tracker.roadmap_path)
    if tracker.config_path is not None:
        excluded_paths.add(tracker.config_path)

    for path in sorted(paths, key=lambda item: item.as_posix()):
        if ".git" in path.parts or path in excluded_paths:
            continue
        relative = path.relative_to(tracker.repo_root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            digest.update(b"L\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(b"F\0")
            digest.update(
                str(stat.S_IMODE(path.stat().st_mode)).encode("ascii") + b"\0"
            )
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"D\0")
        else:
            digest.update(b"MISSING\0")
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _verification_workspace_revision(tracker: Tracker) -> str:
    digest = hashlib.sha256(source_revision(tracker).encode("ascii"))
    tracker_paths = [
        tracker.spec_path,
        *[ticket.path for ticket in tracker.tickets.values()],
    ]
    if tracker.config_path is not None:
        tracker_paths.append(tracker.config_path)
    if tracker.roadmap_path is not None and tracker.roadmap_path.exists():
        tracker_paths.append(tracker.roadmap_path)
    for path in sorted(set(tracker_paths), key=lambda item: item.as_posix()):
        relative = path.relative_to(tracker.repo_root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(_hash_path(path).encode("ascii") + b"\0")
    return f"sha256:{digest.hexdigest()}"


def _verification_sandbox_backend() -> str | None:
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


def _snapshot_tracker(tracker: Tracker, snapshot_root: Path) -> Tracker:
    feature_relative = tracker.feature_root.relative_to(tracker.repo_root)
    snapshot_feature = snapshot_root / feature_relative
    roadmap_override = None
    if tracker.roadmap_path is not None:
        roadmap_override = tracker.roadmap_path.relative_to(
            tracker.feature_root
        ).as_posix()
    return load_tracker(snapshot_feature, roadmap_override=roadmap_override)


@contextlib.contextmanager
def _verification_sandbox(tracker: Tracker) -> Iterator[VerificationSandbox]:
    backend = _verification_sandbox_backend()
    if backend is None:
        yield VerificationSandbox(
            tracker=tracker,
            original_repo_root=tracker.repo_root,
            snapshot_root=None,
            runtime_root=None,
            backend=None,
        )
        return

    with tempfile.TemporaryDirectory(prefix="ticketctl-verify-") as directory:
        root = Path(directory).resolve()
        snapshot_root = root / "repository"
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        for name in ("home", "tmp", "cache"):
            (runtime_root / name).mkdir()
        try:
            shutil.copytree(
                tracker.repo_root,
                snapshot_root,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )
            isolated_tracker = _snapshot_tracker(tracker, snapshot_root)
        except (OSError, TicketCtlError) as exc:
            raise TicketCtlError(
                f"cannot create isolated verification repository: {exc}",
                kind="VERIFICATION_ISOLATION_FAILED",
            ) from exc
        yield VerificationSandbox(
            tracker=isolated_tracker,
            original_repo_root=tracker.repo_root,
            snapshot_root=snapshot_root,
            runtime_root=runtime_root,
            backend=backend,
        )


def _isolated_environment(sandbox: VerificationSandbox) -> dict[str, str]:
    assert sandbox.runtime_root is not None
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(
            marker in key.upper()
            for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")
        )
    }
    home = sandbox.runtime_root / "home"
    temporary = sandbox.runtime_root / "tmp"
    environment.update(
        {
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "XDG_CACHE_HOME": str(sandbox.runtime_root / "cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _map_isolated_argv(argv: Sequence[str], sandbox: VerificationSandbox) -> list[str]:
    assert sandbox.snapshot_root is not None
    mapped: list[str] = []
    for argument in argv:
        path = Path(argument)
        if path.is_absolute() and _is_within(path, sandbox.original_repo_root):
            mapped.append(
                str(
                    sandbox.snapshot_root / path.relative_to(sandbox.original_repo_root)
                )
            )
        else:
            mapped.append(argument)
    return mapped


def _sandbox_command(
    argv: Sequence[str], sandbox: VerificationSandbox
) -> tuple[list[str], Path, dict[str, str]]:
    if (
        sandbox.backend is None
        or sandbox.snapshot_root is None
        or sandbox.runtime_root is None
    ):
        raise TicketCtlError(
            "no supported secure verification sandbox is available; install bwrap on Linux",
            kind="VERIFICATION_ISOLATION_UNAVAILABLE",
        )
    mapped_argv = _map_isolated_argv(argv, sandbox)
    environment = _isolated_environment(sandbox)
    if sandbox.backend == "sandbox-exec":
        profile = "\n".join(
            (
                "(version 1)",
                "(deny default)",
                "(allow file-read*)",
                "(allow process*)",
                "(allow sysctl-read)",
                "(allow mach-lookup)",
                f"(allow file-write* (subpath {json.dumps(str(sandbox.snapshot_root))}))",
                f"(allow file-write* (subpath {json.dumps(str(sandbox.runtime_root))}))",
            )
        )
        command = ["sandbox-exec", "-p", profile, "--", *mapped_argv]
    else:
        command = [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(sandbox.snapshot_root),
            str(sandbox.snapshot_root),
            "--bind",
            str(sandbox.runtime_root),
            str(sandbox.runtime_root),
            "--chdir",
            str(sandbox.snapshot_root),
            "--",
            *mapped_argv,
        ]
    return command, sandbox.snapshot_root, environment


def _redact_output(value: str) -> str:
    redacted = value
    for key, secret in os.environ.items():
        upper = key.upper()
        if len(secret) >= 4 and any(
            marker in upper
            for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL")
        ):
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(
        r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        redacted,
        flags=re.DOTALL,
    )
    if len(redacted) > 2000:
        redacted = redacted[-2000:]
        redacted = "[truncated]\n" + redacted
    return redacted


def _command_argv(command: str | list[Any]) -> list[str]:
    if isinstance(command, str):
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise TicketCtlError(f"cannot parse command: {exc}") from exc
    elif isinstance(command, list) and all(isinstance(item, str) for item in command):
        argv = list(command)
    else:
        raise TicketCtlError("command must be a string or an array of strings")
    if not argv:
        raise TicketCtlError("command cannot be empty")
    if any(any(character in token for character in "\n\r\0") for token in argv):
        raise TicketCtlError(
            "control characters are not allowed in verification commands"
        )
    if any(
        token in SHELL_OPERATORS or re.search(r"(?:&&|\|\||[;|<>])", token)
        for token in argv
    ):
        raise TicketCtlError(
            "shell operators are not allowed in Verification Plan commands"
        )
    executable = Path(argv[0]).name.lower()
    if executable in DENIED_SHELLS:
        raise TicketCtlError(
            f"shell execution is not allowed in automatic verification: {executable}"
        )
    if executable in DENIED_EXECUTABLES:
        raise TicketCtlError(
            f"unsafe executable is not allowed in automatic verification: {executable}"
        )
    interpreter = executable
    if re.fullmatch(r"python\d+(?:\.\d+)*", interpreter):
        interpreter = "python3"
    denied_eval_flags = INTERPRETER_EVAL_FLAGS.get(interpreter, set())
    if any(argument in denied_eval_flags for argument in argv[1:]):
        raise TicketCtlError(
            f"inline code evaluation is not allowed in automatic verification: {executable}"
        )
    if executable == "git":
        if len(argv) < 2 or argv[1].startswith("-"):
            raise TicketCtlError(
                "Git global options and wrappers are not allowed in automatic verification"
            )
        if argv[1] not in {
            "diff",
            "status",
            "show",
            "log",
            "rev-parse",
            "ls-files",
        }:
            raise TicketCtlError(
                f"mutating Git command is not allowed in automatic verification: {argv[1]}"
            )
        if any(argument in {"--ext-diff", "--textconv"} for argument in argv[2:]):
            raise TicketCtlError(
                "Git options that execute external helpers are not allowed"
            )
    dangerous_subcommands = {
        "cargo": {"publish"},
        "dart": {"publish"},
        "docker": {"push"},
        "gcloud": {"deploy"},
        "kubectl": {"apply", "create", "delete", "patch", "replace"},
        "npm": {"publish"},
        "pnpm": {"publish"},
        "yarn": {"npm"},
    }
    if len(argv) > 1 and argv[1].lower() in dangerous_subcommands.get(
        executable, set()
    ):
        raise TicketCtlError(
            f"production/release subcommand is not allowed: {executable} {argv[1]}"
        )
    if executable == "find" and any(
        argument in {"-delete", "-exec", "-execdir", "-ok", "-okdir"}
        for argument in argv[1:]
    ):
        raise TicketCtlError("mutating find actions are not allowed")
    if executable in {"make", "just", "task"} and any(
        re.search(
            r"(?:^|[-_:])(deploy|prod|production|publish|release)(?:$|[-_:])", argument
        )
        for argument in argv[1:]
    ):
        raise TicketCtlError("production/release task targets are not allowed")
    return argv


def _run_command_check(
    check: dict[str, Any], tracker: Tracker, sandbox: VerificationSandbox
) -> dict[str, Any]:
    argv: list[str] | None = None
    try:
        argv = _command_argv(check["command"])
        command, working_directory, environment = _sandbox_command(argv, sandbox)
    except TicketCtlError as exc:
        return {
            "id": check["id"],
            "result": "BLOCKED",
            "exit_code": None,
            "evidence": {"command": argv, "error": str(exc)},
            "unlocking_condition": (
                "Install or enable a supported secure verification sandbox, then rerun verification."
            ),
        }

    revision_before = _verification_workspace_revision(tracker)
    observed_exit_code: int | None = None
    try:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=check.get("timeout_seconds", 900),
        )
        observed_exit_code = completed.returncode
        stdout = _redact_output(completed.stdout)
        stderr = _redact_output(completed.stderr)
        evidence = {
            "command": [_redact_output(item) for item in argv],
            "stdout": stdout,
            "stderr": stderr,
            "stdout_sha256": hashlib.sha256(
                completed.stdout.encode("utf-8")
            ).hexdigest(),
            "stderr_sha256": hashlib.sha256(
                completed.stderr.encode("utf-8")
            ).hexdigest(),
            "isolation": sandbox.backend,
        }
        result = {
            "id": check["id"],
            "result": "PASS" if completed.returncode == 0 else "FAIL",
            "exit_code": completed.returncode,
            "evidence": evidence,
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "id": check["id"],
            "result": "FAIL",
            "exit_code": None,
            "evidence": {
                "command": argv,
                "error": f"command timed out after {exc.timeout} seconds",
                "isolation": sandbox.backend,
            },
        }
    except OSError as exc:
        evidence: dict[str, Any] = {"error": str(exc)}
        if argv is not None:
            evidence["command"] = argv
        evidence["isolation"] = sandbox.backend
        result = {
            "id": check["id"],
            "result": "BLOCKED",
            "exit_code": None,
            "evidence": evidence,
            "unlocking_condition": (
                "Install or authorize the required safe local tool, then rerun verification."
            ),
        }

    revision_after = _verification_workspace_revision(tracker)
    if revision_after != revision_before:
        sandbox.contaminated = True
        result["result"] = "BLOCKED"
        result["exit_code"] = None
        result["evidence"].update(
            {
                "error": "verification command modified isolated source or tracker files",
                "observed_exit_code": observed_exit_code,
                "workspace_revision_before": revision_before,
                "workspace_revision_after": revision_after,
            }
        )
        result["unlocking_condition"] = (
            "Make the verification command side-effect free for source and tracker files, "
            "then rerun confirmation."
        )
    return result


def _run_path_check(check: dict[str, Any], tracker: Tracker) -> dict[str, Any]:
    try:
        path = _resolve_repo_path(tracker.repo_root, check["path"])
        if not path.exists() and not path.is_symlink():
            raise TicketCtlError(f"path does not exist: {check['path']}")
        return {
            "id": check["id"],
            "result": "PASS",
            "evidence": {
                "path": path.relative_to(tracker.repo_root).as_posix(),
                "sha256": _hash_path(path),
            },
        }
    except (OSError, TicketCtlError) as exc:
        return {
            "id": check["id"],
            "result": "FAIL",
            "evidence": {"error": str(exc)},
        }


def _independent_evidence(check: dict[str, Any]) -> dict[str, Any]:
    evidence = check.get("evidence")
    if not isinstance(evidence, dict):
        return {
            "id": check["id"],
            "result": "BLOCKED",
            "evidence": {"error": "independent evidence object is missing"},
            "unlocking_condition": "Attach independently issued PASS evidence to the plan check.",
        }
    required = ("result", "reference", "issuer")
    if evidence.get("result") != "PASS" or any(
        not isinstance(evidence.get(key), str) or not evidence[key].strip()
        for key in required[1:]
    ):
        return {
            "id": check["id"],
            "result": "BLOCKED",
            "evidence": {
                "error": "evidence requires result=PASS, reference, and issuer"
            },
            "unlocking_condition": "Attach independently issued PASS evidence with a stable reference and issuer.",
        }
    return {"id": check["id"], "result": "PASS", "evidence": evidence}


def _run_assertion_check(
    check: dict[str, Any], tracker: Tracker, sandbox: VerificationSandbox
) -> dict[str, Any]:
    if "command" in check:
        return _run_command_check(check, tracker, sandbox)
    evidence_path = check.get("evidence_path")
    if isinstance(evidence_path, str):
        path_check = dict(check)
        path_check["path"] = evidence_path
        return _run_path_check(path_check, tracker)
    evidence = check.get("evidence")
    if (
        isinstance(evidence, dict)
        and evidence.get("result") == "PASS"
        and isinstance(evidence.get("reference"), str)
        and evidence["reference"].strip()
    ):
        return {"id": check["id"], "result": "PASS", "evidence": evidence}
    return {
        "id": check["id"],
        "result": "BLOCKED",
        "evidence": {"error": "assertion has no executable command or stable evidence"},
        "unlocking_condition": "Add a local command, evidence_path, or stable PASS evidence to this assertion.",
    }


def _execute_check(
    check: dict[str, Any], tracker: Tracker, sandbox: VerificationSandbox
) -> dict[str, Any]:
    kind = check["kind"]
    if sandbox.contaminated and kind not in ("external", "manual-review"):
        result = {
            "id": check["id"],
            "result": "BLOCKED",
            "evidence": {
                "error": "an earlier verification command contaminated the isolated repository"
            },
            "unlocking_condition": (
                "Make every verification command side-effect free for source and tracker files, "
                "then rerun confirmation."
            ),
        }
    elif kind == "command":
        result = _run_command_check(check, tracker, sandbox)
    elif kind in ("file", "artifact"):
        result = _run_path_check(check, tracker)
    elif kind == "assertion":
        result = _run_assertion_check(check, tracker, sandbox)
    else:
        result = _independent_evidence(check)
    result["kind"] = kind
    result["blocking"] = check["blocking"]
    return result


def _load_results(ticket: Ticket) -> list[dict[str, Any]]:
    body = ticket.sections.get("Verification Result")
    if body is None:
        return []
    return _json_blocks(body)


def _pending_external_checkpoints(ticket: Ticket) -> list[str]:
    try:
        results = _load_results(ticket)
    except TicketCtlError:
        return []
    if not results:
        return []
    checks = results[-1].get("checks", [])
    if not isinstance(checks, list):
        return []
    integrity = _external_checkpoint_integrity(ticket)
    recorded = set(integrity["records"])
    return [
        check["id"]
        for check in checks
        if isinstance(check, dict)
        and isinstance(check.get("id"), str)
        and check.get("kind") in ("external", "manual-review")
        and check.get("blocking") is False
        and check.get("result") != "PASS"
        and check["id"] not in recorded
    ]


def _canonical_json_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _text_hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _completion_receipt_entry(result: dict[str, Any]) -> str:
    passing_ids = [
        check["id"]
        for check in result.get("checks", [])
        if isinstance(check, dict)
        and isinstance(check.get("id"), str)
        and check.get("result") == "PASS"
    ]
    passing_checks = ", ".join(f"`{check_id}`" for check_id in passing_ids) or "None"
    return (
        f"- Verification PASS at `{result.get('source_revision')}` "
        f"with plan `{result.get('plan_hash')}` "
        f"and contract `{result.get('immutable_contract_hash')}` "
        f"for checks: {passing_checks}."
    )


def _has_completion_receipt(ticket: Ticket, result: dict[str, Any]) -> bool:
    data = _completion_receipt_data(ticket.sections.get("Evidence", ""))
    if data["errors"]:
        raise ValueError("; ".join(data["errors"]))
    return _completion_receipt_entry(result) in data["completion_receipts"]


EXTERNAL_RECORD_FIELDS = frozenset(
    {
        "version",
        "controller_version",
        "ticket",
        "check",
        "result",
        "reference",
        "issuer",
        "recorded_at",
        "core_result_hash",
        "completion_receipt_hash",
        "record_hash",
    }
)


def _external_record_hash(record: dict[str, Any]) -> str:
    content = {key: value for key, value in record.items() if key != "record_hash"}
    return _canonical_json_hash(content)


def _valid_recorded_at(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


EXTERNAL_RECORD_HEADING_RE = re.compile(r"^###[ \t]+(.+?)[ \t]*$", re.MULTILINE)
EXTERNAL_RECORD_BLOCK_RE = re.compile(
    r"\s*```json[ \t]*\n(.*?)\n```[ \t]*\s*",
    re.DOTALL | re.IGNORECASE,
)


def _external_record_segment(
    segment: str, *, label: str, heading_check: str | None
) -> dict[str, Any]:
    match = EXTERNAL_RECORD_BLOCK_RE.fullmatch(segment)
    errors: list[str] = []
    record: dict[str, Any] | None = None
    if match is None:
        errors.append(f"{label} must contain exactly one complete JSON fenced object")
    else:
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            errors.append(f"{label} has invalid JSON: {exc}")
        else:
            if not isinstance(value, dict):
                errors.append(f"{label} JSON must contain an object")
            else:
                record = value
    return {
        "label": label,
        "heading_check": heading_check,
        "record": record,
        "errors": errors,
    }


def _external_record_data(ticket: Ticket) -> dict[str, Any]:
    bodies = _section_bodies(ticket.text, EXTERNAL_CHECKPOINT_RESULT_SECTION)
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    invalid_check_ids: set[str] = set()
    if len(bodies) > 1:
        errors.append(f"duplicate section {EXTERNAL_CHECKPOINT_RESULT_SECTION!r}")

    for section_index, body in enumerate(bodies, start=1):
        headings = list(EXTERNAL_RECORD_HEADING_RE.finditer(body))
        if not headings:
            if body.strip():
                entry = _external_record_segment(
                    body,
                    label=f"external record section {section_index}",
                    heading_check=None,
                )
                entries.append(entry)
                errors.extend(entry["errors"])
            continue

        prefix = body[: headings[0].start()]
        if prefix.strip():
            errors.append(
                f"external record section {section_index} has content before its first record heading"
            )
        for heading_index, heading in enumerate(headings):
            check_id = heading.group(1).strip()
            end = (
                headings[heading_index + 1].start()
                if heading_index + 1 < len(headings)
                else len(body)
            )
            entry = _external_record_segment(
                body[heading.end() : end],
                label=f"external record {check_id!r} in section {section_index}",
                heading_check=check_id,
            )
            entries.append(entry)
            errors.extend(entry["errors"])
            if entry["errors"]:
                invalid_check_ids.add(check_id)

    return {
        "entries": entries,
        "errors": errors,
        "invalid_check_ids": invalid_check_ids,
        "section_count": len(bodies),
    }


def _external_checkpoint_integrity(ticket: Ticket) -> dict[str, Any]:
    record_data = _external_record_data(ticket)
    anchor_data = _external_anchor_channels(ticket)
    errors: list[str] = [*record_data["errors"], *anchor_data["errors"]]
    records: dict[str, dict[str, Any]] = {}
    record_candidates: dict[str, dict[str, Any]] = {}
    invalid_record_ids: set[str] = set(record_data["invalid_check_ids"])
    anchors = anchor_data["anchors"]
    raw_records = [
        entry["record"]
        for entry in record_data["entries"]
        if entry["record"] is not None
    ]

    try:
        results = _load_results(ticket)
    except TicketCtlError as exc:
        results = []
        if raw_records or anchors:
            errors.append(f"cannot bind external records to Verification Result: {exc}")
    latest = results[-1] if results else None
    result_checks = (
        {
            check["id"]: check
            for check in latest.get("checks", [])
            if isinstance(check, dict) and isinstance(check.get("id"), str)
        }
        if isinstance(latest, dict)
        else {}
    )
    expected_core_hash = (
        _canonical_json_hash(latest) if isinstance(latest, dict) else None
    )
    expected_receipt_hash = (
        _text_hash(_completion_receipt_entry(latest))
        if isinstance(latest, dict)
        else None
    )

    seen_check_ids: set[str] = set()
    for index, entry in enumerate(record_data["entries"], start=1):
        record = entry["record"]
        if record is None:
            continue
        label = entry["label"] or f"external record {index}"
        heading_check = entry["heading_check"]
        record_errors: list[str] = []
        if set(record) != EXTERNAL_RECORD_FIELDS:
            record_errors.append(f"{label} has an invalid field set")
        check_id = record.get("check")
        if not isinstance(check_id, str) or not check_id:
            record_errors.append(f"{label} has an invalid check id")
            check_id = f"<record-{index}>"
        if heading_check is not None and heading_check != check_id:
            invalid_record_ids.add(heading_check)
            record_errors.append(
                f"{label} heading does not match record check {check_id!r}"
            )
        if check_id in seen_check_ids:
            invalid_record_ids.add(check_id)
            record_errors.append(f"external checkpoint {check_id} is duplicated")
        else:
            seen_check_ids.add(check_id)
        if record.get("version") != 1:
            record_errors.append(f"external checkpoint {check_id} has invalid version")
        if (
            record.get("controller_version")
            not in SUPPORTED_EXTERNAL_CONTROLLER_VERSIONS
        ):
            record_errors.append(
                f"external checkpoint {check_id} has invalid controller_version"
            )
        if record.get("ticket") != ticket.ticket_id:
            record_errors.append(
                f"external checkpoint {check_id} has invalid ticket identity"
            )
        if record.get("result") != "PASS":
            record_errors.append(f"external checkpoint {check_id} is not PASS")
        for field in ("reference", "issuer"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                record_errors.append(
                    f"external checkpoint {check_id} has invalid {field}"
                )
        if not _valid_recorded_at(record.get("recorded_at")):
            record_errors.append(
                f"external checkpoint {check_id} has invalid recorded_at"
            )
        if record.get("core_result_hash") != expected_core_hash:
            record_errors.append(
                f"external checkpoint {check_id} is not bound to the Core result"
            )
        if record.get("completion_receipt_hash") != expected_receipt_hash:
            record_errors.append(
                f"external checkpoint {check_id} is not bound to the completion receipt"
            )
        if record.get("record_hash") != _external_record_hash(record):
            record_errors.append(
                f"external checkpoint {check_id} record hash is invalid"
            )

        completion_check = result_checks.get(check_id)
        if completion_check is None:
            record_errors.append(
                f"external checkpoint {check_id} is absent from the completion result"
            )
        elif not (
            completion_check.get("kind") in ("external", "manual-review")
            and completion_check.get("blocking") is False
            and completion_check.get("result") != "PASS"
        ):
            record_errors.append(
                f"external checkpoint {check_id} was not pending after Core completion"
            )

        if record_errors:
            invalid_record_ids.add(check_id)
        errors.extend(record_errors)
        if not record_errors:
            record_candidates[check_id] = record

    for check_id in invalid_record_ids:
        record_candidates.pop(check_id, None)

    for check_id, record in sorted(record_candidates.items()):
        anchor = anchors.get(check_id)
        if anchor is None:
            errors.append(f"external checkpoint {check_id} Evidence anchor is missing")
        elif anchor["record_hash"] != record.get("record_hash") or anchor[
            "core_result_hash"
        ] != record.get("core_result_hash"):
            errors.append(
                f"external checkpoint {check_id} Evidence anchor is inconsistent"
            )
        else:
            records[check_id] = record

    for check_id in sorted(set(anchors) - set(record_candidates)):
        errors.append(
            f"external checkpoint {check_id} Evidence anchor has no valid record"
        )

    if errors:
        state = "INVALID"
    elif record_data["section_count"] or anchors:
        state = "VALID"
    else:
        state = "NONE"
    return {
        "state": state,
        "errors": errors,
        "records": records,
        "record_candidates": record_candidates,
        "raw_records": raw_records,
    }


def _external_result_body(records: Sequence[dict[str, Any]]) -> str:
    blocks = []
    for record in sorted(records, key=lambda item: item["check"]):
        blocks.append(
            f"### {record['check']}\n\n"
            f"```json\n{json.dumps(record, indent=2, sort_keys=True)}\n```"
        )
    return "\n\n".join(blocks)


def _external_anchor_body(records: Sequence[dict[str, Any]]) -> str:
    entries = [
        (
            f"- External checkpoint `{record['check']}` record "
            f"`{record['record_hash']}` bound to Core result "
            f"`{record['core_result_hash']}`."
        )
        for record in sorted(records, key=lambda item: item["check"])
    ]
    return "\n".join(
        [EXTERNAL_CHECKPOINT_MARKER_START, *entries, EXTERNAL_CHECKPOINT_MARKER_END]
    )


def _historical_completion_integrity(
    tracker: Tracker, ticket: Ticket
) -> dict[str, Any]:
    result_body = ticket.sections.get("Verification Result")
    has_structured_result_attempt = bool(
        result_body and re.search(r"```json\b", result_body, flags=re.IGNORECASE)
    )
    if not has_structured_result_attempt:
        return {
            "state": "LEGACY_MIGRATION_REQUIRED",
            "errors": ["ticket predates structured Verification Result evidence"],
        }
    if "Verification Plan" not in ticket.sections:
        return {
            "state": "INVALID",
            "errors": [
                "structured Verification Result exists without a matching Verification Plan"
            ],
        }
    try:
        plan = _load_plan(ticket)
        validated = _validate_plan(ticket, plan)
        normalized_plan = dict(plan)
        normalized_plan["checks"] = validated["checks"]
        results = _load_results(ticket)
        if not results:
            return {"state": "INVALID", "errors": ["Verification Result is empty"]}
        latest = results[-1]
        errors = _completion_errors(
            tracker,
            ticket,
            normalized_plan,
            latest,
            require_current_source=False,
            revalidate_artifacts=False,
            check_dependencies=False,
        )
        if not _has_completion_receipt(ticket, latest):
            errors.append("completion receipt is missing or inconsistent")
    except (KeyError, OSError, TicketCtlError, ValueError) as exc:
        return {"state": "INVALID", "errors": [str(exc)]}
    return {"state": "INVALID" if errors else "VALID", "errors": errors}


def _dependency_has_valid_pass(tracker: Tracker, ticket: Ticket) -> bool:
    try:
        if _historical_completion_integrity(tracker, ticket)["state"] != "VALID":
            return False
        results = _load_results(ticket)
        if not results:
            return False
        result = results[-1]
        plan = _load_plan(ticket)
        validated = _validate_plan(ticket, plan)
        normalized_plan = dict(plan)
        normalized_plan["checks"] = validated["checks"]
        errors = _completion_errors(
            tracker,
            ticket,
            normalized_plan,
            result,
            require_current_source=True,
            revalidate_artifacts=True,
            check_dependencies=True,
        )
        return not errors
    except (KeyError, OSError, TicketCtlError):
        return False


def _result_body(existing_body: str | None, result: dict[str, Any]) -> str:
    existing = existing_body.strip() if existing_body else ""
    latest_blocks = _json_blocks(existing) if existing else []
    comparable = dict(result)
    comparable.pop("attempt", None)
    if latest_blocks:
        previous = dict(latest_blocks[-1])
        previous.pop("attempt", None)
        if previous == comparable:
            return existing
    attempt = len(latest_blocks) + 1
    result_with_attempt = dict(result)
    result_with_attempt["attempt"] = attempt
    failed = result_with_attempt.get("failed_checks", [])
    blocked = result_with_attempt.get("blocked_by", [])
    if result_with_attempt["result"] == "PASS":
        summary = "Summary: all blocking checks passed."
    elif result_with_attempt["result"] == "REMEDIATE":
        summary = f"Summary: remediation required for {', '.join(failed)}."
    else:
        summary = f"Summary: blocked by {', '.join(blocked)}."
    block = (
        f"### Attempt {attempt}\n\n"
        f"```json\n{json.dumps(result_with_attempt, indent=2, sort_keys=True)}\n```\n\n"
        f"{summary}"
    )
    return f"{existing}\n\n{block}".strip() if existing else block


def _declared_existing_paths(ticket: Ticket, tracker: Tracker) -> list[str]:
    candidates: set[str] = set()
    for section_name in ("Delivery", "Core Acceptance", "Evidence"):
        body = ticket.sections.get(section_name, "")
        candidates.update(re.findall(r"`([^`\n]+)`", body))
        candidates.update(re.findall(r"\[[^\]]+\]\(([^)]+)\)", body))

    existing: list[str] = []
    for raw_value in sorted(candidates):
        value = raw_value.strip().rstrip("/")
        if (
            not value
            or any(character.isspace() for character in value)
            or "://" in value
            or value.startswith("-")
            or ("/" not in value and "." not in Path(value).name)
        ):
            continue
        try:
            path = _resolve_repo_path(tracker.repo_root, value)
        except TicketCtlError:
            continue
        if path.exists() or path.is_symlink():
            existing.append(path.relative_to(tracker.repo_root).as_posix())
    return existing


def _roadmap_item_match(line: str) -> re.Match[str] | None:
    return ROADMAP_LIST_ITEM_RE.fullmatch(line) or ROADMAP_HEADING_ITEM_RE.fullmatch(
        line
    )


def _frontier_region(text: str) -> tuple[re.Match[str], int, str]:
    all_sections = list(SECTION_RE.finditer(text))
    section_matches = [
        (index, match)
        for index, match in enumerate(all_sections)
        if match.group(1).strip() == "Current Frontier"
    ]
    if len(section_matches) != 1:
        raise TicketCtlError(
            "roadmap must contain exactly one '## Current Frontier' section"
        )
    section_index, frontier_match = section_matches[0]
    end = (
        all_sections[section_index + 1].start()
        if section_index + 1 < len(all_sections)
        else len(text)
    )
    return frontier_match, end, text[frontier_match.end() : end]


def _legacy_frontier_block(body: str) -> tuple[int, int, list[str]]:
    matches: list[tuple[int, int, int, str]] = []
    none_matches: list[tuple[int, int, int]] = []
    offset = 0
    for index, line in enumerate(body.splitlines(keepends=True)):
        content = line.rstrip("\r\n")
        match = re.match(r"^\s*[-*]\s+(?:\[)?`?(\d{2,})`?(?:\s|\]|$)", content)
        if match:
            matches.append((index, offset, offset + len(line), match.group(1)))
        elif re.fullmatch(r"\s*_None\._\s*", content):
            none_matches.append((index, offset, offset + len(line)))
        offset += len(line)

    if matches and none_matches:
        raise TicketCtlError(
            "markerless Current Frontier mixes ticket entries with _None._",
            kind="ROADMAP_MARKER_MISSING",
        )
    if matches:
        indices = [item[0] for item in matches]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise TicketCtlError(
                "markerless Current Frontier does not contain one continuous legacy list",
                kind="ROADMAP_MARKER_MISSING",
            )
        ids = [item[3] for item in matches]
        if len(ids) != len(set(ids)):
            raise TicketCtlError(
                "markerless Current Frontier contains duplicate ticket ids",
                kind="ROADMAP_MARKER_MISSING",
            )
        return matches[0][1], matches[-1][2], ids
    if len(none_matches) == 1:
        _, start, end = none_matches[0]
        return start, end, []
    raise TicketCtlError(
        "roadmap Current Frontier has no marker and no unique legacy frontier list",
        kind="ROADMAP_MARKER_MISSING",
    )


def _parse_roadmap(tracker: Tracker, text: str) -> tuple[dict[str, bool], list[str]]:
    checkbox_status: dict[str, bool] = {}
    for line in text.splitlines():
        match = _roadmap_item_match(line)
        if not match:
            continue
        ticket_id = match.group("id")
        if ticket_id in checkbox_status:
            raise TicketCtlError(
                f"roadmap has duplicate checkbox for ticket {ticket_id}"
            )
        checkbox_status[ticket_id] = match.group("mark").lower() == "x"
    expected_ids = set(tracker.tickets)
    if set(checkbox_status) != expected_ids:
        missing = sorted(expected_ids - set(checkbox_status))
        unknown = sorted(set(checkbox_status) - expected_ids)
        parts = []
        if missing:
            parts.append(f"missing ticket checkboxes: {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown ticket checkboxes: {', '.join(unknown)}")
        raise TicketCtlError(
            "roadmap format is not safely recognizable: " + "; ".join(parts)
        )

    _, _, body = _frontier_region(text)
    start_count = body.count(FRONTIER_MARKER_START)
    end_count = body.count(FRONTIER_MARKER_END)
    if start_count != end_count or start_count > 1:
        raise TicketCtlError(
            "roadmap Current Frontier must contain zero or one complete ticketctl marker block"
        )
    if start_count == 1:
        marker_start = body.index(FRONTIER_MARKER_START)
        marker_end = body.index(FRONTIER_MARKER_END) + len(FRONTIER_MARKER_END)
        frontier_source = body[marker_start:marker_end]
    else:
        legacy_start, legacy_end, _ = _legacy_frontier_block(body)
        frontier_source = body[legacy_start:legacy_end]
    frontier_ids = []
    for line in frontier_source.splitlines():
        match = re.match(r"^\s*[-*]\s+(?:\[)?`?(\d{2,})`?(?:\s|\]|$)", line)
        if match:
            frontier_ids.append(match.group(1))
    unknown_frontier = sorted(set(frontier_ids) - expected_ids)
    if unknown_frontier:
        raise TicketCtlError(
            f"roadmap Current Frontier references unknown tickets: {', '.join(unknown_frontier)}"
        )
    return checkbox_status, frontier_ids


def _render_frontier_marker(text: str, ready: list[str]) -> str:
    frontier_match, _, body = _frontier_region(text)
    body_lines = [FRONTIER_MARKER_START]
    body_lines.extend(f"- `{ticket_id}`" for ticket_id in ready)
    if not ready:
        body_lines.append("_None._")
    body_lines.append(FRONTIER_MARKER_END)
    block = "\n".join(body_lines)
    if FRONTIER_MARKER_START in body:
        start = text.index(FRONTIER_MARKER_START, frontier_match.end())
        end = text.index(FRONTIER_MARKER_END, start) + len(FRONTIER_MARKER_END)
        return text[:start] + block + text[end:]
    legacy_start, legacy_end, _ = _legacy_frontier_block(body)
    absolute_start = frontier_match.end() + legacy_start
    absolute_end = frontier_match.end() + legacy_end
    trailing_newline = "\n" if body[legacy_start:legacy_end].endswith("\n") else ""
    return text[:absolute_start] + block + trailing_newline + text[absolute_end:]


def _render_roadmap(tracker: Tracker, text: str) -> str:
    _parse_roadmap(tracker, text)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        content = line[:-1] if newline else line
        match = _roadmap_item_match(content)
        if match:
            done = tracker.tickets[match.group("id")].status == "Done"
            mark = "x" if done else " "
            content = (
                content[: match.start("mark")] + mark + content[match.end("mark") :]
            )
        lines.append(content + newline)
    updated = "".join(lines)
    ready = frontier(tracker)
    return _render_frontier_marker(updated, ready)


def _roadmap_summary(value: str) -> str:
    parts: list[str] = []
    in_fence = False
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if line and not _is_empty_requirement(line):
            parts.append(line)
    summary = " ".join(parts)
    return summary if len(summary) <= 240 else summary[:237].rstrip() + "..."


def _initial_roadmap_text(tracker: Tracker) -> str:
    cards: list[str] = []
    for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key):
        ticket = tracker.tickets[ticket_id]
        display_title = re.sub(
            rf"^{re.escape(ticket_id)}(?:\s+[-\u2014])?\s*", "", ticket.title
        ).strip()
        display_title = display_title or ticket.title
        function = (
            _roadmap_summary(ticket.sections["Delivery"]) or "See frozen Delivery."
        )
        verification_parts = [
            _roadmap_summary(ticket.sections["Core Acceptance"]),
            _roadmap_summary(_frozen_evidence(ticket.sections["Evidence"])),
        ]
        verification_parts = [part for part in verification_parts if part]
        if _verification_plan_state(ticket) == "VALID":
            plan = _normalized_plan(ticket, _load_plan(ticket))
            ids = ", ".join(f"`{check['id']}`" for check in plan["checks"])
            verification_parts.append(f"checks {ids}")
        verification = "; ".join(verification_parts) or "Verification Plan pending."
        mark = "x" if ticket.status == "Done" else " "
        cards.append(
            f"#### [{mark}] [`{ticket_id}` {display_title}](issues/{ticket.path.name})\n\n"
            f"- Function: {function}\n"
            f"- Verification: {verification}\n"
            f"- Depends On: {ticket.metadata['Depends On']}\n"
            f"- Unblocks: {ticket.metadata['Unblocks']}"
        )
    ready = frontier(tracker)
    frontier_lines = "\n".join(f"- `{ticket_id}`" for ticket_id in ready) or "_None._"
    return (
        "# Implementation Roadmap\n\n"
        "## Tickets\n\n"
        + "\n\n".join(cards)
        + "\n\n## Current Frontier\n\n"
        + f"{FRONTIER_MARKER_START}\n{frontier_lines}\n{FRONTIER_MARKER_END}\n"
    )


def _roadmap_status(tracker: Tracker) -> dict[str, Any]:
    path = tracker.roadmap_path
    if path is None or not path.is_file():
        return {
            "present": False,
            "path": str(path) if path is not None else None,
            "state": "MISSING",
        }
    drift = roadmap_drift(tracker)
    malformed = any("malformed roadmap:" in item.get("reason", "") for item in drift)
    return {
        "present": True,
        "path": str(path),
        "state": "MALFORMED" if malformed else "STALE" if drift else "CURRENT",
    }


def roadmap_drift(tracker: Tracker) -> list[dict[str, Any]]:
    path = tracker.roadmap_path
    if path is None or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
        checkbox_status, roadmap_frontier = _parse_roadmap(tracker, text)
    except (OSError, UnicodeError, TicketCtlError) as exc:
        return [
            {
                "source": str(path),
                "severity": "informational",
                "reason": f"malformed roadmap: {exc}",
            }
        ]
    drift: list[dict[str, Any]] = []
    for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key):
        expected = tracker.tickets[ticket_id].status == "Done"
        if checkbox_status[ticket_id] != expected:
            drift.append(
                {
                    "source": str(path),
                    "severity": "informational",
                    "ticket": ticket_id,
                    "reason": "roadmap checkbox disagrees with authoritative ticket status",
                    "expected_checked": expected,
                }
            )
    expected_frontier = frontier(tracker)
    if roadmap_frontier != expected_frontier:
        drift.append(
            {
                "source": str(path),
                "severity": "informational",
                "reason": "Current Frontier is stale",
                "expected": expected_frontier,
                "actual": roadmap_frontier,
            }
        )
    return drift


def ticket_state_drift(tracker: Tracker) -> list[dict[str, Any]]:
    drift: list[dict[str, Any]] = []
    for ticket in tracker.tickets.values():
        if ticket.status == "Open":
            evidence_sources: list[str] = []
            try:
                if _load_results(ticket):
                    evidence_sources.append("Verification Result")
            except TicketCtlError as exc:
                evidence_sources.append(f"malformed Verification Result: {exc}")
            evidence_sources.extend(
                f"existing declared path {path}"
                for path in _declared_existing_paths(ticket, tracker)
            )
            plan_body = ticket.sections.get("Verification Plan")
            if plan_body:
                try:
                    plan = _load_plan(ticket)
                    for check in plan.get("checks", []):
                        if check.get("kind") in ("file", "artifact") and isinstance(
                            check.get("path"), str
                        ):
                            path = _resolve_repo_path(tracker.repo_root, check["path"])
                            if path.exists() or path.is_symlink():
                                evidence_sources.append(
                                    f"existing {check['kind']} {check['path']}"
                                )
                except TicketCtlError:
                    pass
            if evidence_sources:
                drift.append(
                    {
                        "source": str(ticket.path),
                        "ticket": ticket.ticket_id,
                        "severity": "migratable",
                        "reason": "ticket is Open although implementation or verification evidence exists",
                        "evidence": sorted(set(evidence_sources)),
                        "repair": "Run the formal claim, verify, and complete flow; do not infer Done.",
                    }
                )
        if ticket.status == "Done":
            integrity = _historical_completion_integrity(tracker, ticket)
            if integrity["state"] == "LEGACY_MIGRATION_REQUIRED":
                drift.append(
                    {
                        "source": str(ticket.path),
                        "ticket": ticket.ticket_id,
                        "severity": "migratable",
                        "reason": "legacy Done ticket lacks structured completion evidence",
                        "details": integrity["errors"],
                        "repair": (
                            f"Run ticketctl.py reconcile --feature-root {tracker.feature_root} "
                            f"--ticket {ticket.ticket_id} --reason <operator-reason>, then follow "
                            "the returned recovery handoff."
                        ),
                    }
                )
            elif integrity["state"] == "INVALID":
                drift.append(
                    {
                        "source": str(ticket.path),
                        "ticket": ticket.ticket_id,
                        "severity": "fatal",
                        "reason": "Done ticket has invalid historical completion integrity",
                        "details": integrity["errors"],
                        "repair": (
                            f"Run ticketctl.py reconcile --feature-root {tracker.feature_root} "
                            f"--ticket {ticket.ticket_id} --reason <operator-reason>, then follow "
                            "the returned recovery handoff."
                        ),
                    }
                )
            external_integrity = _external_checkpoint_integrity(ticket)
            if external_integrity["state"] == "INVALID":
                pending = _pending_external_checkpoints(ticket)
                check_value = pending[0] if len(pending) == 1 else "<check-id>"
                drift.append(
                    {
                        "source": str(ticket.path),
                        "ticket": ticket.ticket_id,
                        "severity": "informational",
                        "reason": "external checkpoint evidence is invalid",
                        "details": external_integrity["errors"],
                        "repair": (
                            f"Run ticketctl.py record-external --feature-root {tracker.feature_root} "
                            f"--ticket {ticket.ticket_id} --check {check_value} --result PASS "
                            "--reference <stable-reference> --issuer <independent-issuer> "
                            "--repair to rebuild only external evidence."
                        ),
                    }
                )
    drift.extend(roadmap_drift(tracker))
    return drift


def sync_roadmap_internal(
    tracker: Tracker,
    *,
    create_if_missing: bool = False,
    regenerate: bool = False,
) -> dict[str, Any]:
    path = tracker.roadmap_path
    if path is None and (create_if_missing or regenerate):
        path = (tracker.feature_root / "implementation-roadmap.md").resolve()
        tracker = dataclasses.replace(tracker, roadmap_path=path)
    if path is None:
        return {"present": False, "changed": False, "state_drift": []}
    try:
        if not path.exists():
            if not (create_if_missing or regenerate):
                return {
                    "present": False,
                    "path": str(path),
                    "changed": False,
                    "state_drift": [],
                }
            rendered = _initial_roadmap_text(tracker)
            _parse_roadmap(tracker, rendered)
            _atomic_write(path, rendered, expected_text=None)
            return {
                "present": True,
                "path": str(path),
                "changed": True,
                "generated": True,
                "state_drift": [],
            }
        original = path.read_text(encoding="utf-8")
        drift = roadmap_drift(tracker)
        rendered = (
            _initial_roadmap_text(tracker)
            if regenerate
            else _render_roadmap(tracker, original)
        )
        changed = rendered != original
        if changed:
            _parse_roadmap(tracker, rendered)
            _atomic_write(path, rendered, expected_text=original)
        return {
            "present": True,
            "path": str(path),
            "changed": changed,
            "generated": False,
            "regenerated": regenerate,
            "state_drift": drift,
        }
    except (OSError, UnicodeError, TicketCtlError) as exc:
        raise StateDriftFailure(
            "roadmap synchronization failed; authoritative ticket state was not rolled back",
            {
                "unsynchronized_files": [str(path)],
                "repair": (
                    "Fix the roadmap format or write condition, then rerun sync-roadmap; "
                    f"use --regenerate only to explicitly rebuild the derived view: {exc}"
                ),
            },
        ) from exc


def _load_tracker_from_args(args: argparse.Namespace) -> Tracker:
    return load_tracker(
        Path(args.feature_root), roadmap_override=getattr(args, "roadmap", None)
    )


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    return {
        "command": "validate",
        "valid": True,
        "feature_root": str(tracker.feature_root),
        "tickets": sorted(tracker.tickets, key=_ticket_sort_key),
        "canonical_spec": str(tracker.spec_path),
        "roadmap": _roadmap_status(tracker),
        "tracker_baseline": tracker_baseline(tracker),
    }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    by_status = {
        status: [
            ticket_id
            for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key)
            if tracker.tickets[ticket_id].status == status
        ]
        for status in STATUSES
    }
    baseline = tracker_baseline(tracker)
    drift = ticket_state_drift(tracker)
    active_tickets = [
        ticket_id
        for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key)
        if tracker.tickets[ticket_id].status in ("In Progress", "Blocked")
    ]
    if len(active_tickets) > 1:
        drift.append(
            {
                "source": "ticket_state",
                "severity": "fatal",
                "reason": "multiple tickets own the single execution lane",
                "active_tickets": active_tickets,
                "repair": "Resolve all but one active ticket through the formal state workflow.",
            }
        )
    if baseline["applicable"] and baseline["status"] != "PASS":
        drift.append(
            {
                "source": "tracker_baseline",
                "severity": "fatal",
                "reason": "published tracker baseline is incomplete or has frozen contract drift",
                "details": baseline,
                "repair": "Publish the tracker baseline without editing execution state by hand.",
            }
        )
    drift_summary = {
        severity: sum(item.get("severity") == severity for item in drift)
        for severity in ("fatal", "migratable", "informational")
    }
    ready = frontier(tracker)
    return {
        "command": "status",
        "feature_root": str(tracker.feature_root),
        "repository": tracker.repo_name,
        "roadmap": _roadmap_status(tracker),
        "tracker_baseline": baseline,
        "tickets": {
            ticket_id: {
                "status": ticket.status,
                "owner_repo": ticket.owner_repo,
                "core_dependencies": [
                    dep.ticket_id for dep in ticket.dependencies if not dep.external
                ],
                "external_dependencies": [
                    dep.ticket_id for dep in ticket.dependencies if dep.external
                ],
                "completion_integrity": (
                    _historical_completion_integrity(tracker, ticket)["state"]
                    if ticket.status == "Done"
                    else None
                ),
                "external_checkpoint_integrity": (
                    _external_checkpoint_integrity(ticket)["state"]
                    if ticket.status == "Done"
                    else None
                ),
                "verification_plan": _verification_plan_state(ticket),
                "pending_external_checkpoints": _pending_external_checkpoints(ticket),
            }
            for ticket_id, ticket in sorted(
                tracker.tickets.items(), key=lambda item: _ticket_sort_key(item[0])
            )
        },
        "by_status": by_status,
        "active_ticket": active_tickets[0] if len(active_tickets) == 1 else None,
        "active_tickets": active_tickets,
        "frontier": ready,
        "recommended_next": ready[0] if ready and not active_tickets else None,
        "state": "STATE_DRIFT" if drift else "CONSISTENT",
        "drift_summary": drift_summary,
        "state_drift": drift,
    }


def command_frontier(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    return {
        "command": "frontier",
        "feature_root": str(tracker.feature_root),
        "frontier": frontier(tracker),
        "roadmap": _roadmap_status(tracker),
    }


def _next_preflight(tracker: Tracker, requested_ticket: str | None) -> NextPreflight:
    baseline = _require_tracker_baseline(tracker)
    drift = ticket_state_drift(tracker)
    fatal_drift = [item for item in drift if item.get("severity") == "fatal"]
    if fatal_drift:
        raise StateDriftFailure(
            "fatal tracker drift must be resolved before activating a ticket",
            {"state_drift": fatal_drift},
        )

    active = [
        ticket_id
        for ticket_id in sorted(tracker.tickets, key=_ticket_sort_key)
        if tracker.tickets[ticket_id].status in ("In Progress", "Blocked")
    ]
    if len(active) > 1:
        raise TicketCtlError(
            "multiple tickets already own the single execution lane",
            kind="ACTIVE_TICKET_CONFLICT",
            details={"active_tickets": active},
        )

    ready = frontier(tracker)
    recovered = bool(active)
    if active:
        ticket = tracker.tickets[active[0]]
        if requested_ticket is not None and requested_ticket != ticket.ticket_id:
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} already owns the single execution lane",
                kind="ACTIVE_TICKET_EXISTS",
                details={
                    "active_ticket": ticket.ticket_id,
                    "status": ticket.status,
                },
            )
        if ticket.status == "Blocked":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is Blocked and still owns the single execution lane",
                kind="ACTIVE_TICKET_EXISTS",
                details={
                    "active_ticket": ticket.ticket_id,
                    "status": ticket.status,
                    "repair": (
                        "Satisfy the recorded unlocking condition and run resume before confirm."
                    ),
                },
            )
    else:
        selected = requested_ticket
        if selected is None:
            if not ready:
                raise TicketCtlError(
                    "no ticket is available in the current frontier",
                    kind="NO_FRONTIER",
                )
            selected = ready[0]
        ticket = _require_ticket(tracker, selected)
        if ticket.ticket_id not in ready:
            unsatisfied = {
                dependency.ticket_id: reason
                for dependency in ticket.dependencies
                if not dependency.external
                for satisfied, reason in [_dependency_satisfied(tracker, dependency)]
                if not satisfied
            }
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is not in the current frontier",
                kind="NOT_FRONTIER",
                details={
                    "frontier": ready,
                    "unsatisfied_dependencies": unsatisfied,
                },
            )

    if _verification_plan_state(ticket) == "INVALID":
        raise TicketCtlError(
            f"ticket {ticket.ticket_id} has an invalid existing Verification Plan",
            kind="PLAN_INVALID",
            details={
                "ticket": ticket.ticket_id,
                "status": ticket.status,
                "repair": (
                    "Preserve and repair the existing plan explicitly, then rerun next."
                ),
            },
        )

    return NextPreflight(
        ticket=ticket,
        baseline=baseline,
        frontier=ready,
        recovered_activation=recovered,
        drift_summary={
            severity: sum(item.get("severity") == severity for item in drift)
            for severity in ("migratable", "informational")
        },
    )


def _planning_requirements(ticket: Ticket) -> list[dict[str, Any]]:
    requirements = _requirement_units(ticket)
    blocking_required = {
        requirement_id
        for requirement_id in requirements
        if requirement_id.startswith(("AC-", "INV-"))
    }
    blocking_required.update(ticket.completion_gates)
    return [
        {
            "id": requirement_id,
            "source": REQUIREMENT_SECTION_BY_PREFIX[requirement_id.split("-", 1)[0]],
            "requirement": requirement,
            "blocking_required": requirement_id in blocking_required,
        }
        for requirement_id, requirement in requirements.items()
    ]


def command_prepare_next(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    preflight = _next_preflight(tracker, getattr(args, "ticket", None))
    ticket = preflight.ticket
    plan_state = _verification_plan_state(ticket)
    requirements = _planning_requirements(ticket)
    blocking_ids = [item["id"] for item in requirements if item["blocking_required"]]
    existing_plan = None
    if plan_state == "VALID":
        plan = _normalized_plan(ticket, _load_plan(ticket))
        existing_plan = {
            "hash": _plan_hash(plan),
            "check_ids": [check["id"] for check in plan["checks"]],
        }
    return {
        "command": "prepare-next",
        "packet_version": 1,
        "feature_root": str(tracker.feature_root),
        "repository_root": str(tracker.repo_root),
        "ticket": {
            "id": ticket.ticket_id,
            "path": str(ticket.path),
            "title": ticket.title,
            "status": ticket.status,
            "plan_state": plan_state,
            "wave_layer": ticket.metadata["Wave / Layer"],
            "owner_repo": ticket.owner_repo,
            "depends_on": _dependency_packet(ticket.dependencies),
            "unblocks": _dependency_packet(ticket.unblocks),
            "completion_gates": sorted(ticket.completion_gates),
        },
        "planning_required": plan_state != "VALID",
        "recovered_activation": preflight.recovered_activation,
        "frontier": preflight.frontier,
        "requirements": requirements,
        "plan_contract": {
            "version": 1,
            "ticket": ticket.ticket_id,
            "allowed_check_kinds": list(CHECK_KINDS),
            "required_check_fields": [
                "id",
                "source",
                "requirement",
                "kind",
                "expected",
                "blocking",
                "covers",
            ],
            "checks_must_be_nonempty": True,
            "checks_must_have_unique_ids": True,
            "all_requirement_ids_must_be_covered": True,
            "source_and_requirement_must_exactly_match_one_covered_requirement": True,
            "repository_instructions_must_not_add_or_reinterpret_requirements": True,
            "verification_mechanics_must_be_grounded_in": [
                "Planning Packet",
                "applicable repository instructions",
            ],
            "unplanable_blocking_requirement_outcome": "CONTRACT_NOT_PLANABLE",
            "blocking_requirement_ids": blocking_ids,
            "core_gate_coverage_must_be_blocking": True,
            "external_only_checks_must_be_nonblocking": True,
            "kind_requirements": {
                "command": {
                    "requires": ["command"],
                    "expected": "exit_code == 0",
                },
                "file": {"requires": ["path"]},
                "assertion": {
                    "requires_one_of": ["command", "evidence_path", "evidence"]
                },
                "artifact": {"requires": ["path"]},
                "external": {"evidence": "independent issuer only"},
                "manual-review": {"evidence": "independent issuer only"},
            },
            "command": {
                "direct_argv_only": True,
                "expected": "exit_code == 0",
                "shell_operators_allowed": False,
            },
            "prohibited_commands": [
                "shell executables and operators",
                "interpreter inline evaluation",
                "destructive or mutating Git operations",
                "publish, deploy, or release",
                "network or production writes",
            ],
            "paths_must_be_repository_contained": True,
            "external_requirements_use": ["external", "manual-review"],
            "external_evidence_must_be_independent": True,
        },
        "existing_plan": existing_plan,
        "canonical_spec": {
            "path": str(tracker.spec_path),
            "sha256": f"sha256:{_hash_path(tracker.spec_path)}",
            "content_required_for_planning": False,
        },
        "immutable_contract_hash": _immutable_contract_hash(tracker, ticket),
        "tracker_baseline": {
            "status": preflight.baseline["status"],
            "revision": preflight.baseline.get("revision"),
        },
        "roadmap": _roadmap_status(tracker),
        "nonfatal_drift_summary": preflight.drift_summary,
    }


def command_next(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        preflight = _next_preflight(tracker, getattr(args, "ticket", None))
        baseline = preflight.baseline
        ticket = preflight.ticket
        provided_plan = (
            _load_plan_file(args.plan_file)
            if getattr(args, "plan_file", None)
            else None
        )
        recovered = preflight.recovered_activation
        ticket_changed = False
        if recovered:
            plan_state = _verification_plan_state(ticket)
            if plan_state == "MISSING":
                if provided_plan is None:
                    raise TicketCtlError(
                        f"ticket {ticket.ticket_id} activation is incomplete and requires --plan-file",
                        kind="PLAN_REQUIRED",
                    )
                normalized_plan = _normalized_plan(ticket, provided_plan)
                updated = _replace_or_insert_section(
                    ticket.text, "Verification Plan", _plan_markdown(normalized_plan)
                )
                _validated_ticket_candidate(tracker, ticket, updated)
                _atomic_write(ticket.path, updated, expected_text=ticket.text)
                ticket_changed = True
            else:
                existing_plan = _normalized_plan(ticket, _load_plan(ticket))
                if provided_plan is not None:
                    candidate_plan = _normalized_plan(ticket, provided_plan)
                    if candidate_plan != existing_plan:
                        raise TicketCtlError(
                            f"ticket {ticket.ticket_id} already has a different Verification Plan",
                            kind="PLAN_CONFLICT",
                        )
        else:
            plan_state = _verification_plan_state(ticket)
            if plan_state == "VALID":
                existing_plan = _normalized_plan(ticket, _load_plan(ticket))
                if provided_plan is not None:
                    candidate_plan = _normalized_plan(ticket, provided_plan)
                    if candidate_plan != existing_plan:
                        raise TicketCtlError(
                            f"ticket {ticket.ticket_id} already has a different Verification Plan",
                            kind="PLAN_CONFLICT",
                        )
                updated = ticket.text
            else:
                if provided_plan is None:
                    raise TicketCtlError(
                        "next requires a complete generated Verification Plan via --plan-file",
                        kind="PLAN_REQUIRED",
                    )
                normalized_plan = _normalized_plan(ticket, provided_plan)
                updated = _replace_or_insert_section(
                    ticket.text, "Verification Plan", _plan_markdown(normalized_plan)
                )
            updated = _replace_metadata(updated, "Status", "In Progress")
            _validated_ticket_candidate(tracker, ticket, updated)
            _atomic_write(ticket.path, updated, expected_text=ticket.text)
            ticket_changed = True

        activated_tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        activated_ticket = _require_ticket(activated_tracker, ticket.ticket_id)
        if activated_ticket.status != "In Progress":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} activation did not persist In Progress",
                kind="CONCURRENT_MODIFICATION",
            )
        roadmap = sync_roadmap_internal(activated_tracker, create_if_missing=True)
        final_tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        final_ticket = _require_ticket(final_tracker, ticket.ticket_id)
        return {
            "command": "next",
            "ticket": ticket.ticket_id,
            "status": "In Progress",
            "changed": ticket_changed or roadmap["changed"],
            "recovered": recovered,
            "tracker_baseline": baseline,
            "roadmap": roadmap,
            "frontier": frontier(final_tracker),
            "work_packet": _work_packet(final_tracker, final_ticket),
        }


def command_claim(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        baseline = _require_tracker_baseline(tracker)
        if ticket.status != "Open":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot be claimed from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
        ready = frontier(tracker)
        if ticket.ticket_id not in ready:
            unsatisfied = {
                dependency.ticket_id: reason
                for dependency in ticket.dependencies
                if not dependency.external
                for satisfied, reason in [_dependency_satisfied(tracker, dependency)]
                if not satisfied
            }
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} is not in the current frontier",
                kind="NOT_FRONTIER",
                details={
                    "frontier": ready,
                    "unsatisfied_dependencies": unsatisfied,
                },
            )
        updated = _replace_metadata(ticket.text, "Status", "In Progress")
        _validated_ticket_candidate(tracker, ticket, updated)
        _atomic_write(ticket.path, updated, expected_text=ticket.text)
        return {
            "command": "claim",
            "ticket": ticket.ticket_id,
            "from": "Open",
            "to": "In Progress",
            "tracker_baseline": baseline,
        }


def command_resume(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        if ticket.status != "Blocked":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot resume from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
        updated = _replace_metadata(ticket.text, "Status", "In Progress")
        _validated_ticket_candidate(tracker, ticket, updated)
        _atomic_write(ticket.path, updated, expected_text=ticket.text)
        return {
            "command": "resume",
            "ticket": ticket.ticket_id,
            "from": "Blocked",
            "to": "In Progress",
        }


def _reconciliation_body(
    existing_body: str | None,
    *,
    ticket: Ticket,
    integrity: dict[str, Any],
    reason: str,
) -> str:
    existing = existing_body.strip() if existing_body else ""
    prior_records = _json_blocks(existing) if existing else []
    record = {
        "version": 1,
        "controller_version": CONTROLLER_VERSION,
        "ticket": ticket.ticket_id,
        "action": "Done -> In Progress",
        "original_status": "Done",
        "reason": reason,
        "prior_integrity": integrity,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "attempt": len(prior_records) + 1,
    }
    block = (
        f"### Reconciliation {record['attempt']}\n\n"
        f"```json\n{json.dumps(record, indent=2, sort_keys=True)}\n```"
    )
    return f"{existing}\n\n{block}".strip() if existing else block


def command_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        if ticket.status != "Done":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot reconcile from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
        integrity = _historical_completion_integrity(tracker, ticket)
        if integrity["state"] == "VALID":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} already has valid historical completion evidence",
                kind="RECONCILIATION_NOT_REQUIRED",
            )
        baseline = _require_tracker_baseline(tracker)
        reason = getattr(args, "reason", None) or (
            "Explicit migration of legacy or invalid completion evidence requested by the operator."
        )
        body = _reconciliation_body(
            ticket.sections.get("Reconciliation Record"),
            ticket=ticket,
            integrity=integrity,
            reason=reason,
        )
        updated = _replace_or_insert_section(ticket.text, "Reconciliation Record", body)
        updated = _replace_metadata(updated, "Status", "In Progress")
        _validated_ticket_candidate(tracker, ticket, updated)
        _atomic_write(ticket.path, updated, expected_text=ticket.text)
        return {
            "command": "reconcile",
            "ticket": ticket.ticket_id,
            "from": "Done",
            "to": "In Progress",
            "prior_integrity": integrity,
            "tracker_baseline": baseline,
            "next": "Create or repair the Verification Plan, then run verify and complete.",
        }


def command_plan_check(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    ticket = _require_ticket(tracker, args.ticket)
    plan = _load_plan(ticket)
    validated = _validate_plan(ticket, plan)
    return {
        "command": "plan-check",
        "ticket": ticket.ticket_id,
        "valid": True,
        "plan_hash": _plan_hash(plan),
        "requirements": validated["requirements"],
        "check_ids": [check["id"] for check in validated["checks"]],
    }


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        if ticket.status != "In Progress":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} must be In Progress before verification",
                kind="INVALID_TRANSITION",
            )
        baseline = _require_tracker_baseline(tracker)
        plan = _load_plan(ticket)
        validated = _validate_plan(ticket, plan)
        initial_plan_body = ticket.sections.get("Verification Plan")
        initial_result_body = ticket.sections.get("Verification Result")
        initial_contract_hash = _immutable_contract_hash(tracker, ticket)
        source_revision_before = source_revision(tracker)
        with _verification_sandbox(tracker) as sandbox:
            check_results = [
                _execute_check(check, sandbox.tracker, sandbox)
                for check in validated["checks"]
            ]
        blocking_failures = [
            result["id"]
            for result in check_results
            if result["blocking"] and result["result"] == "FAIL"
        ]
        blockers = [
            result["id"]
            for result in check_results
            if result["blocking"] and result["result"] == "BLOCKED"
        ]
        warnings = [
            result["id"]
            for result in check_results
            if not result["blocking"] and result["result"] != "PASS"
        ]
        if blockers:
            overall = "BLOCKED"
        elif blocking_failures:
            overall = "REMEDIATE"
        else:
            overall = "PASS"

        current_tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        current_ticket = _require_ticket(current_tracker, args.ticket)
        source_revision_after = source_revision(current_tracker)
        if source_revision_after != source_revision_before:
            raise TicketCtlError(
                f"repository source changed while verifying ticket {current_ticket.ticket_id}",
                kind="CONCURRENT_MODIFICATION",
                details={
                    "source_revision_before": source_revision_before,
                    "source_revision_after": source_revision_after,
                    "repair": (
                        "Review the source changes and rerun verify against a stable working tree."
                    ),
                },
            )
        if current_ticket.status != "In Progress":
            raise TicketCtlError(
                f"ticket {current_ticket.ticket_id} changed status during verification",
                kind="CONCURRENT_MODIFICATION",
                details={
                    "expected_status": "In Progress",
                    "current_status": current_ticket.status,
                },
            )
        baseline = _require_tracker_baseline(current_tracker)
        if current_ticket.sections.get("Verification Plan") != initial_plan_body:
            raise TicketCtlError(
                f"ticket {current_ticket.ticket_id} Verification Plan changed during verification",
                kind="CONCURRENT_MODIFICATION",
            )
        if current_ticket.sections.get("Verification Result") != initial_result_body:
            raise TicketCtlError(
                f"ticket {current_ticket.ticket_id} Verification Result history changed during verification",
                kind="CONCURRENT_MODIFICATION",
            )
        if (
            _immutable_contract_hash(current_tracker, current_ticket)
            != initial_contract_hash
        ):
            raise TicketCtlError(
                f"ticket {current_ticket.ticket_id} frozen contract changed during verification",
                kind="CONCURRENT_MODIFICATION",
            )
        unsatisfied_dependencies = {
            dependency.ticket_id: reason
            for dependency in current_ticket.dependencies
            if not dependency.external
            for satisfied, reason in [
                _dependency_satisfied(current_tracker, dependency)
            ]
            if not satisfied
        }
        if unsatisfied_dependencies:
            raise TicketCtlError(
                f"ticket {current_ticket.ticket_id} dependencies changed during verification",
                kind="CONCURRENT_MODIFICATION",
                details={"unsatisfied_dependencies": unsatisfied_dependencies},
            )
        current_plan = _load_plan(current_ticket)
        current_validated = _validate_plan(current_ticket, current_plan)
        normalized_plan = dict(current_plan)
        normalized_plan["checks"] = current_validated["checks"]
        result = {
            "version": 1,
            "ticket": current_ticket.ticket_id,
            "result": overall,
            "source_revision": source_revision_after,
            "plan_hash": _plan_hash(normalized_plan),
            "immutable_contract_hash": _immutable_contract_hash(
                current_tracker, current_ticket
            ),
            "checks": check_results,
            "failed_checks": blocking_failures,
            "blocked_by": blockers,
            "warnings": warnings,
        }
        result_body = _result_body(
            current_ticket.sections.get("Verification Result"), result
        )
        updated = _replace_or_insert_section(
            current_ticket.text, "Verification Result", result_body
        )
        if overall == "BLOCKED":
            updated = _replace_metadata(updated, "Status", "Blocked")
        _, candidate_ticket = _validated_ticket_candidate(
            current_tracker, current_ticket, updated
        )
        candidate_results = _load_results(candidate_ticket)
        if not candidate_results:
            raise TicketCtlError(
                "controller generated an empty Verification Result",
                kind="GENERATED_TICKET_INVALID",
            )
        _atomic_write(current_ticket.path, updated, expected_text=current_ticket.text)
        return {"command": "verify", "tracker_baseline": baseline, **result}


def _is_sha256_digest(value: Any, *, prefixed: bool = False) -> bool:
    pattern = r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}"
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _command_result_errors(
    plan_check: dict[str, Any], result_check: dict[str, Any]
) -> list[str]:
    check_id = plan_check["id"]
    errors: list[str] = []
    try:
        expected_argv = _command_argv(plan_check["command"])
    except (KeyError, TicketCtlError) as exc:
        return [f"check {check_id} plan command is invalid or unsafe: {exc}"]
    evidence = result_check.get("evidence")
    if not isinstance(evidence, dict):
        return [f"check {check_id} has malformed command evidence"]
    expected_evidence_argv = [_redact_output(item) for item in expected_argv]
    if evidence.get("command") != expected_evidence_argv:
        errors.append(
            f"check {check_id} evidence command does not match the current plan"
        )

    exit_code = result_check.get("exit_code")
    check_result = result_check.get("result")
    if isinstance(exit_code, bool) or (
        exit_code is not None and not isinstance(exit_code, int)
    ):
        errors.append(f"check {check_id} exit_code must be an integer or null")
    elif isinstance(exit_code, int):
        expected_result = "PASS" if exit_code == 0 else "FAIL"
        if check_result != expected_result:
            errors.append(
                f"check {check_id} result is inconsistent with exit_code {exit_code}"
            )
        for field in ("stdout_sha256", "stderr_sha256"):
            if not _is_sha256_digest(evidence.get(field)):
                errors.append(f"check {check_id} command evidence has invalid {field}")
    elif check_result == "PASS":
        errors.append(f"check {check_id} PASS requires exit_code == 0")
    return errors


def _path_result_errors(
    tracker: Tracker,
    plan_check: dict[str, Any],
    result_check: dict[str, Any],
    *,
    path_field: str,
    revalidate_artifacts: bool,
) -> list[str]:
    check_id = plan_check["id"]
    evidence = result_check.get("evidence")
    if result_check.get("result") != "PASS":
        return []
    if not isinstance(evidence, dict):
        return [f"check {check_id} has malformed path evidence"]
    evidence_path = evidence.get("path")
    evidence_hash = evidence.get("sha256")
    if not isinstance(evidence_path, str) or not _is_sha256_digest(evidence_hash):
        return [f"check {check_id} lacks a valid persisted path/hash pair"]
    errors: list[str] = []
    try:
        planned_path = (
            _resolve_repo_path(tracker.repo_root, plan_check[path_field])
            .relative_to(tracker.repo_root)
            .as_posix()
        )
        if evidence_path != planned_path:
            errors.append(
                f"check {check_id} artifact path does not match the current plan"
            )
        if revalidate_artifacts:
            current_path = _resolve_repo_path(tracker.repo_root, evidence_path)
            if not current_path.exists() and not current_path.is_symlink():
                raise TicketCtlError(f"artifact path no longer exists: {evidence_path}")
            if _hash_path(current_path) != evidence_hash:
                errors.append(f"check {check_id} artifact hash is stale")
    except (KeyError, OSError, TicketCtlError) as exc:
        errors.append(f"check {check_id} artifact cannot be revalidated: {exc}")
    return errors


def _completion_errors(
    tracker: Tracker,
    ticket: Ticket,
    plan: dict[str, Any],
    result: dict[str, Any],
    *,
    require_current_source: bool,
    revalidate_artifacts: bool,
    check_dependencies: bool,
) -> list[str]:
    errors: list[str] = []
    if result.get("version") != 1 or result.get("ticket") != ticket.ticket_id:
        errors.append(
            "latest Verification Result has invalid version or ticket identity"
        )
    attempt = result.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        errors.append("latest Verification Result has an invalid attempt number")
    recorded_revision = result.get("source_revision")
    if not _is_sha256_digest(recorded_revision, prefixed=True):
        errors.append("Verification Result source_revision is missing or malformed")
    elif require_current_source and recorded_revision != source_revision(tracker):
        errors.append("Verification Result source_revision is stale")
    if result.get("plan_hash") != _plan_hash(plan):
        errors.append(
            "Verification Result does not match the current Verification Plan"
        )
    if result.get("immutable_contract_hash") != _immutable_contract_hash(
        tracker, ticket
    ):
        errors.append(
            "Verification Result immutable_contract_hash is stale for the canonical spec or ticket contract"
        )
    raw_result_checks = result.get("checks", [])
    result_checks: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_result_checks, list):
        raw_result_checks = []
        errors.append("Verification Result check ids are missing or duplicated")
    else:
        for raw_check in raw_result_checks:
            if not isinstance(raw_check, dict) or not isinstance(
                raw_check.get("id"), str
            ):
                errors.append("Verification Result check ids are missing or duplicated")
                continue
            check_id = raw_check["id"]
            if check_id in result_checks:
                errors.append("Verification Result check ids are missing or duplicated")
                continue
            result_checks[check_id] = raw_check
    plan_ids = {check["id"] for check in plan["checks"]}
    if set(result_checks) != plan_ids:
        errors.append("Verification Result check set does not match the current plan")

    expected_failed: list[str] = []
    expected_blocked: list[str] = []
    expected_warnings: list[str] = []
    for plan_check in plan["checks"]:
        result_check = result_checks.get(plan_check["id"])
        if result_check is None:
            errors.append(f"missing result for check {plan_check['id']}")
            continue
        check_id = plan_check["id"]
        check_result = result_check.get("result")
        if check_result not in ("PASS", "FAIL", "BLOCKED"):
            errors.append(f"check {plan_check['id']} has an invalid result")
        elif plan_check["blocking"] and check_result == "FAIL":
            expected_failed.append(check_id)
        elif plan_check["blocking"] and check_result == "BLOCKED":
            expected_blocked.append(check_id)
        elif not plan_check["blocking"] and check_result != "PASS":
            expected_warnings.append(check_id)
        if result_check.get("kind") != plan_check["kind"]:
            errors.append(f"check {check_id} kind does not match the current plan")
        if result_check.get("blocking") is not plan_check["blocking"]:
            errors.append(
                f"check {check_id} blocking flag does not match the current plan"
            )
        evidence = result_check.get("evidence")
        if not isinstance(evidence, dict) or not evidence:
            errors.append(f"check {check_id} has no evidence object")

        has_command = plan_check["kind"] == "command" or (
            plan_check["kind"] == "assertion" and "command" in plan_check
        )
        if has_command:
            errors.extend(_command_result_errors(plan_check, result_check))

        path_field: str | None = None
        if plan_check["kind"] in ("file", "artifact"):
            path_field = "path"
        elif (
            plan_check["kind"] == "assertion"
            and "command" not in plan_check
            and isinstance(plan_check.get("evidence_path"), str)
        ):
            path_field = "evidence_path"
        if path_field is not None:
            errors.extend(
                _path_result_errors(
                    tracker,
                    plan_check,
                    result_check,
                    path_field=path_field,
                    revalidate_artifacts=revalidate_artifacts,
                )
            )

        if (
            plan_check["kind"] in ("external", "manual-review")
            and (plan_check["blocking"] or result_check.get("result") == "PASS")
            and (
                not isinstance(evidence, dict)
                or evidence.get("result") != "PASS"
                or any(
                    not isinstance(evidence.get(field), str)
                    or not evidence[field].strip()
                    for field in ("reference", "issuer")
                )
            )
        ):
            errors.append(f"check {check_id} lacks independent PASS evidence")
        if (
            plan_check["kind"] == "assertion"
            and not has_command
            and path_field is None
            and check_result == "PASS"
            and (
                not isinstance(evidence, dict)
                or evidence.get("result") != "PASS"
                or not isinstance(evidence.get("reference"), str)
                or not evidence["reference"].strip()
            )
        ):
            errors.append(f"check {check_id} lacks stable assertion evidence")

    expected_overall = (
        "BLOCKED" if expected_blocked else "REMEDIATE" if expected_failed else "PASS"
    )
    if result.get("result") != expected_overall:
        errors.append(
            "Verification Result result does not match the outcomes of its checks"
        )
    for field, expected in (
        ("failed_checks", expected_failed),
        ("blocked_by", expected_blocked),
        ("warnings", expected_warnings),
    ):
        if result.get(field) != expected:
            errors.append(f"Verification Result {field} does not match its checks")
    if expected_overall != "PASS":
        errors.append("latest Verification Result is not PASS")
    if check_dependencies:
        for dependency in ticket.dependencies:
            if not dependency.external:
                satisfied, reason = _dependency_satisfied(tracker, dependency)
                if not satisfied:
                    errors.append(
                        f"Core dependency {dependency.ticket_id} is unsatisfied: {reason}"
                    )
    return errors


def _validate_completion(
    tracker: Tracker, ticket: Ticket, plan: dict[str, Any], result: dict[str, Any]
) -> None:
    errors = _completion_errors(
        tracker,
        ticket,
        plan,
        result,
        require_current_source=True,
        revalidate_artifacts=True,
        check_dependencies=True,
    )
    if errors:
        raise TicketCtlError(
            f"ticket {ticket.ticket_id} cannot be completed",
            kind="COMPLETION_REJECTED",
            details={"errors": errors},
        )


def _append_completion_evidence(ticket: Ticket, result: dict[str, Any]) -> str:
    body = ticket.sections.get("Evidence", "").rstrip()
    if body.strip().lower() in {"none", "n/a", "not yet"}:
        body = ""
    entry = _completion_receipt_entry(result)
    body = _append_completion_receipt(body, entry)
    return _replace_or_insert_section(ticket.text, "Evidence", body)


def _validated_ticket_candidate(
    tracker: Tracker, ticket: Ticket, text: str
) -> tuple[Tracker, Ticket]:
    candidate, errors = _parse_ticket_text(ticket.path, text)
    if candidate is None or errors:
        raise TicketCtlError(
            f"controller generated an invalid candidate for ticket {ticket.ticket_id}",
            kind="GENERATED_TICKET_INVALID",
            details={"errors": errors},
        )
    candidate_tickets = dict(tracker.tickets)
    candidate_tickets[ticket.ticket_id] = candidate
    return dataclasses.replace(tracker, tickets=candidate_tickets), candidate


def command_complete(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        if ticket.status != "In Progress":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot complete from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
        baseline = _require_tracker_baseline(tracker)
        plan = _load_plan(ticket)
        validated = _validate_plan(ticket, plan)
        normalized_plan = dict(plan)
        normalized_plan["checks"] = validated["checks"]
        results = _load_results(ticket)
        if not results:
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} has no Verification Result",
                kind="COMPLETION_REJECTED",
            )
        latest = results[-1]
        _validate_completion(tracker, ticket, normalized_plan, latest)

        try:
            with_evidence = _append_completion_evidence(ticket, latest)
        except ValueError as exc:
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} completion receipt cannot be generated",
                kind="COMPLETION_REJECTED",
                details={"errors": [str(exc)]},
            ) from exc
        done_text = _replace_metadata(with_evidence, "Status", "Done")
        candidate_tracker, candidate_ticket = _validated_ticket_candidate(
            tracker, ticket, done_text
        )
        candidate_integrity = _historical_completion_integrity(
            candidate_tracker, candidate_ticket
        )
        if candidate_integrity["state"] != "VALID":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} generated completion evidence is invalid",
                kind="COMPLETION_REJECTED",
                details={"errors": candidate_integrity["errors"]},
            )
        _atomic_write(ticket.path, done_text, expected_text=ticket.text)

        completed_tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        try:
            roadmap = sync_roadmap_internal(completed_tracker)
        except StateDriftFailure as exc:
            details = dict(exc.details)
            details.update(
                {
                    "ticket": ticket.ticket_id,
                    "ticket_status": "Done",
                    "verification_result": "PASS",
                }
            )
            raise StateDriftFailure(
                "ticket is Done, but derived roadmap/frontier synchronization failed",
                details,
            ) from exc
        return {
            "command": "complete",
            "ticket": ticket.ticket_id,
            "status": "Done",
            "verification_result": "PASS",
            "tracker_baseline": baseline,
            "pending_external_checkpoints": _pending_external_checkpoints(
                completed_tracker.tickets[ticket.ticket_id]
            ),
            "roadmap": roadmap,
            "frontier": frontier(completed_tracker),
        }


def command_confirm(args: argparse.Namespace) -> dict[str, Any]:
    tracker = _load_tracker_from_args(args)
    requested_ticket = getattr(args, "ticket", None)
    if requested_ticket is not None:
        ticket = _require_ticket(tracker, requested_ticket)
        if ticket.status != "In Progress":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot be confirmed from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
    else:
        active = [
            ticket
            for ticket in tracker.tickets.values()
            if ticket.status == "In Progress"
        ]
        active.sort(key=lambda ticket: _ticket_sort_key(ticket.ticket_id))
        if len(active) != 1:
            blocked = sorted(
                (
                    ticket.ticket_id
                    for ticket in tracker.tickets.values()
                    if ticket.status == "Blocked"
                ),
                key=_ticket_sort_key,
            )
            raise TicketCtlError(
                "confirm requires an explicit ticket or exactly one In Progress ticket",
                kind="ACTIVE_TICKET_AMBIGUOUS",
                details={
                    "in_progress": [ticket.ticket_id for ticket in active],
                    "blocked": blocked,
                },
            )
        ticket = active[0]

    resolved_values = vars(args).copy()
    resolved_values["ticket"] = ticket.ticket_id
    resolved_args = argparse.Namespace(**resolved_values)
    verification = command_verify(resolved_args)
    result = verification["result"]
    if result == "PASS":
        completion = command_complete(resolved_args)
        return {
            "command": "confirm",
            "ticket": ticket.ticket_id,
            "result": "PASS",
            "status": "Done",
            "source_revision": verification["source_revision"],
            "plan_hash": verification["plan_hash"],
            "immutable_contract_hash": verification["immutable_contract_hash"],
            "verification": verification,
            "completion": completion,
            "pending_external_checkpoints": completion["pending_external_checkpoints"],
            "roadmap": completion["roadmap"],
            "frontier": completion["frontier"],
        }

    current = load_tracker(
        Path(args.feature_root), roadmap_override=getattr(args, "roadmap", None)
    )
    current_ticket = _require_ticket(current, ticket.ticket_id)
    relevant_ids = set(verification["failed_checks"] + verification["blocked_by"])
    relevant_checks = [
        check for check in verification["checks"] if check["id"] in relevant_ids
    ]
    handoff = {
        "ticket": ticket.ticket_id,
        "result": result,
        "failed_checks": verification["failed_checks"],
        "blocked_by": verification["blocked_by"],
        "check_results": relevant_checks,
        "unlocking_conditions": {
            check["id"]: check["unlocking_condition"]
            for check in relevant_checks
            if isinstance(check.get("unlocking_condition"), str)
        },
        "implementation_agent_may_edit_source": result == "REMEDIATE",
        "next_command": (
            f"run-ticket-plan confirm {ticket.ticket_id}"
            if result == "REMEDIATE"
            else (
                f"ticketctl.py resume --feature-root {current.feature_root} "
                f"--ticket {ticket.ticket_id}, then run-ticket-plan confirm {ticket.ticket_id}"
            )
        ),
    }
    return {
        "command": "confirm",
        "ticket": ticket.ticket_id,
        "result": result,
        "status": current_ticket.status,
        "source_revision": verification["source_revision"],
        "plan_hash": verification["plan_hash"],
        "immutable_contract_hash": verification["immutable_contract_hash"],
        "verification": verification,
        "handoff": handoff,
        "roadmap": _roadmap_status(current),
        "frontier": frontier(current),
    }


def command_record_external(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        ticket = _require_ticket(tracker, args.ticket)
        if ticket.status != "Done":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} cannot record external evidence from status {ticket.status!r}",
                kind="INVALID_TRANSITION",
            )
        if args.result != "PASS":
            raise TicketCtlError(
                "external checkpoint result must be PASS",
                kind="CHECKPOINT_EVIDENCE_INVALID",
            )
        reference = args.reference.strip()
        issuer = args.issuer.strip()
        if not reference or not issuer:
            raise TicketCtlError(
                "external checkpoint reference and issuer must be non-empty",
                kind="CHECKPOINT_EVIDENCE_INVALID",
            )

        baseline = _require_tracker_baseline(tracker)
        completion_integrity = _historical_completion_integrity(tracker, ticket)
        if completion_integrity["state"] != "VALID":
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} has invalid Core completion integrity",
                kind="COMPLETION_INTEGRITY_INVALID",
                details={"errors": completion_integrity["errors"]},
            )
        results = _load_results(ticket)
        latest = results[-1]
        result_checks = {
            check["id"]: check
            for check in latest.get("checks", [])
            if isinstance(check, dict) and isinstance(check.get("id"), str)
        }
        completion_check = result_checks.get(args.check)
        if completion_check is None:
            raise TicketCtlError(
                f"unknown external checkpoint {args.check!r}",
                kind="UNKNOWN_CHECKPOINT",
            )
        if not (
            completion_check.get("kind") in ("external", "manual-review")
            and completion_check.get("blocking") is False
            and completion_check.get("result") != "PASS"
        ):
            raise TicketCtlError(
                f"check {args.check!r} was not a pending non-blocking external checkpoint at Core completion",
                kind="CHECKPOINT_NOT_ELIGIBLE",
            )

        external_integrity = _external_checkpoint_integrity(ticket)
        repair = bool(getattr(args, "repair", False))
        if external_integrity["state"] == "INVALID" and not repair:
            repair_command = shlex.join(
                [
                    "ticketctl.py",
                    "record-external",
                    "--feature-root",
                    str(tracker.feature_root),
                    "--ticket",
                    ticket.ticket_id,
                    "--check",
                    args.check,
                    "--result",
                    "PASS",
                    "--reference",
                    reference,
                    "--issuer",
                    issuer,
                    "--repair",
                ]
            )
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} has invalid external checkpoint evidence",
                kind="EXTERNAL_CHECKPOINT_INVALID",
                details={
                    "errors": external_integrity["errors"],
                    "repair_command": repair_command,
                },
            )
        existing = external_integrity["record_candidates"].get(args.check)
        if existing is not None:
            same_evidence = (
                existing["result"] == args.result
                and existing["reference"] == reference
                and existing["issuer"] == issuer
            )
            if same_evidence:
                if external_integrity["state"] != "INVALID":
                    return {
                        "command": "record-external",
                        "ticket": ticket.ticket_id,
                        "check": args.check,
                        "result": "PASS",
                        "changed": False,
                        "record": existing,
                        "tracker_baseline": baseline,
                        "pending_external_checkpoints": _pending_external_checkpoints(
                            ticket
                        ),
                    }
            else:
                raise TicketCtlError(
                    f"external checkpoint {args.check!r} already has different evidence",
                    kind="CHECKPOINT_CONFLICT",
                    details={"existing_record": existing},
                )

        if existing is None:
            receipt = _completion_receipt_entry(latest)
            record: dict[str, Any] = {
                "version": 1,
                "controller_version": CONTROLLER_VERSION,
                "ticket": ticket.ticket_id,
                "check": args.check,
                "result": "PASS",
                "reference": reference,
                "issuer": issuer,
                "recorded_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "core_result_hash": _canonical_json_hash(latest),
                "completion_receipt_hash": _text_hash(receipt),
            }
            record["record_hash"] = _external_record_hash(record)
        else:
            record = existing
        record_map = dict(external_integrity["record_candidates"])
        record_map[args.check] = record
        records = list(record_map.values())
        updated = _replace_or_collapse_sections(
            ticket.text,
            EXTERNAL_CHECKPOINT_RESULT_SECTION,
            _external_result_body(records),
        )
        updated = _replace_or_collapse_sections(
            updated,
            EXTERNAL_CHECKPOINT_ANCHOR_SECTION,
            _external_anchor_body(records),
        )
        candidate_tracker, candidate_ticket = _validated_ticket_candidate(
            tracker, ticket, updated
        )
        if (
            _historical_completion_integrity(candidate_tracker, candidate_ticket)[
                "state"
            ]
            != "VALID"
        ):
            raise TicketCtlError(
                f"ticket {ticket.ticket_id} Core completion changed while recording external evidence",
                kind="COMPLETION_INTEGRITY_INVALID",
            )
        candidate_integrity = _external_checkpoint_integrity(candidate_ticket)
        if candidate_integrity["state"] != "VALID":
            raise TicketCtlError(
                "controller generated invalid external checkpoint evidence",
                kind="GENERATED_TICKET_INVALID",
                details={"errors": candidate_integrity["errors"]},
            )
        _atomic_write(ticket.path, updated, expected_text=ticket.text)

        recorded_tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        recorded_ticket = recorded_tracker.tickets[ticket.ticket_id]
        recorded_integrity = _external_checkpoint_integrity(recorded_ticket)
        if recorded_integrity["state"] != "VALID":
            raise StateDriftFailure(
                "external checkpoint was written but failed integrity validation",
                {
                    "ticket": ticket.ticket_id,
                    "errors": recorded_integrity["errors"],
                    "repair": "Restore the atomic ticket write from version control before retrying.",
                },
            )
        return {
            "command": "record-external",
            "ticket": ticket.ticket_id,
            "check": args.check,
            "result": "PASS",
            "changed": True,
            "repaired": repair and external_integrity["state"] == "INVALID",
            "record": record,
            "tracker_baseline": baseline,
            "pending_external_checkpoints": _pending_external_checkpoints(
                recorded_ticket
            ),
        }


def command_sync_roadmap(args: argparse.Namespace) -> dict[str, Any]:
    feature_root = Path(args.feature_root)
    with _feature_lock(feature_root):
        tracker = load_tracker(
            feature_root, roadmap_override=getattr(args, "roadmap", None)
        )
        result = sync_roadmap_internal(
            tracker, regenerate=bool(getattr(args, "regenerate", False))
        )
        return {"command": "sync-roadmap", **result, "frontier": frontier(tracker)}


def command_removed_mode(args: argparse.Namespace) -> dict[str, Any]:
    raise TicketCtlError(
        f"{args.command!r} no longer implements tickets; use status, next, or confirm",
        kind="MIGRATION_REQUIRED",
        details={
            "replacement_modes": ["status", "next", "confirm"],
            "reason": "run-ticket-plan is now a control-plane workflow",
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    handlers = {
        "validate": command_validate,
        "status": command_status,
        "frontier": command_frontier,
        "prepare-next": command_prepare_next,
        "next": command_next,
        "confirm": command_confirm,
        "claim": command_claim,
        "resume": command_resume,
        "reconcile": command_reconcile,
        "plan-check": command_plan_check,
        "verify": command_verify,
        "complete": command_complete,
        "record-external": command_record_external,
        "sync-roadmap": command_sync_roadmap,
        "run": command_removed_mode,
        "continue": command_removed_mode,
    }
    ticket_commands = {
        "claim",
        "resume",
        "reconcile",
        "plan-check",
        "verify",
        "complete",
        "record-external",
    }
    for name, handler in handlers.items():
        child = subparsers.add_parser(name)
        child.add_argument("--feature-root", required=True)
        child.add_argument(
            "--roadmap",
            help="Roadmap path relative to the feature root; overrides .ticketctl.json discovery",
        )
        if name in ticket_commands:
            child.add_argument("--ticket", required=True)
        if name in {"prepare-next", "next", "confirm", "run"}:
            child.add_argument("--ticket")
        if name == "next":
            child.add_argument(
                "--plan-file",
                help="Path to the generated Verification Plan JSON object",
            )
        if name == "reconcile":
            child.add_argument(
                "--reason",
                help="Operator-supplied reason recorded in the reconciliation history",
            )
        if name == "record-external":
            child.add_argument("--check", required=True)
            child.add_argument("--result", required=True)
            child.add_argument("--reference", required=True)
            child.add_argument("--issuer", required=True)
            child.add_argument(
                "--repair",
                action="store_true",
                help="Explicitly rebuild damaged external-only records and anchors",
            )
        if name == "sync-roadmap":
            child.add_argument(
                "--regenerate",
                action="store_true",
                help="Explicitly rebuild the derived roadmap from formal tickets",
            )
        child.set_defaults(handler=handler)
    return parser


def _emit(payload: dict[str, Any], human: str, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
    print(human, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
        if args.command == "frontier":
            human = "Frontier: " + (", ".join(payload["frontier"]) or "none")
        elif args.command == "status":
            human = f"Status: {payload['state']}; frontier: " + (
                ", ".join(payload["frontier"]) or "none"
            )
        elif args.command == "prepare-next":
            human = f"Prepared planning packet for ticket {payload['ticket']['id']}"
        elif args.command == "next":
            human = f"Activated ticket {payload['ticket']}"
        elif args.command == "confirm":
            human = f"Confirmed ticket {payload['ticket']}: {payload['result']}"
        else:
            human = f"{args.command}: OK"
        _emit(payload, human)
        return 0
    except TicketCtlError as exc:
        payload = {
            "command": getattr(args, "command", None),
            "error": exc.kind,
            "message": str(exc),
        }
        if exc.details is not None:
            payload["details"] = exc.details
        _emit(payload, f"{exc.kind}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
