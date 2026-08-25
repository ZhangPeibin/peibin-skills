from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_handoff.py"
FIXTURE = Path(__file__).parent / "fixtures" / "valid-handoff.md"
COMPACT_FIXTURE = Path(__file__).parent / "fixtures" / "valid-compact-handoff.md"
SPEC = importlib.util.spec_from_file_location("validate_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validate_handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_handoff)

LEGEND = validate_handoff.EVIDENCE_LEGEND
V2_LEGEND = (
    "Evidence labels: `[V]` was verified while generating this handoff; `[?]` "
    "was reported by the source session or trusted runtime context and not "
    "rechecked. Drift-prone state must be rechecked before acting."
)
VALID_ARTIFACT = FIXTURE.read_text(encoding="utf-8")
VALID_COMPACT_ARTIFACT = COMPACT_FIXTURE.read_text(encoding="utf-8")


def artifact(*, payload: str = "Coding details.") -> str:
    return VALID_ARTIFACT.replace("Coding details.", payload)


def v2_full_artifact() -> str:
    return artifact().replace(
        'handoff_schema: "1.0"\n',
        'handoff_schema: "2.0"\nhandoff_profile: "full"\n',
        1,
    ).replace(LEGEND, V2_LEGEND, 1).replace(
        "Run the next check because it establishes readiness; expect PASS.",
        "Source-prescribed next action: Run the next check because it establishes readiness; expect PASS.",
        1,
    )


class ValidateHandoffTest(unittest.TestCase):
    def test_complete_artifact_is_valid(self) -> None:
        validate_handoff.validate_handoff(artifact())
        validate_handoff.validate_handoff(artifact().encode("utf-8"))

    def test_compact_artifact_is_valid(self) -> None:
        validate_handoff.validate_handoff(VALID_COMPACT_ARTIFACT)
        validate_handoff.validate_handoff(VALID_COMPACT_ARTIFACT.encode("utf-8"))

    def test_compact_resume_and_state_require_explicit_provenance(self) -> None:
        invalid = (
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: Repository `/work/example`; source working directory not supplied.",
                "None",
            ),
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: Repository `/work/example`; source working directory not supplied.",
                "Repository `/work/example`.",
            ),
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: The task is unfinished.",
                "The task is unfinished.",
            ),
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: The task is unfinished.",
                "[?] The task is unfinished.",
            ),
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: Repository `/work/example`; source working directory not supplied.",
                "Reported:",
            ),
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: The task is unfinished.",
                "Verified:   ",
            ),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate[:160]), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "Verified:|Reported:|symbolic provenance",
            ):
                validate_handoff.validate_handoff(candidate)

        validate_handoff.validate_handoff(
            VALID_COMPACT_ARTIFACT.replace(
                "Reported: The task is unfinished.",
                "Verified: The task is unfinished.",
            )
        )

    def test_compact_essential_context_accepts_explicit_provenance(self) -> None:
        valid = (
            "None",
            "Verified: `python3 -m unittest` passed.\n\n"
            "Reported: `notes/release.md` contains the remaining draft.",
            "Artifact-owned: Unrecoverable release note draft.\n"
            "````markdown\n"
            "# Draft\n\n"
            "```text\n"
            "inner fence\n"
            "```\n"
            "````",
        )
        for essential_context in valid:
            with self.subTest(essential_context=essential_context):
                validate_handoff.validate_handoff(
                    VALID_COMPACT_ARTIFACT.replace(
                        "## Essential Context\n\nNone",
                        f"## Essential Context\n\n{essential_context}",
                    )
                )

    def test_compact_essential_context_rejects_missing_provenance(self) -> None:
        invalid = (
            "The focused test passed.",
            "Verified:",
            "Reported:   ",
            "Reported: One fact.\nunprefixed continuation",
            "Artifact-owned:",
            "```text\nunlabeled transient data\n```",
            "Artifact-owned: Empty draft.\n```text\n\n```",
            "Artifact-owned: Unrecoverable draft.\n\n```text\ndraft\n```",
            "Artifact-owned: Unrecoverable draft.\nDescription before fence.\n"
            "```text\ndraft\n```",
            "Artifact-owned: Unrecoverable draft without a fence.",
            "None\n\nReported: Extra fact.",
            "Artifact-owned: First draft.\n```text\none\n```\n\n"
            "```text\ntwo\n```",
        )
        for essential_context in invalid:
            candidate = VALID_COMPACT_ARTIFACT.replace(
                "## Essential Context\n\nNone",
                f"## Essential Context\n\n{essential_context}",
            )
            with self.subTest(essential_context=essential_context), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "Essential Context|Verified:|Reported:|Artifact-owned:",
            ):
                validate_handoff.validate_handoff(candidate)

    def test_compact_next_action_uses_an_explicit_source_or_mechanical_form(self) -> None:
        original = "Source-prescribed next action: Run the focused check."
        valid = (
            "Continuity request: Ask the requester to provide the malformed-input fixture.",
            "Continuity request: Ask the repository owner to grant access to `/work/orders`.",
            "Continuity request: Ask the requester to specify the next action.",
            "None - the stated goal is complete.",
        )
        for replacement in valid:
            with self.subTest(replacement=replacement):
                validate_handoff.validate_handoff(
                    VALID_COMPACT_ARTIFACT.replace(original, replacement)
                )

        invalid = (
            "Run the focused check.",
            "Reported: Run the focused check.",
            "Continuity request: Inspect the failing assertion.",
            "Continuity request: Ask the requester to provide logs or grant access.",
            "Source-prescribed next action: Run the focused check.\nThen inspect logs.",
        )
        for replacement in invalid:
            with self.subTest(replacement=replacement), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "Source-prescribed next action|Continuity request|stated goal",
            ):
                validate_handoff.validate_handoff(
                    VALID_COMPACT_ARTIFACT.replace(original, replacement)
                )

    def test_compact_rejects_symbolic_provenance_outside_transient_data(self) -> None:
        for label in ("[V]", "[?]"):
            candidate = VALID_COMPACT_ARTIFACT.replace(
                "## Essential Context\n\nNone",
                f"## Essential Context\n\n{label} Legacy claim.",
            )
            with self.subTest(label=label), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "symbolic provenance",
            ):
                validate_handoff.validate_handoff(candidate)

        fenced = VALID_COMPACT_ARTIFACT.replace(
            "## Essential Context\n\nNone",
            "## Essential Context\n\nArtifact-owned: Preserved source data.\n"
            "```text\n[V] and [?] are preserved data.\n```",
        )
        validate_handoff.validate_handoff(fenced)

    def test_schema_two_requires_a_known_profile_and_legacy_has_none(self) -> None:
        validate_handoff.validate_handoff(v2_full_artifact())

        invalid = (
            artifact().replace('handoff_schema: "1.0"', 'handoff_schema: "2.0"'),
            v2_full_artifact().replace('handoff_profile: "full"\n', ""),
            v2_full_artifact().replace(
                'handoff_profile: "full"', 'handoff_profile: "unknown"'
            ),
            artifact().replace(
                'handoff_schema: "1.0"\n',
                'handoff_schema: "1.0"\nhandoff_profile: "full"\n',
                1,
            ),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate[:100]), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(candidate)

    def test_full_evidence_legend_matches_the_schema_semantics(self) -> None:
        validate_handoff.validate_handoff(artifact())
        validate_handoff.validate_handoff(v2_full_artifact())

        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "evidence label legend"
        ):
            validate_handoff.validate_handoff(
                artifact().replace(LEGEND, V2_LEGEND, 1)
            )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "evidence label legend"
        ):
            validate_handoff.validate_handoff(
                v2_full_artifact().replace(V2_LEGEND, LEGEND, 1)
            )

    def test_schema_two_full_next_action_uses_the_shared_forms(self) -> None:
        validate_handoff.validate_handoff(artifact())
        for replacement in (
            "Source-prescribed next action: Run the focused check.",
            "Continuity request: Ask the requester to provide malformed-order.json.",
            "Continuity request: Ask the requester to specify the next action.",
            "None - the stated goal is complete.",
        ):
            with self.subTest(replacement=replacement):
                validate_handoff.validate_handoff(
                    v2_full_artifact().replace(
                        "Source-prescribed next action: Run the next check because it establishes readiness; expect PASS.",
                        replacement,
                        1,
                    )
                )

        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError,
            "full Next Action",
        ):
            validate_handoff.validate_handoff(
                v2_full_artifact().replace(
                    "Source-prescribed next action: Run the next check because it establishes readiness; expect PASS.",
                    "Ask the requester to specify the next action.",
                    1,
                )
            )

    def test_frontmatter_is_required_and_schema_is_supported(self) -> None:
        cases = (
            artifact().removeprefix("---\n"),
            artifact().replace('handoff_schema: "1.0"', 'handoff_schema: "2.0"'),
            artifact().replace("source_session: null\n", ""),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate[:40]), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(candidate)

    def test_schema_two_full_resume_context_requires_provenance(self) -> None:
        original = "## Resume Context\n\nNone"
        valid = (
            "None",
            "[?] Repository and working directory were not supplied.",
            "[V] Repository `/work/example`.\n\n[?] Working directory was not supplied.",
        )
        for resume_context in valid:
            with self.subTest(resume_context=resume_context):
                validate_handoff.validate_handoff(
                    v2_full_artifact().replace(
                        original,
                        f"## Resume Context\n\n{resume_context}",
                    )
                )

        invalid = (
            "Repository and working directory were not supplied.",
            "[?]",
            "[V] Repository `/work/example`.\n\nWorking directory was not supplied.",
        )
        for resume_context in invalid:
            candidate = v2_full_artifact().replace(
                original,
                f"## Resume Context\n\n{resume_context}",
            )
            with self.subTest(resume_context=resume_context), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                r"Resume Context|\[V\]|\[\?\]",
            ):
                validate_handoff.validate_handoff(candidate)

        validate_handoff.validate_handoff(
            artifact().replace(
                original,
                "## Resume Context\n\nLegacy unlabelled context.",
            )
        )

    def test_frontmatter_requires_canonical_scalar_encodings(self) -> None:
        replacements = (
            ('handoff_schema: "1.0"', "handoff_schema: 1.0"),
            ('handoff_schema: "1.0"', "handoff_schema: '1.0'"),
            (
                'generated_at: "2026-08-19T12:34:56+08:00"',
                "generated_at: 2026-08-19T12:34:56+08:00",
            ),
            ("source_session: null", "source_session: false"),
            ("source_session: null", "source_session: 123"),
            ("source_session: null", "source_session: []"),
            ("source_session: null", "source_session: {}"),
            ("source_session: null", "source_session: Null"),
            ("source_session: null", "source_session: ~"),
            ("source_session: null", "source_session: 'session-id'"),
            ("source_session: null", 'source_session: ""'),
        )
        for original, replacement in replacements:
            with self.subTest(replacement=replacement), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(
                    artifact().replace(original, replacement)
                )

        escaped_session = artifact().replace(
            "source_session: null", 'source_session: "session-\\\"quoted\\\""'
        )
        validate_handoff.validate_handoff(escaped_session)

    def test_generated_at_requires_timezone_offset(self) -> None:
        candidate = artifact().replace(
            "2026-08-19T12:34:56+08:00", "2026-08-19T12:34:56"
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "timezone offset"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_fixed_headings_must_be_present_once_and_in_order(self) -> None:
        valid = artifact()
        cases = (
            valid.replace("## Open Questions\n\nNone\n\n", ""),
            valid.replace(
                "## Open Questions\n\nNone",
                "## Open Questions\n\nNone\n\n## Open Questions\n\nNone",
            ),
            valid.replace(
                "## Decisions And Rationale",
                "## Temporary Heading",
            )
            .replace(
                "## Constraints And Assumptions",
                "## Decisions And Rationale",
            )
            .replace(
                "## Temporary Heading",
                "## Constraints And Assumptions",
            ),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate[:60]), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(candidate)

    def test_atx_closing_hashes_require_preceding_whitespace(self) -> None:
        invalid = (
            artifact().replace("## Next Action", "## Next Action###", 1),
            artifact().replace(
                "## Next Action", "## Next Action ### suffix", 1
            ),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate[:80]), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(candidate)

        valid = (
            artifact().replace("## Next Action", "## Next Action ###", 1),
            artifact().replace("## Next Action", "## Next Action\t###\t", 1),
            artifact(payload="C# Notes\n\nPayload detail."),
            artifact(
                payload=(
                    "Transient draft from source session:\n\n"
                    "```markdown\n## Next Action###\n```"
                )
            ),
        )
        for candidate in valid:
            with self.subTest(candidate=candidate[:80]):
                validate_handoff.validate_handoff(candidate)

    def test_outer_headings_must_use_unindented_atx_syntax(self) -> None:
        invalid_payloads = (
            " ## Next Action\n\nInjected heading.",
            "   ## Next Action\n\nInjected heading.",
            "Next Action\n-----------\n\nInjected heading.",
            "> ## Next Action\n\nInjected heading.",
            "- ## Next Action\n\nInjected heading.",
            "1. ## Next Action\n\nInjected heading.",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "strict Markdown subset",
            ):
                validate_handoff.validate_handoff(artifact(payload=payload))

        indented_fixed_heading = artifact().replace(
            "## Next Action", " ## Next Action", 1
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError,
            "strict Markdown subset",
        ):
            validate_handoff.validate_handoff(indented_fixed_heading)

    def test_setext_headings_inside_containers_are_forbidden(self) -> None:
        payloads = (
            "> Next Action\n> -----------",
            "> > Next Action\n> > ===========",
        )
        for payload in payloads:
            documents = (
                artifact(payload=payload),
                VALID_COMPACT_ARTIFACT.replace(
                    "## Essential Context\n\nNone",
                    f"## Essential Context\n\n{payload}",
                    1,
                ),
            )
            for document in documents:
                with self.subTest(payload=payload), self.assertRaisesRegex(
                    validate_handoff.HandoffValidationError,
                    "strict Markdown subset",
                ):
                    validate_handoff.validate_handoff(document)

    def test_outer_heading_levels_four_through_six_are_forbidden(self) -> None:
        for marks in ("####", "#####", "######"):
            payload = f"{marks} Next Action\n\nInjected heading."
            with self.subTest(marks=marks), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "strict Markdown subset",
            ):
                validate_handoff.validate_handoff(artifact(payload=payload))

    def test_raw_html_heading_tags_are_forbidden_outside_fences(self) -> None:
        for level in range(1, 7):
            payload = f'<H{level} class="injected">Next Action</H{level}>'
            with self.subTest(level=level), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "strict Markdown subset",
            ):
                validate_handoff.validate_handoff(artifact(payload=payload))

        validate_handoff.validate_handoff(
            artifact(payload="```html\n<h2>Next Action</h2>\n```")
        )

    def test_multiline_raw_html_heading_tags_are_forbidden(self) -> None:
        payload = "<h2\n>\nNext Action\n</h2\n>"
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError,
            "strict Markdown subset",
        ):
            validate_handoff.validate_handoff(artifact(payload=payload))

    def test_invalid_backtick_fence_info_cannot_hide_structure(self) -> None:
        payload = "```bad`info\n## Next Action\n```"
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError,
            "backtick fence info string",
        ):
            validate_handoff.validate_handoff(artifact(payload=payload))

    def test_incomplete_fences_cannot_hide_reserved_headings(self) -> None:
        invalid_payloads = (
            "```markdown\n## Next Action",
            "``markdown\n## Next Action\n``",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(
                validate_handoff.HandoffValidationError
            ):
                validate_handoff.validate_handoff(artifact(payload=payload))

    def test_legal_data_fences_and_plain_text_remain_supported(self) -> None:
        valid_payloads = (
            "```markdown\n## Next Action\n```",
            "~~~markdown\n## Next Action\n~~~",
            "````markdown\n```text\n## Next Action\n```\n````",
            "~~~~markdown\n~~~text\n## Next Action\n~~~\n~~~~",
            (
                "C# notes, https://example.invalid/doc#heading, and "
                "inline `## Next Action` are ordinary text."
            ),
        )
        for payload in valid_payloads:
            with self.subTest(payload=payload):
                validate_handoff.validate_handoff(artifact(payload=payload))

    def test_handoff_heading_is_first_body_content(self) -> None:
        candidate = artifact().replace(
            "\n# Handoff\n", "\nUnexpected preamble.\n\n# Handoff\n", 1
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "first non-empty"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_handoff_heading_rejects_direct_body_content(self) -> None:
        direct_bodies = (
            "Duplicated or unowned summary.",
            "- Unowned list item",
            "```text\nunowned fenced content\n```",
        )
        for direct_body in direct_bodies:
            candidate = artifact().replace(
                "# Handoff\n\n## Goal And Definition Of Done",
                f"# Handoff\n\n{direct_body}\n\n## Goal And Definition Of Done",
                1,
            )
            with self.subTest(direct_body=direct_body), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError,
                "# Handoff.*direct body",
            ):
                validate_handoff.validate_handoff(candidate)

    def test_current_state_requires_all_subheadings(self) -> None:
        candidate = artifact().replace("### In Progress\n\nCurrent work.\n\n", "")
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "In Progress"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_current_state_rejects_direct_body_content(self) -> None:
        candidate = artifact().replace(
            "## Current State\n\n### Completed",
            "## Current State\n\nDuplicated lifecycle summary.\n\n### Completed",
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "Current State.*direct"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_payload_type_must_be_known(self) -> None:
        candidate = artifact().replace("### Coding", "### Unknown Type")
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "unknown Continuation Payload"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_continuation_payload_rejects_direct_body_content(self) -> None:
        candidate = artifact().replace(
            "## Continuation Payload\n\n### Coding",
            "## Continuation Payload\n\nUnowned payload text.\n\n### Coding",
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError,
            "Continuation Payload.*direct",
        ):
            validate_handoff.validate_handoff(candidate)

    def test_evidence_legend_is_mandatory_and_first(self) -> None:
        for candidate in (
            artifact().replace(f"{LEGEND}\n\n", ""),
            artifact().replace(f"{LEGEND}\n\nNone", f"None\n\n{LEGEND}"),
        ):
            with self.subTest(), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError, "evidence label legend"
            ):
                validate_handoff.validate_handoff(candidate)

    def test_evidence_legend_requires_following_content(self) -> None:
        candidate = artifact().replace(f"{LEGEND}\n\nNone", LEGEND)
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "legend must be followed"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_semantic_pair_sections_require_canonical_labels(self) -> None:
        cases = (
            artifact().replace("**Constraints**\n\nNone\n\n", "", 1),
            artifact().replace("**Assumptions**", "**Premises**", 1),
            artifact().replace("**Risks**\n\nNone\n\n", "", 1),
            artifact().replace("**Active Blockers**", "**Blockers**", 1),
        )
        for candidate in cases:
            with self.subTest(), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError, "canonical labels"
            ):
                validate_handoff.validate_handoff(candidate)

    def test_semantic_pair_labels_require_content(self) -> None:
        cases = (
            artifact().replace(
                "**Constraints**\n\nNone\n\n**Assumptions**",
                "**Constraints**\n\n**Assumptions**",
            ),
            artifact().replace(
                "**Risks**\n\nNone\n\n**Active Blockers**",
                "**Risks**\n\n**Active Blockers**",
            ),
        )
        for candidate in cases:
            with self.subTest(), self.assertRaisesRegex(
                validate_handoff.HandoffValidationError, "must have content"
            ):
                validate_handoff.validate_handoff(candidate)

    def test_fenced_transient_markdown_cannot_break_schema(self) -> None:
        transient = '''Transient draft from source session:

````markdown
---
title: Draft
---

# Handoff

## Next Action

Do not parse this as structure.

```python
print("still inside the outer data fence")
```
````'''
        validate_handoff.validate_handoff(artifact(payload=transient))

    def test_unfenced_reserved_heading_is_rejected(self) -> None:
        candidate = artifact(payload="## Next Action\n\nInjected heading.")
        with self.assertRaises(validate_handoff.HandoffValidationError):
            validate_handoff.validate_handoff(candidate)

    def test_payload_heading_outside_payload_section_is_rejected(self) -> None:
        candidate = artifact().replace(
            "## Decisions And Rationale",
            "### Research\n\nMisplaced.\n\n## Decisions And Rationale",
        )
        with self.assertRaisesRegex(
            validate_handoff.HandoffValidationError, "must be under"
        ):
            validate_handoff.validate_handoff(candidate)

    def test_cli_reports_validity_without_echoing_artifact(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=artifact().encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"VALID\n")
        self.assertEqual(result.stderr, b"")

    def test_cli_failure_has_empty_stdout_and_diagnostic_stderr(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=b"not an artifact",
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.strip())

    def test_cli_rejects_unprovenanced_compact_essential_context(self) -> None:
        invalid = VALID_COMPACT_ARTIFACT.replace(
            "## Essential Context\n\nNone",
            "## Essential Context\n\nThe focused test passed.",
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=invalid.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"Essential Context", result.stderr)


if __name__ == "__main__":
    unittest.main()
