from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
SKILL_PATH = SKILL_ROOT / "SKILL.md"
RISK_PATH = SKILL_ROOT / "references" / "risk-review.md"
SCOPE_PATH = SKILL_ROOT / "references" / "scope-resolution.md"
CONTRACTS_PATH = SKILL_ROOT / "references" / "contracts-v2.md"
OPENAI_PATH = SKILL_ROOT / "agents" / "openai.yaml"
EVAL_CASES_PATH = Path(__file__).with_name("eval_cases.json")
MANIFEST_PATH = REPO_ROOT / ".claude-plugin" / "plugin.json"
README_PATH = REPO_ROOT / "README.md"
README_ZH_PATH = REPO_ROOT / "README.zh-CN.md"
REPO_VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_repo.py"
CANONICAL_PATH = "skills/personal/code-review"
VENDOR_PATH = "skills/vendor/mattpocock/engineering/code-review"

SPEC = importlib.util.spec_from_file_location(
    "code_review_repo_validator", REPO_VALIDATOR_PATH
)
assert SPEC is not None and SPEC.loader is not None
repo_validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repo_validator
SPEC.loader.exec_module(repo_validator)


def _frontmatter(path: Path) -> dict[str, object]:
    match = re.match(
        r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)",
        path.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"{path} has no YAML frontmatter")
    value = yaml.safe_load(match.group(1))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} frontmatter is not an object")
    return value


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")

    def test_metadata_and_progressive_disclosure_are_valid(self) -> None:
        metadata = _frontmatter(SKILL_PATH)
        self.assertEqual(metadata["name"], "code-review")
        self.assertIn("local changes", str(metadata["description"]))
        self.assertIn("pull requests", str(metadata["description"]))

        agent = yaml.safe_load(OPENAI_PATH.read_text(encoding="utf-8"))
        interface = agent["interface"]
        self.assertEqual(interface["display_name"], "Code Review")
        self.assertIn("$code-review", interface["default_prompt"])
        self.assertTrue(25 <= len(interface["short_description"]) <= 64)

        references = set(re.findall(r"references/[a-z0-9-]+\.md", self.skill))
        self.assertEqual(
            references,
            {
                "references/risk-review.md",
                "references/scope-resolution.md",
                "references/contracts-v2.md",
            },
        )
        for path in (RISK_PATH, SCOPE_PATH, CONTRACTS_PATH):
            self.assertTrue(path.is_file())

    def test_plugin_registers_only_the_canonical_copy(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        registered_paths = [path.removeprefix("./") for path in manifest["skills"]]
        self.assertEqual(registered_paths.count(CANONICAL_PATH), 1)
        self.assertNotIn(VENDOR_PATH, registered_paths)
        self.assertEqual(
            _frontmatter(REPO_ROOT / VENDOR_PATH / "SKILL.md")["name"],
            "code-review",
        )

        registered_names = [
            _frontmatter(REPO_ROOT / path / "SKILL.md")["name"]
            for path in registered_paths
        ]
        self.assertEqual(len(registered_names), len(set(registered_names)))

    def test_duplicate_registered_name_validator_detects_collisions(self) -> None:
        grouped = {
            "alpha": ["./skills/personal/alpha"],
            "code-review": [
                "./skills/personal/code-review",
                "./skills/vendor/example/code-review",
            ],
        }
        self.assertEqual(
            repo_validator._duplicate_registered_skill_names(grouped),
            {"code-review": grouped["code-review"]},
        )
        self.assertEqual(
            repo_validator._duplicate_registered_skill_names(
                {"code-review": ["./skills/personal/code-review"]}
            ),
            {},
        )

    def test_readmes_define_unambiguous_installation(self) -> None:
        install_command = (
            "npx skills add /path/to/peibin-skills/skills/personal/code-review "
            "--agent codex --copy -y"
        )
        for path in (README_PATH, README_ZH_PATH):
            text = path.read_text(encoding="utf-8")
            self.assertIn(install_command, text)
            root_installs = [
                line
                for line in text.splitlines()
                if line.startswith("npx skills add ") and "--full-depth" in line
            ]
            self.assertTrue(root_installs)
            self.assertTrue(
                all(
                    "code-review" not in line.split("--skill", 1)[-1].split()
                    for line in root_installs
                )
            )
        readme = README_PATH.read_text(encoding="utf-8")
        readme_zh = README_ZH_PATH.read_text(encoding="utf-8")
        self.assertIn("personal versions are canonical", readme)
        self.assertIn("vendor versions are retained", readme)
        self.assertIn("personal 版本是 canonical", readme_zh)
        self.assertIn("vendor 版本仅作为参考源码", readme_zh)

    def test_eval_catalog_schema_and_independent_evaluation_coverage(self) -> None:
        """Validate reusable inputs only; this test does not execute their behavior."""
        payload = json.loads(EVAL_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"version", "skill", "purpose", "cases"})
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["skill"], "code-review")
        self.assertEqual(
            payload["purpose"],
            "Reusable unevaluated inputs for independent behavioral evaluation.",
        )
        cases = payload["cases"]
        case_ids = [case["id"] for case in cases]
        self.assertEqual(len(case_ids), len(set(case_ids)))

        required_coverage = {
            "working-tree",
            "root-commit",
            "merge-commit",
            "incomplete-requirement",
            "tooling-failure",
            "paired-review",
            "deletion",
            "untrusted-command",
            "secret-symlink",
            "separate-phase",
            "explicit-range",
            "branch-merge-base",
            "explicit-pr-read",
            "generic-network-lookup",
            "severity-calibration",
        }
        observed: set[str] = set()
        for case in cases:
            self.assertEqual(
                set(case),
                {"id", "request", "context", "expected", "forbidden", "covers"},
            )
            self.assertTrue(case["request"])
            self.assertTrue(case["context"])
            self.assertTrue(case["expected"]["decisions"])
            self.assertTrue(case["expected"]["report"])
            self.assertTrue(case["forbidden"]["behaviors"])
            self.assertTrue(case["covers"])
            self.assertTrue(set(case["covers"]) <= required_coverage)
            observed.update(case["covers"])
        self.assertEqual(observed, required_coverage)


if __name__ == "__main__":
    unittest.main()
