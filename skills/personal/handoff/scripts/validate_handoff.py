#!/usr/bin/env python3
"""Validate supported structural handoff Markdown contracts from stdin."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Sequence

LEGACY_SCHEMA_VERSION = "1.0"
SCHEMA_VERSION = "2.0"
PROFILES = ("full", "compact")
EVIDENCE_LEGEND = (
    "Evidence labels: `[V]` was verified while generating this handoff; `[?]` "
    "was remembered, inferred, or not rechecked. Drift-prone state must be "
    "rechecked before acting."
)
V2_EVIDENCE_LEGEND = (
    "Evidence labels: `[V]` was verified while generating this handoff; `[?]` "
    "was reported by the source session or trusted runtime context and not "
    "rechecked. Drift-prone state must be rechecked before acting."
)
CORE_HEADINGS = (
    "Goal And Definition Of Done",
    "Resume Context",
    "Current State",
    "Decisions And Rationale",
    "Constraints And Assumptions",
    "Open Questions",
    "Evidence And References",
    "Risks And Blockers",
    "Next Action",
    "Continuation Payload",
)
COMPACT_HEADINGS = (
    "Goal",
    "Resume At",
    "Current State",
    "Constraints",
    "Next Action",
    "Essential Context",
)
STATE_HEADINGS = ("Completed", "In Progress", "Remaining")
PAYLOAD_TYPES = (
    "Coding",
    "Debugging",
    "Writing Or Design",
    "Research",
    "Operations",
    "General",
)
LEGACY_FRONTMATTER_FIELDS = (
    "handoff_schema",
    "generated_at",
    "source_session",
)
FRONTMATTER_FIELDS = (
    "handoff_schema",
    "handoff_profile",
    "generated_at",
    "source_session",
)
ALLOWED_FRONTMATTER_FIELDS = set(FRONTMATTER_FIELDS)
SEMANTIC_SECTION_LABELS = {
    "Constraints And Assumptions": ("**Constraints**", "**Assumptions**"),
    "Risks And Blockers": ("**Risks**", "**Active Blockers**"),
}
BACKTICK_FENCE_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,})(?P<info>[^`]*)$"
)
BACKTICK_FENCE_CANDIDATE_RE = re.compile(r"^ {0,3}`{3,}")
TILDE_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>~{3,})(?P<info>.*)$")
HEADING_RE = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+"
    r"(?P<title>.*?)"
    r"(?:[ \t]+#+)?[ \t]*$"
)
INDENTED_ATX_RE = re.compile(r"^[ \t]+#{1,6}(?:[ \t]+|$)")
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
CONTAINER_ATX_RE = re.compile(
    r"^ {0,3}"
    r"(?:(?:>[ \t]*)|(?:(?:[-+*]|\d{1,9}[.)])[ \t]+))+"
    r"#{1,6}(?:[ \t]+|$)"
)
CONTAINER_SETEXT_RE = re.compile(
    r"^ {0,3}"
    r"(?:(?:>[ \t]*)|(?:(?:[-+*]|\d{1,9}[.)])[ \t]+))+"
    r" {0,3}(?:=+|-+)[ \t]*$"
)
RAW_HTML_HEADING_TAG_RE = re.compile(
    r"</?h[1-6](?=[\t />]|$)",
    re.IGNORECASE,
)
SOURCE_ACTION_PREFIX = "Source-prescribed next action:"
CONTINUITY_REQUEST_PREFIX = "Continuity request:"
COMPLETION_SENTINEL = "None - the stated goal is complete."
CONTINUITY_REQUEST_PATTERNS = (
    re.compile(r"^Continuity request: Ask .+ to provide .+\.$"),
    re.compile(r"^Continuity request: Ask .+ to grant access to .+\.$"),
)
ACTION_REQUEST = "Continuity request: Ask the requester to specify the next action."


class HandoffValidationError(ValueError):
    """The artifact does not satisfy the handoff/v1 structural contract."""


def _reject_forbidden_outer_structure(line: str, line_number: int) -> None:
    if INDENTED_ATX_RE.match(line):
        kind = "indented ATX heading"
    elif SETEXT_UNDERLINE_RE.fullmatch(line):
        kind = "setext heading underline"
    elif CONTAINER_SETEXT_RE.fullmatch(line):
        kind = "setext heading inside a block quote or list"
    elif CONTAINER_ATX_RE.match(line):
        kind = "heading inside a block quote or list"
    elif RAW_HTML_HEADING_TAG_RE.search(line):
        kind = "raw HTML heading tag"
    else:
        return
    raise HandoffValidationError(
        f"strict Markdown subset forbids {kind} at line {line_number}"
    )


def _fence_opener(line: str, line_number: int) -> str | None:
    backtick = BACKTICK_FENCE_RE.fullmatch(line)
    if backtick is not None:
        return backtick.group("fence")
    if BACKTICK_FENCE_CANDIDATE_RE.match(line):
        raise HandoffValidationError(
            "backtick fence info string must not contain a backtick "
            f"at line {line_number}"
        )
    tilde = TILDE_FENCE_RE.fullmatch(line)
    if tilde is not None:
        return tilde.group("fence")
    return None


def _is_fence_closer(line: str, character: str, length: int) -> bool:
    return re.fullmatch(
        rf" {{0,3}}{re.escape(character)}{{{length},}}[ \t]*",
        line,
    ) is not None


def _parse_json_string(raw: str, field: str) -> str:
    value = raw.strip()
    if not value.startswith('"'):
        raise HandoffValidationError(f"{field} must be a double-quoted string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(
            f"{field} must be a valid double-quoted string: {exc}"
        ) from exc
    if not isinstance(parsed, str):
        raise HandoffValidationError(f"{field} must be a string")
    return parsed


def _frontmatter(text: str) -> tuple[dict[str, str | None], int]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise HandoffValidationError("artifact must start with YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise HandoffValidationError("frontmatter closing delimiter is missing") from exc
    values: dict[str, str | None] = {}
    for line in lines[1:closing]:
        if not line.strip() or ":" not in line:
            raise HandoffValidationError("frontmatter must contain simple key/value lines")
        key, raw = line.split(":", 1)
        key = key.strip()
        if not key or key in values:
            raise HandoffValidationError(f"invalid or duplicate frontmatter key: {key!r}")
        if key not in ALLOWED_FRONTMATTER_FIELDS:
            raise HandoffValidationError(f"unknown frontmatter key: {key!r}")
        if key == "source_session" and raw.strip() == "null":
            values[key] = None
        else:
            values[key] = _parse_json_string(raw, key)
    return values, closing + 1


def _validate_frontmatter(values: dict[str, str | None]) -> str:
    schema = values.get("handoff_schema")
    if schema == LEGACY_SCHEMA_VERSION:
        required = set(LEGACY_FRONTMATTER_FIELDS)
        profile = "full"
    elif schema == SCHEMA_VERSION:
        required = set(FRONTMATTER_FIELDS)
        profile = values.get("handoff_profile")
        if profile not in PROFILES:
            raise HandoffValidationError(
                f"handoff_profile must be one of {list(PROFILES)} for schema {SCHEMA_VERSION}"
            )
    else:
        raise HandoffValidationError(
            "handoff_schema must equal a supported version: "
            f"{LEGACY_SCHEMA_VERSION!r} or {SCHEMA_VERSION!r}"
        )
    if set(values) != required:
        missing = sorted(required - set(values))
        unknown = sorted(set(values) - required)
        raise HandoffValidationError(
            f"frontmatter fields must be exactly {sorted(required)}; "
            f"missing={missing}, unknown={unknown}"
        )
    generated_at = values.get("generated_at")
    if not isinstance(generated_at, str) or "T" not in generated_at:
        raise HandoffValidationError("generated_at must be an ISO-8601 timestamp")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffValidationError("generated_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise HandoffValidationError("generated_at must include a timezone offset")
    source_session = values.get("source_session")
    if source_session is not None and (
        not isinstance(source_session, str) or not source_session.strip()
    ):
        raise HandoffValidationError("source_session must be a non-empty string or null")
    assert isinstance(profile, str)
    return profile


def _outer_lines(lines: list[str], body_start: int) -> list[tuple[int, str]]:
    outer: list[tuple[int, str]] = []
    fence_character: str | None = None
    fence_length = 0
    for index in range(body_start, len(lines)):
        line = lines[index]
        if fence_character is not None:
            if _is_fence_closer(line, fence_character, fence_length):
                fence_character = None
                fence_length = 0
            continue
        delimiter = _fence_opener(line, index + 1)
        if delimiter is not None:
            fence_character = delimiter[0]
            fence_length = len(delimiter)
            continue
        outer.append((index, line))
    if fence_character is not None:
        raise HandoffValidationError("artifact contains an unclosed fenced block")
    return outer


def _structural_headings(lines: list[str], body_start: int) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    for index, line in _outer_lines(lines, body_start):
        _reject_forbidden_outer_structure(line, index + 1)
        heading = HEADING_RE.fullmatch(line)
        if heading is not None:
            level = len(heading.group("marks"))
            if level > 3:
                raise HandoffValidationError(
                    "strict Markdown subset forbids outer heading levels 4-6 "
                    f"at line {index + 1}"
                )
            headings.append((index, level, heading.group("title")))
    return headings


def _unique_heading(
    headings: list[tuple[int, int, str]], level: int, title: str
) -> int:
    matches = [
        line
        for line, found_level, found_title in headings
        if found_level == level and found_title == title
    ]
    if len(matches) != 1:
        raise HandoffValidationError(
            f"expected exactly one level-{level} heading {title!r}; found {len(matches)}"
        )
    return matches[0]


def _section_text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start + 1 : end]).strip()


def _validate_labeled_section(
    lines: list[str],
    start: int,
    end: int,
    section: str,
    labels: tuple[str, ...],
) -> None:
    nonempty = [
        (index, lines[index].strip())
        for index in range(start + 1, end)
        if lines[index].strip()
    ]
    label_lines: list[int] = []
    for label in labels:
        matches = [index for index, value in nonempty if value == label]
        if len(matches) != 1:
            raise HandoffValidationError(
                f"{section} must use its canonical labels exactly once"
            )
        label_lines.append(matches[0])
    if label_lines != sorted(label_lines) or nonempty[0][1] != labels[0]:
        raise HandoffValidationError(
            f"{section} must use its canonical labels in order"
        )
    boundaries = [*label_lines, end]
    for index, label in enumerate(labels):
        if not _section_text(lines, boundaries[index], boundaries[index + 1]):
            raise HandoffValidationError(f"{section} label {label} must have content")


def _validate_provenance_section(
    body: str,
    section: str,
    prefixes: tuple[str, ...],
    *,
    allow_none: bool = False,
) -> None:
    if allow_none and body == "None":
        return
    items = [item.strip() for item in re.split(r"\n[ \t]*\n+", body) if item.strip()]
    if not items or any(
        not any(
            item.startswith(prefix) and item.removeprefix(prefix).strip()
            for prefix in prefixes
        )
        for item in items
    ):
        alternatives = " or ".join(prefixes)
        raise HandoffValidationError(
            f"section {section!r} items must start with {alternatives} and "
            "include content after the prefix"
        )


def _validate_compact_provenance_section(body: str, section: str) -> None:
    _validate_provenance_section(
        body,
        section,
        ("Verified:", "Reported:"),
    )


def _validate_compact_essential_context(body: str) -> None:
    if body == "None":
        return

    lines = body.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if any(
            line.startswith(prefix) and line.removeprefix(prefix).strip()
            for prefix in ("Verified:", "Reported:")
        ):
            index += 1
            continue
        artifact_prefix = "Artifact-owned:"
        if line.startswith(artifact_prefix) and line.removeprefix(
            artifact_prefix
        ).strip():
            opener_index = index + 1
            if opener_index >= len(lines):
                break
            delimiter = _fence_opener(lines[opener_index], opener_index + 1)
            if delimiter is None:
                break
            character = delimiter[0]
            length = len(delimiter)
            closer_index = opener_index + 1
            while closer_index < len(lines) and not _is_fence_closer(
                lines[closer_index], character, length
            ):
                closer_index += 1
            if closer_index >= len(lines) or not any(
                content.strip() for content in lines[opener_index + 1 : closer_index]
            ):
                break
            index = closer_index + 1
            continue
        break
    else:
        return

    raise HandoffValidationError(
        "compact section 'Essential Context' must be exact None or contain "
        "non-empty Verified: or Reported: lines, or Artifact-owned: descriptions "
        "immediately followed by one non-empty legal fenced block"
    )


def _validate_v2_next_action(body: str, profile: str) -> None:
    if body == COMPLETION_SENTINEL:
        return
    if "\n" not in body and body.startswith(f"{SOURCE_ACTION_PREFIX} "):
        if body.removeprefix(SOURCE_ACTION_PREFIX).strip():
            return
    if body == ACTION_REQUEST:
        return
    if "\n" not in body and " or " not in body and any(
        pattern.fullmatch(body) for pattern in CONTINUITY_REQUEST_PATTERNS
    ):
        return
    raise HandoffValidationError(
        f"{profile} Next Action must use Source-prescribed next action:, "
        "a supported Continuity request:, or the exact stated goal sentinel"
    )


def _validate_structure(
    lines: list[str],
    body_start: int,
    evidence_legend: str,
    enforce_v2_next_action: bool,
) -> None:
    first_body_line = next(
        (line for line in lines[body_start:] if line.strip()), None
    )
    if first_body_line != "# Handoff":
        raise HandoffValidationError(
            "# Handoff must be the first non-empty line after frontmatter"
        )
    headings = _structural_headings(lines, body_start)
    handoff_line = _unique_heading(headings, 1, "Handoff")
    level_one = [(line, title) for line, level, title in headings if level == 1]
    if level_one != [(handoff_line, "Handoff")]:
        raise HandoffValidationError("# Handoff must be the only level-1 heading")

    core_lines = [_unique_heading(headings, 2, title) for title in CORE_HEADINGS]
    if core_lines != sorted(core_lines) or handoff_line >= core_lines[0]:
        raise HandoffValidationError("stable-core headings are out of order")
    if _section_text(lines, handoff_line, core_lines[0]):
        raise HandoffValidationError(
            "# Handoff must not contain direct body content"
        )
    unknown_level_two = [
        title for _, level, title in headings if level == 2 and title not in CORE_HEADINGS
    ]
    if unknown_level_two:
        raise HandoffValidationError(
            f"unknown level-2 headings: {sorted(set(unknown_level_two))}"
        )

    state_lines = [_unique_heading(headings, 3, title) for title in STATE_HEADINGS]
    state_start = core_lines[CORE_HEADINGS.index("Current State")]
    state_end = core_lines[CORE_HEADINGS.index("Decisions And Rationale")]
    if state_lines != sorted(state_lines) or not all(
        state_start < line < state_end for line in state_lines
    ):
        raise HandoffValidationError("Current State subheadings are missing or out of order")
    if _section_text(lines, state_start, state_lines[0]):
        raise HandoffValidationError(
            "Current State must not contain direct body content"
        )

    payload_start = core_lines[CORE_HEADINGS.index("Continuation Payload")]
    payload_headings = [
        (line, title)
        for line, level, title in headings
        if level == 3 and line > payload_start
    ]
    if not payload_headings:
        raise HandoffValidationError("Continuation Payload requires at least one payload type")
    if _section_text(lines, payload_start, payload_headings[0][0]):
        raise HandoffValidationError(
            "Continuation Payload must not contain direct body content"
        )
    unknown_payloads = [title for _, title in payload_headings if title not in PAYLOAD_TYPES]
    if unknown_payloads:
        raise HandoffValidationError(
            f"unknown Continuation Payload types: {sorted(set(unknown_payloads))}"
        )
    payload_names = [title for _, title in payload_headings]
    if len(payload_names) != len(set(payload_names)):
        raise HandoffValidationError("Continuation Payload types must not be duplicated")

    allowed_level_three = set(STATE_HEADINGS) | set(PAYLOAD_TYPES)
    unexpected_level_three = [
        title
        for _, level, title in headings
        if level == 3 and title not in allowed_level_three
    ]
    if unexpected_level_three:
        raise HandoffValidationError(
            f"unknown level-3 headings: {sorted(set(unexpected_level_three))}"
        )
    misplaced_payloads = [
        title
        for line, level, title in headings
        if level == 3 and title in PAYLOAD_TYPES and line <= payload_start
    ]
    if misplaced_payloads:
        raise HandoffValidationError(
            f"payload headings must be under Continuation Payload: {misplaced_payloads}"
        )

    section_boundaries = [*core_lines, len(lines)]
    for index, title in enumerate(CORE_HEADINGS):
        if title == "Current State" or title == "Continuation Payload":
            continue
        body = _section_text(lines, section_boundaries[index], section_boundaries[index + 1])
        if not body:
            raise HandoffValidationError(f"section {title!r} must not be empty")
    for section, labels in SEMANTIC_SECTION_LABELS.items():
        index = CORE_HEADINGS.index(section)
        _validate_labeled_section(
            lines,
            section_boundaries[index],
            section_boundaries[index + 1],
            section,
            labels,
        )
    if enforce_v2_next_action:
        resume_index = CORE_HEADINGS.index("Resume Context")
        _validate_provenance_section(
            _section_text(
                lines,
                section_boundaries[resume_index],
                section_boundaries[resume_index + 1],
            ),
            "Resume Context",
            ("[V]", "[?]"),
            allow_none=True,
        )
        action_index = CORE_HEADINGS.index("Next Action")
        _validate_v2_next_action(
            _section_text(
                lines,
                section_boundaries[action_index],
                section_boundaries[action_index + 1],
            ),
            "full",
        )
    state_boundaries = [*state_lines, state_end]
    for index, title in enumerate(STATE_HEADINGS):
        if not _section_text(lines, state_boundaries[index], state_boundaries[index + 1]):
            raise HandoffValidationError(f"Current State subsection {title!r} must not be empty")
    payload_boundaries = [line for line, _ in payload_headings] + [len(lines)]
    for index, (_, title) in enumerate(payload_headings):
        if not _section_text(lines, payload_boundaries[index], payload_boundaries[index + 1]):
            raise HandoffValidationError(f"payload {title!r} must not be empty")

    evidence_start = core_lines[CORE_HEADINGS.index("Evidence And References")]
    evidence_end = core_lines[CORE_HEADINGS.index("Risks And Blockers")]
    evidence_lines = [
        line.strip()
        for line in lines[evidence_start + 1 : evidence_end]
        if line.strip()
    ]
    if not evidence_lines or evidence_lines[0] != evidence_legend:
        raise HandoffValidationError(
            "Evidence And References must start with the evidence label legend"
        )
    if len(evidence_lines) == 1:
        raise HandoffValidationError(
            "the evidence label legend must be followed by evidence, a reference, or None"
        )


def _validate_compact_structure(lines: list[str], body_start: int) -> None:
    first_body_line = next(
        (line for line in lines[body_start:] if line.strip()), None
    )
    if first_body_line != "# Handoff":
        raise HandoffValidationError(
            "# Handoff must be the first non-empty line after frontmatter"
        )
    headings = _structural_headings(lines, body_start)
    symbolic_provenance = [
        index + 1
        for index, line in _outer_lines(lines, body_start)
        if "[V]" in line or "[?]" in line
    ]
    if symbolic_provenance:
        raise HandoffValidationError(
            "compact profile forbids symbolic provenance outside transient data "
            f"at lines {symbolic_provenance}"
        )
    handoff_line = _unique_heading(headings, 1, "Handoff")
    level_one = [(line, title) for line, level, title in headings if level == 1]
    if level_one != [(handoff_line, "Handoff")]:
        raise HandoffValidationError("# Handoff must be the only level-1 heading")

    section_lines = [
        _unique_heading(headings, 2, title) for title in COMPACT_HEADINGS
    ]
    if section_lines != sorted(section_lines) or handoff_line >= section_lines[0]:
        raise HandoffValidationError("compact headings are out of order")
    if _section_text(lines, handoff_line, section_lines[0]):
        raise HandoffValidationError(
            "# Handoff must not contain direct body content"
        )

    unknown_level_two = [
        title
        for _, level, title in headings
        if level == 2 and title not in COMPACT_HEADINGS
    ]
    if unknown_level_two:
        raise HandoffValidationError(
            f"unknown compact level-2 headings: {sorted(set(unknown_level_two))}"
        )
    level_three = [title for _, level, title in headings if level == 3]
    if level_three:
        raise HandoffValidationError(
            f"compact profile forbids level-3 headings: {sorted(set(level_three))}"
        )

    boundaries = [*section_lines, len(lines)]
    for index, title in enumerate(COMPACT_HEADINGS):
        body = _section_text(lines, boundaries[index], boundaries[index + 1])
        if not body:
            raise HandoffValidationError(
                f"compact section {title!r} must not be empty"
            )
        if title in {"Resume At", "Current State"}:
            _validate_compact_provenance_section(body, title)
        elif title == "Next Action":
            _validate_v2_next_action(body, "compact")
        elif title == "Essential Context":
            _validate_compact_essential_context(body)


def validate_handoff(content: bytes | str) -> None:
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise HandoffValidationError(f"artifact is not valid UTF-8: {exc}") from exc
    else:
        text = content
    if not text.strip():
        raise HandoffValidationError("artifact must contain non-blank Markdown")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    values, body_start = _frontmatter(normalized)
    profile = _validate_frontmatter(values)
    lines = normalized.splitlines()
    if profile == "full":
        evidence_legend = (
            V2_EVIDENCE_LEGEND
            if values["handoff_schema"] == SCHEMA_VERSION
            else EVIDENCE_LEGEND
        )
        _validate_structure(
            lines,
            body_start,
            evidence_legend,
            values["handoff_schema"] == SCHEMA_VERSION,
        )
    else:
        _validate_compact_structure(lines, body_start)


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("validate_handoff: no arguments are accepted", file=sys.stderr)
        return 2
    try:
        validate_handoff(sys.stdin.buffer.read())
    except (OSError, HandoffValidationError) as error:
        print(f"validate_handoff: {error}", file=sys.stderr)
        return 1
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
