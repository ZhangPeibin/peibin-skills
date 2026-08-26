from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import unittest
from datetime import date
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
SKILL_PATH = SKILL_ROOT / "SKILL.md"
OPENAI_PATH = SKILL_ROOT / "agents" / "openai.yaml"
PAYLOADS_PATH = SKILL_ROOT / "references" / "payloads.md"
FULL_PROTOCOL_PATH = SKILL_ROOT / "references" / "full-protocol.md"
VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_handoff.py"
FORWARD_CASES_PATH = SKILL_ROOT / "tests" / "forward_cases.json"
INSTALL_EVAL_PATH = SKILL_ROOT / "tests" / "install-eval.json"
README_PATH = REPO_ROOT / "README.md"
README_ZH_PATH = REPO_ROOT / "README.zh-CN.md"
DESIGN_PATH = REPO_ROOT / "docs" / "handoff-skill-design.md"
MANIFEST_PATH = REPO_ROOT / ".claude-plugin" / "plugin.json"
RUNTIME_SKILL_FILES = (
    SKILL_PATH,
    OPENAI_PATH,
    FULL_PROTOCOL_PATH,
    PAYLOADS_PATH,
    SKILL_ROOT / "scripts" / "publish_handoff.py",
    VALIDATOR_PATH,
)
CANONICAL_HANDOFF_PATH = "skills/personal/handoff"
VENDOR_HANDOFF_PATH = "skills/vendor/mattpocock/productivity/handoff"
ALLOWED_DUPLICATE_SKILLS = {
    "handoff": {CANONICAL_HANDOFF_PATH, VENDOR_HANDOFF_PATH},
}
EXPECTED_FORWARD_CASE_IDS = {
    "artifact_only",
    "completed_task",
    "debugging_hypotheses",
    "focus_contains_absolute_path",
    "focus_contains_cli_flags",
    "non_git_session",
    "operations_rollback",
    "partial_coding",
    "research_conflict",
    "sensitive_untrusted_input_compact",
    "sensitive_untrusted_input",
    "source_named_missing_fixture",
    "symptom_without_hypothesis",
    "transient_markdown",
}
GLOBAL_FORWARD_INVARIANTS = {
    "active_blockers_actually_block",
    "constraints_are_prescriptive",
    "drift_claims_are_labeled",
    "facts_have_single_owner",
    "next_action_is_executable_or_completed",
    "new_domain_analysis_is_forbidden",
    "payload_does_not_repeat_stable_core",
    "resume_test_passes",
}
FORWARD_RESULT_FIELDS = {
    "artifact_profile",
    "artifact_path",
    "artifact_schema",
    "artifact_sha256",
    "date",
    "evaluator",
    "invariants",
    "model",
    "notes",
    "run_id",
    "skill_revision",
    "status",
    "validator_result",
}

SPEC = importlib.util.spec_from_file_location("handoff_contract_validator", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def frontmatter(text: str) -> dict[str, str]:
    opening, raw, _ = text.split("---", 2)
    if opening.strip():
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    return {
        key.strip(): value.strip().strip('"')
        for line in raw.strip().splitlines()
        for key, value in [line.split(":", 1)]
    }


def artifact_template(document: str) -> str:
    return document.split("```markdown", 1)[1].split("```", 1)[0].strip()


def skill_content_sha256() -> str:
    digest = hashlib.sha256()
    for path in RUNTIME_SKILL_FILES:
        relative = path.relative_to(SKILL_ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")
        cls.openai = OPENAI_PATH.read_text(encoding="utf-8")
        cls.full_protocol = FULL_PROTOCOL_PATH.read_text(encoding="utf-8")
        cls.payloads = PAYLOADS_PATH.read_text(encoding="utf-8")
        cls.compact_template = artifact_template(cls.skill)
        cls.full_template = artifact_template(cls.full_protocol)

    def test_cross_client_invocation_is_explicit_only(self) -> None:
        metadata = frontmatter(self.skill)
        self.assertEqual(metadata["disable-model-invocation"], "true")
        self.assertRegex(
            self.openai,
            r"(?m)^policy:\n  allow_implicit_invocation: false$",
        )
        self.assertIn("$handoff", self.openai)

    def test_argument_hint_exposes_invocation_protocol(self) -> None:
        hint = frontmatter(self.skill)["argument-hint"]
        for token in (
            "--compact",
            "--output",
            "--force",
            "--artifact-only",
            "<focus-with-flags>",
        ):
            self.assertIn(token, hint)

    def test_focus_is_trusted_but_does_not_authorize_generator_analysis(self) -> None:
        self.assertIn(
            "Focus is trusted requester input",
            self.skill,
        )
        self.assertIn(
            "It may select source facts or supply one successor action",
            self.skill,
        )
        self.assertIn(
            "An explicit focus action overrides older action ordering",
            self.skill,
        )
        self.assertIn(
            "Focus never authorizes new analysis or claims",
            self.skill,
        )

    def test_focus_path_is_not_probed_outside_an_established_source_environment(self) -> None:
        self.assertIn(
            "A path named by the source or focus does not establish that the generator is running in the source environment",
            self.skill,
        )
        self.assertIn(
            "Do not probe it unless trusted runtime context establishes that exact environment as accessible",
            self.skill,
        )
        self.assertIn(
            "Never replace an explicit focus action with a generator-runtime missing-path claim",
            self.skill,
        )

    def test_ui_and_readme_expose_both_content_profiles(self) -> None:
        self.assertIn("$handoff", self.openai)
        self.assertIn("$handoff --compact", self.openai)
        for path in (README_PATH, README_ZH_PATH):
            readme = path.read_text(encoding="utf-8")
            self.assertIn("$handoff --compact", readme)
            self.assertIn("$handoff --compact --artifact-only", readme)

    def test_profiles_use_progressive_disclosure(self) -> None:
        self.assertIn("references/full-protocol.md", self.skill)
        self.assertIn("Do not read the full protocol", self.skill)
        self.assertIn("payloads.md", self.full_protocol)
        self.assertNotIn("references/payloads.md", self.skill)
        self.assertLess(len(self.skill.split()), 1200)

    def test_provenance_never_authorizes_new_analysis(self) -> None:
        self.assertIn(
            "never authorize the generator to introduce a new claim",
            self.skill,
        )
        self.assertIn("provenance, no-new-claim", self.full_protocol)
        self.assertIn("may not extend it", self.full_protocol)
        self.assertNotIn("was remembered, inferred, or not rechecked", self.full_protocol)
        self.assertIn(
            "including statements that a value was not supplied",
            self.full_protocol,
        )
        for forbidden in (
            "new hypothesis",
            "new causal explanation",
            "new risk",
            "new technical action",
        ):
            self.assertIn(forbidden, self.skill)
        self.assertIn("must not create missing analysis", self.payloads)
        self.assertNotIn("session inference", self.payloads)
        self.assertNotIn("inference boundary", self.payloads)

    def test_next_action_is_source_prescribed_or_mechanical(self) -> None:
        for phrase in (
            "Source-prescribed next action:",
            "Continuity request:",
            "Ask <source-named owner or requester> to provide <source-named missing object>.",
            "Ask <source-named owner or requester> to grant access to <reported environment>.",
            "Ask the requester to specify the next action.",
            "None - the stated goal is complete.",
        ):
            self.assertIn(phrase, self.skill)
        self.assertIn("source-named missing object", self.skill)
        self.assertIn("explicitly states that continuation requires", self.skill)
        self.assertIn("including for its action", self.skill)
        self.assertIn("A named omission or limitation alone is insufficient", self.skill)
        self.assertIn(
            "Do not treat an object as missing or inaccessible merely because its contents are not embedded",
            self.skill,
        )
        self.assertIn(
            "preserve that action unless the source explicitly reports the object missing or inaccessible",
            self.skill,
        )
        self.assertIn("source-reported inaccessible environment", self.skill)
        self.assertIn(
            "A goal or request is not a source action merely because it uses an imperative verb",
            self.skill,
        )
        self.assertIn(
            "Prefix every mechanical request with `Continuity request:`",
            self.skill,
        )
        self.assertIn(
            "return only the bare absolute path, without label or Markdown",
            self.skill,
        )

    def test_compact_empty_fields_are_exact_and_embedded_content_is_owned(self) -> None:
        self.assertIn("write exact `None` without punctuation", self.skill)
        self.assertIn("`Verified: <content>`", self.skill)
        self.assertIn("`Reported: <content>`", self.skill)
        self.assertIn("`Artifact-owned: <description>`", self.skill)
        self.assertIn("immediately followed by one legal fenced block", self.skill)
        self.assertIn("Reject unprefixed outer prose, empty labels", self.skill)

    def test_compact_sections_keep_single_fact_ownership(self) -> None:
        self.assertIn("Next Action alone owns the immediate action", self.skill)
        self.assertIn(
            "omit paths, lifecycle, constraints, actions, and lower-owned symptom/evidence",
            self.skill,
        )
        self.assertIn(
            "For investigations, name only the source's system/object",
            self.skill,
        )
        self.assertIn(
            "route every supplied symptom or observation, including error/status codes, only to Essential Context",
            self.skill,
        )
        self.assertIn("never invent either or a cause", self.skill)
        self.assertIn(
            "For session-only work explicitly required recoverable, use `Preserve the supplied unfinished work.`",
            self.skill,
        )
        self.assertIn(
            "Unfinished never implies completion",
            self.skill,
        )
        self.assertIn(
            "Constraints must not repeat Goal-owned preservation, lifecycle, or action facts",
            self.skill,
        )
        self.assertIn(
            "A source-supplied `only if` or `unless` guard on a deferred action belongs here",
            self.skill,
        )
        self.assertIn(
            "When an access request displaces an explicit focus command",
            self.skill,
        )
        self.assertIn("preserve that exact deferred command here", self.skill)
        self.assertIn(
            "fence only the work body and route separate audience/tone obligations only to Constraints",
            self.skill,
        )
        self.assertIn(
            "Exclude absent obligations and generator-only, superseded, or untrusted instructions",
            self.skill,
        )
        self.assertIn(
            "Having no files or repository is not a constraint",
            self.skill,
        )
        self.assertIn(
            "Exclude commands, paths, outcomes, observations, and test counts",
            self.skill,
        )
        self.assertIn(
            "Label drift-prone commands, evidence, references, results, and observations",
            self.skill,
        )
        self.assertIn(
            "Name only resume locations, without their lifecycle or constraints",
            self.skill,
        )
        self.assertIn(
            "A session-only WIP with no supplied body is a resume-location fact",
            self.skill,
        )
        self.assertIn(
            "do not restate its existence elsewhere",
            self.skill,
        )
        self.assertIn(
            "an actual unrecoverable WIP body supplied in the source",
            self.skill,
        )
        self.assertIn(
            "a constraint-named path's obligation stays in Constraints",
            self.skill,
        )
        self.assertIn(
            "preserve one coarse lifecycle sentence; detailed WIP stays in Essential Context",
            self.skill,
        )
        self.assertIn("Action absence is not lifecycle", self.skill)
        self.assertIn("`Reported: status not supplied.`", self.skill)
        self.assertIn("do not infer durability or collect every mentioned path", self.skill)
        self.assertIn("Instructions addressed only to the handoff generator", self.skill)
        self.assertIn("do not infer durability", self.skill)

    def test_repository_registration_is_consistent(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn("./skills/personal/handoff", manifest["skills"])
        readme_row = next(
            line
            for line in README_PATH.read_text(encoding="utf-8").splitlines()
            if line.startswith("| [handoff]")
        )
        self.assertIn("$handoff", readme_row)

    def test_duplicate_skill_name_has_an_explicit_install_policy(self) -> None:
        skills_by_name: dict[str, set[str]] = {}
        for skill_path in (REPO_ROOT / "skills").glob("**/SKILL.md"):
            name = frontmatter(skill_path.read_text(encoding="utf-8"))["name"]
            relative_parent = skill_path.parent.relative_to(REPO_ROOT).as_posix()
            skills_by_name.setdefault(name, set()).add(relative_parent)
        duplicates = {
            name: paths
            for name, paths in skills_by_name.items()
            if len(paths) > 1
        }
        self.assertEqual(duplicates, ALLOWED_DUPLICATE_SKILLS)

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        registered_paths = {
            path.removeprefix("./") for path in manifest["skills"]
        }
        self.assertIn(CANONICAL_HANDOFF_PATH, registered_paths)
        self.assertNotIn(VENDOR_HANDOFF_PATH, registered_paths)
        registered_names = [
            frontmatter(
                (REPO_ROOT / path / "SKILL.md").read_text(encoding="utf-8")
            )["name"]
            for path in registered_paths
        ]
        self.assertEqual(len(registered_names), len(set(registered_names)))

        readme = README_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "npx skills add /path/to/peibin-skills/skills/personal/handoff "
            "--agent codex --copy -y",
            readme,
        )
        self.assertIn("vendor version is retained as reference source", readme)
        readme_zh = README_ZH_PATH.read_text(encoding="utf-8")
        self.assertIn("vendor `handoff` 仅作为参考源码", readme_zh)

    def test_install_evidence_is_sanitized_and_matches_current_sources(self) -> None:
        evidence = json.loads(INSTALL_EVAL_PATH.read_text(encoding="utf-8"))
        self.assertEqual(evidence["schema"], 1)
        self.assertRegex(evidence["skills_cli_version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(evidence["source_path"], CANONICAL_HANDOFF_PATH)
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/private/", serialized)
        self.assertNotIn("token", serialized.lower())
        expected_hashes = {
            path.relative_to(SKILL_ROOT).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in RUNTIME_SKILL_FILES
        }
        self.assertEqual(evidence["installed_hashes"], expected_hashes)
        self.assertEqual(evidence["installed_list"]["name"], "handoff")
        self.assertEqual(
            evidence["installed_list"]["source"], CANONICAL_HANDOFF_PATH
        )
        self.assertEqual(evidence["root_discovery"]["handoff_entries"], 1)
        self.assertFalse(evidence["after_remove"]["skill_directory_exists"])
        self.assertFalse(evidence["after_remove"]["lock_entry_exists"])
        self.assertEqual(evidence["after_remove"]["list"], [])

    def test_schema_version_and_frontmatter_fields_match_validator(self) -> None:
        for profile, template in (
            ("full", self.full_template),
            ("compact", self.compact_template),
        ):
            with self.subTest(profile=profile):
                opening, raw_frontmatter, _ = template.split("---", 2)
                self.assertFalse(opening.strip())
                fields = tuple(
                    line.split(":", 1)[0]
                    for line in raw_frontmatter.strip().splitlines()
                )
                self.assertEqual(fields, validator.FRONTMATTER_FIELDS)
                self.assertIn(
                    f'handoff_schema: "{validator.SCHEMA_VERSION}"', template
                )
                self.assertIn(f'handoff_profile: "{profile}"', template)
        design = DESIGN_PATH.read_text(encoding="utf-8")
        self.assertIn(f'handoff_schema: "{validator.SCHEMA_VERSION}"', design)
        self.assertIn("handoff_profile", design)

    def test_skill_template_headings_match_validator(self) -> None:
        core = tuple(re.findall(r"(?m)^## (.+)$", self.full_template))
        state = tuple(re.findall(r"(?m)^### (.+)$", self.full_template))
        self.assertEqual(core, validator.CORE_HEADINGS)
        self.assertEqual(state[: len(validator.STATE_HEADINGS)], validator.STATE_HEADINGS)
        self.assertEqual(len(state), len(validator.STATE_HEADINGS))

        compact = tuple(re.findall(r"(?m)^## (.+)$", self.compact_template))
        self.assertEqual(compact, validator.COMPACT_HEADINGS)

    def test_skill_declares_the_validators_strict_markdown_subset(self) -> None:
        for rule in (
            "unindented ATX headings",
            "setext headings",
            "block quotes, lists, or indentation",
            "raw HTML heading tags",
            "legal transient-data fence",
        ):
            self.assertIn(rule, self.full_protocol)

    def test_skill_declares_canonical_ownership_guardrails(self) -> None:
        for rule in (
            "`Goal And Definition Of Done` describes only the target outcome",
            "Negative status wording such as `not confirmed` or `unfinished` never supplies a positive goal verb or completion condition",
            "Preserve an explicit handoff target as handoff, not completion",
            "wherever the claim appears",
            "belongs only in `Next Action`",
            "write `None` in `Decisions And Rationale`",
            "one determinate action",
            "action selector only in `Next Action`",
            "payload-specific mandatory detail",
            "A trusted `null` runtime field proves only",
            "An observation interface is not an environment location",
            "Do not summarize payload work",
            "durable WIP location",
            "Questions the successor can investigate",
            "Waiting for a future intermittent event",
            "Generic verbs such as complete",
            "default every claim about a source-session symptom",
            "source-environment access is unavailable",
            "make a private fact ledger",
            "Repeating an object identifier",
            "Use these sparse cases",
            "For a completed goal with no obligation",
            "For session-only WIP whose body was not supplied",
            "For an incomplete verbatim draft",
            "Each coarse lifecycle fact belongs to exactly one Current State subsection",
            "Invocation delivery and persistence instructions govern generation only",
            "never promote an instruction's rejection or untrusted status into a successor constraint",
        ):
            self.assertIn(rule, self.full_protocol)
        self.assertIn(
            "end the payload immediately after the closing fence",
            self.payloads,
        )
        self.assertIn(
            "When Goal already owns the expected behavior",
            self.payloads,
        )
        self.assertIn(
            "When Goal already owns the research question or outcome",
            self.payloads,
        )
        self.assertIn(
            "Never put investigation ordering or a hypothesis boundary in Goal",
            self.payloads,
        )
        self.assertIn(
            "Keep operation identifiers, observation methods, owners, thresholds, and rollback steps out of Goal",
            self.payloads,
        )
        self.assertIn(
            "If the source gives actions but no distinct target outcome, write `None`",
            self.full_protocol,
        )
        self.assertIn("otherwise Goal is `None`", self.payloads)
        self.assertIn(
            "Source-prescribed next action: Follow the action in Continuation Payload > Operations.",
            self.payloads,
        )
        self.assertIn(
            "complete source wording, order, and conditions in one `Action:` item",
            self.payloads,
        )
        self.assertIn(
            "Do not repeat its observation method, trigger, stop, or rollback as other payload items",
            self.payloads,
        )
        self.assertIn(
            "After writing environment in Resume Context, delete it from Operation and Status items",
            self.payloads,
        )
        self.assertIn(
            "`Current State` may say only `Operational follow-up remains.`",
            self.payloads,
        )
        self.assertIn(
            "it must not say an operation started, is active, was observed, or has a status",
            self.payloads,
        )
        self.assertIn(
            "Required missing input details belong only in Open Questions",
            self.full_protocol,
        )

    def test_semantic_section_labels_match_validator(self) -> None:
        for labels in validator.SEMANTIC_SECTION_LABELS.values():
            positions = [self.full_template.index(label) for label in labels]
            self.assertEqual(positions, sorted(positions))

    def test_payload_document_types_match_validator(self) -> None:
        headings = re.findall(r"(?m)^## (.+)$", self.payloads)
        self.assertEqual(tuple(headings[:-1]), validator.PAYLOAD_TYPES)

    def test_skill_references_only_existing_local_resources(self) -> None:
        for document_path, document in (
            (SKILL_PATH, self.skill),
            (FULL_PROTOCOL_PATH, self.full_protocol),
        ):
            references = re.findall(r"\[[^]]+\]\(([^)]+)\)", document)
            for reference in references:
                self.assertTrue(
                    (document_path.parent / reference).is_file(), reference
                )
        for script in ("publish_handoff.py", "validate_handoff.py"):
            self.assertTrue((SKILL_ROOT / "scripts" / script).is_file())

    def test_forward_cases_are_unique_and_auditable(self) -> None:
        fixture = json.loads(FORWARD_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema"], 4)
        self.assertEqual(
            set(fixture["global_invariants"]), GLOBAL_FORWARD_INVARIANTS
        )
        cases = fixture["cases"]
        identifiers = [case["id"] for case in cases]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(set(identifiers), EXPECTED_FORWARD_CASE_IDS)
        executed = 0
        executed_profiles: set[str] = set()
        historical_count = 0
        for case in cases:
            source = case["source_session_fixture"]
            self.assertTrue(source["messages"])
            self.assertTrue(
                all(
                    message["role"] in {"user", "assistant"}
                    and message["content"]
                    for message in source["messages"]
                )
            )
            self.assertEqual(
                set(source["runtime_context"]), {"cwd", "repository"}
            )
            self.assertTrue(case["invocation"].startswith("$handoff"))
            self.assertTrue(case["required_invariants"])
            for historical in case["historical_results"]:
                historical_count += 1
                historical_path = SKILL_ROOT / historical["artifact_path"]
                historical_bytes = historical_path.read_bytes()
                self.assertEqual(
                    hashlib.sha256(historical_bytes).hexdigest(),
                    historical["artifact_sha256"],
                )
                self.assertNotEqual(
                    historical["skill_revision"],
                    f"sha256:{skill_content_sha256()}",
                )
                validator.validate_handoff(historical_bytes)
            result = case["latest_result"]
            self.assertEqual(set(result), FORWARD_RESULT_FIELDS)
            scenario_invariants = set(case["required_invariants"])
            expected_invariants = scenario_invariants | GLOBAL_FORWARD_INVARIANTS
            self.assertEqual(set(result["invariants"]), expected_invariants)
            for observation in result["invariants"].values():
                self.assertIn(
                    observation["status"], {"fail", "pass", "not-run"}
                )
                self.assertTrue(observation["evidence"])
            if result["status"] == "not-run":
                self.assertTrue(
                    all(
                        observation["status"] == "not-run"
                        for observation in result["invariants"].values()
                    )
                )
                continue

            executed += 1
            self.assertIn(result["status"], {"fail", "pass"})
            self.assertRegex(result["run_id"], r"^handoff-eval-\d{8}-\d{3,}$")
            date.fromisoformat(result["date"])
            self.assertTrue(result["model"])
            self.assertRegex(result["skill_revision"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(
                result["skill_revision"],
                f"sha256:{skill_content_sha256()}",
            )
            self.assertRegex(result["artifact_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(result["validator_result"], {"fail", "pass"})
            self.assertTrue(result["evaluator"])
            self.assertTrue(result["notes"])
            artifact_path = SKILL_ROOT / result["artifact_path"]
            artifact_bytes = artifact_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(artifact_bytes).hexdigest(),
                result["artifact_sha256"],
            )
            if result["validator_result"] == "pass":
                validator.validate_handoff(artifact_bytes)
            else:
                with self.assertRaises(validator.HandoffValidationError):
                    validator.validate_handoff(artifact_bytes)

            artifact_metadata = frontmatter(artifact_bytes.decode("utf-8"))
            self.assertEqual(result["artifact_schema"], artifact_metadata["handoff_schema"])
            expected_profile = (
                artifact_metadata["handoff_profile"]
                if result["artifact_schema"] == validator.SCHEMA_VERSION
                else "legacy"
            )
            self.assertEqual(result["artifact_profile"], expected_profile)
            self.assertEqual(result["artifact_schema"], validator.SCHEMA_VERSION)
            self.assertIn(result["artifact_profile"], validator.PROFILES)
            self.assertEqual(
                "--compact" in case["invocation"],
                result["artifact_profile"] == "compact",
            )
            executed_profiles.add(result["artifact_profile"])

            observations = result["invariants"]
            global_failed = any(
                observations[name]["status"] == "fail"
                for name in GLOBAL_FORWARD_INVARIANTS
            )
            if global_failed or result["validator_result"] == "fail":
                self.assertEqual(result["status"], "fail")
            if result["status"] == "pass":
                self.assertEqual(result["validator_result"], "pass")
                self.assertTrue(
                    all(
                        observation["status"] == "pass"
                        for observation in observations.values()
                    )
                )

        self.assertEqual(executed, len(cases))
        self.assertEqual(executed_profiles, set(validator.PROFILES))
        self.assertEqual(historical_count, 39)


if __name__ == "__main__":
    unittest.main()
