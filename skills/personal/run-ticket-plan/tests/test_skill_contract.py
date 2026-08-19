from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
SKILL_PATH = SKILL_ROOT / "SKILL.md"
OPENAI_PATH = SKILL_ROOT / "agents" / "openai.yaml"
README_PATH = REPO_ROOT / "README.md"
EVAL_CASES_PATH = Path(__file__).with_name("skill_eval_cases.json")
RESULT_CONTRACT_PATH = SKILL_ROOT / "references" / "result-contract.md"
SCRIPT = SKILL_ROOT / "scripts" / "ticketctl.py"

SPEC = importlib.util.spec_from_file_location("ticketctl_skill_contract", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ticketctl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ticketctl
SPEC.loader.exec_module(ticketctl)


def _frontmatter(text: str) -> dict[str, str]:
    opening, raw, _ = text.split("---", 2)
    if opening.strip():
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    return {
        key.strip(): value.strip()
        for line in raw.strip().splitlines()
        for key, value in [line.split(":", 1)]
    }


def _command_parsers() -> dict[str, argparse.ArgumentParser]:
    parser = ticketctl._parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers.choices


def _required_options(command: str) -> set[str]:
    return {
        action.dest
        for action in _command_parsers()[command]._actions
        if getattr(action, "required", False)
    }


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")
        cls.openai = OPENAI_PATH.read_text(encoding="utf-8")

    def test_cross_runtime_invocation_is_explicit_only(self) -> None:
        self.assertEqual(_frontmatter(self.skill)["disable-model-invocation"], "true")
        self.assertRegex(
            self.openai,
            r"(?m)^policy:\n  allow_implicit_invocation: false$",
        )

    def test_default_prompt_uses_a_public_primary_mode(self) -> None:
        primary = self._primary_modes()
        match = re.search(
            r'(?m)^  default_prompt: "Use \$run-ticket-plan ([a-z-]+)\b',
            self.openai,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertIn(match.group(1), primary)

    def test_omitted_mode_maps_to_status(self) -> None:
        self.assertRegex(
            self.skill,
            r"(?i)explicit invocation omits the mode[^.\n]*run `status`",
        )

    def test_readme_preserves_the_product_implementation_boundary(self) -> None:
        row = next(
            line
            for line in README_PATH.read_text(encoding="utf-8").splitlines()
            if line.startswith("| [run-ticket-plan]")
        )
        self.assertIn("不直接实现产品代码", row)

    def test_primary_workflow_is_status_next_confirm(self) -> None:
        self.assertEqual(self._primary_modes(), ["status", "next", "confirm"])

    def test_record_external_signature_matches_required_parser_inputs(self) -> None:
        signature = self._administrative_signature("record-external")
        tokens = shlex.split(signature)
        self.assertIn("<ticket>", tokens)
        self.assertIn("<check>", tokens)
        self.assertIn("--result", tokens)
        self.assertEqual(tokens[tokens.index("--result") + 1], "PASS")
        self.assertIn("--reference", tokens)
        self.assertIn("--issuer", tokens)
        self.assertEqual(
            _required_options("record-external"),
            {"feature_root", "ticket", "check", "result", "reference", "issuer"},
        )

    def test_reconcile_public_contract_requires_operator_reason(self) -> None:
        signature = self._administrative_signature("reconcile")
        self.assertIn("--reason <operator-reason>", signature)
        contract = next(
            paragraph
            for paragraph in self.skill.split("\n\n")
            if paragraph.startswith("For `reconcile`,")
        )
        self.assertIn("non-empty reason supplied by the operator", contract)
        self.assertIn("never author or substitute the reason", contract)
        self.assertIn("end this administrative action", contract)
        self.assertEqual(_required_options("reconcile"), {"feature_root", "ticket"})

    def test_reconcile_reference_preserves_cli_compatibility_and_public_safety(
        self,
    ) -> None:
        contract = RESULT_CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("low-level compatibility interface", contract)
        self.assertIn("accept an omitted `--reason`", contract)
        self.assertIn("public Skill action requires a non-empty operator-supplied reason", contract)
        self.assertIn("controller's `next` value as the recovery handoff", contract)

    def test_removed_mode_migration_has_one_authoritative_definition(self) -> None:
        self.assertEqual(self.skill.count("`run`"), 1)
        self.assertEqual(self.skill.count("`continue`"), 1)
        migration = next(
            paragraph
            for paragraph in self.skill.split("\n\n")
            if paragraph.startswith("Return a concise migration message")
        )
        for replacement in ("`status`", "`next`", "`confirm`"):
            self.assertIn(replacement, migration)

    def test_referenced_controller_commands_exist(self) -> None:
        referenced = set(re.findall(r"ticketctl\.py ([a-z][a-z-]+)", self.skill))
        self.assertTrue(referenced)
        self.assertEqual(referenced - set(_command_parsers()), set())

    def test_local_eval_cases_are_structured_and_complete(self) -> None:
        payload = json.loads(EVAL_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["skill"], "run-ticket-plan")
        cases = payload["cases"]
        expected_ids = {
            "explicit-without-mode-defaults-to-status",
            "ambiguous-feature-root-stops",
            "blocking-requirement-without-safe-check-stops",
            "next-stops-after-work-packet",
            "record-external-missing-evidence-input-stops",
            "reconcile-missing-reason-stops",
            "reconcile-success-returns-handoff-and-stops",
            "confirm-remediate-does-not-edit-product",
        }
        self.assertEqual({case["id"] for case in cases}, expected_ids)
        commands = set(_command_parsers())
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertIsInstance(case["input"], str)
                self.assertTrue(case["input"].strip())
                self.assertTrue(case["expected"]["outcome"])
                self.assertIsInstance(case["expected"]["tool_calls"], list)
                self.assertIsInstance(case["forbidden"]["tool_calls"], list)
                self.assertIsInstance(case["forbidden"]["behaviors"], list)
                self.assertTrue(case["forbidden"]["behaviors"])
                for call in (
                    case["expected"]["tool_calls"]
                    + case["forbidden"]["tool_calls"]
                ):
                    self.assertIn(call["command"], commands)
                    if "count" in call:
                        self.assertGreater(call["count"], 0)

    @classmethod
    def _primary_modes(cls) -> list[str]:
        block = cls.skill.split(
            "Keep these explicit administrative actions", 1
        )[0].split("Map the primary workflow to one mode:", 1)[1]
        return re.findall(r"(?m)^- `([a-z-]+)(?: [^`]*)?`:", block)

    @classmethod
    def _administrative_signature(cls, command: str) -> str:
        match = re.search(
            rf"(?m)^- `({re.escape(command)} [^`]+)`:", cls.skill
        )
        if match is None:
            raise AssertionError(f"missing public signature for {command}")
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
