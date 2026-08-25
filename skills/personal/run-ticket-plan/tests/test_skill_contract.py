from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[1]
SKILL_PATH = SKILL_ROOT / "SKILL.md"
OPENAI_PATH = SKILL_ROOT / "agents" / "openai.yaml"
EVAL_CASES_PATH = Path(__file__).with_name("skill_eval_cases.json")
RESULT_CONTRACT_PATH = SKILL_ROOT / "references" / "result-contract.md"
RECOVERY_CONTRACT_PATH = SKILL_ROOT / "references" / "recovery-contract.md"
OUTPUT_CONTRACT_PATH = SKILL_ROOT / "references" / "output-contract.md"
CONTRACT_PATHS = (
    OUTPUT_CONTRACT_PATH,
    RESULT_CONTRACT_PATH,
    RECOVERY_CONTRACT_PATH,
)
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


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")
        cls.openai = OPENAI_PATH.read_text(encoding="utf-8")

    def test_metadata_keeps_explicit_invocation(self) -> None:
        frontmatter = _frontmatter(self.skill)
        self.assertEqual(frontmatter["name"], "run-ticket-plan")
        self.assertEqual(frontmatter["disable-model-invocation"], "true")
        self.assertIn("allow_implicit_invocation: false", self.openai)

    def test_command_surface_matches_the_skill(self) -> None:
        mode_block = self.skill.split("## Modes", 1)[1].split("## Confirm", 1)[0]
        modes = re.findall(r"(?m)^- `([a-z]+)", mode_block)
        self.assertEqual(modes, ["status", "next", "confirm", "pass", "pause"])
        commands = _command_parsers()
        required_by_command = {
            name: {
                action.dest
                for action in parser._actions
                if getattr(action, "required", False)
            }
            for name, parser in commands.items()
        }
        self.assertEqual(
            required_by_command,
            {
                "status": {"feature_root"},
                "next": {"feature_root"},
                "pause": {"feature_root", "reason"},
                "pass": {"feature_root"},
                "prepare-confirm": {"feature_root"},
                "confirm": {"feature_root", "result_file"},
            },
        )

    def test_references_define_one_versioned_contract_surface(self) -> None:
        links = set(re.findall(r"references/[a-z-]+\.md", self.skill))
        self.assertEqual(
            links,
            {
                "references/output-contract.md",
                "references/result-contract.md",
                "references/recovery-contract.md",
            },
        )
        contract = OUTPUT_CONTRACT_PATH.read_text(encoding="utf-8")
        for field in ticketctl._response("status", "READY"):
            self.assertIn(f'"{field}"', contract)
        self.assertEqual(ticketctl.OUTPUT_SCHEMA_VERSION, 1)

    def test_instruction_context_stays_within_budget(self) -> None:
        references = [path.read_text(encoding="utf-8") for path in CONTRACT_PATHS]
        self.assertLessEqual(len(self.skill.splitlines()), 40)
        self.assertLessEqual(len(" ".join([self.skill, *references]).split()), 1_400)

    def test_eval_cases_cover_each_public_mode(self) -> None:
        payload = json.loads(EVAL_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["skill"], "run-ticket-plan")
        cases = payload["cases"]
        commands = set(_command_parsers())
        observed: set[str] = set()
        for case in cases:
            self.assertTrue(case["expected"]["outcome"])
            for call in case["expected"]["tool_calls"]:
                self.assertIn(call["command"], commands)
                observed.add(call["command"])
            self.assertTrue(case["forbidden"]["behaviors"])
        self.assertTrue({"status", "next", "confirm", "pass", "pause"} <= observed)


if __name__ == "__main__":
    unittest.main()
