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
RESILIENCE_PATH = SKILL_ROOT / "references" / "resilience-and-lenses.md"
QUALITY_GATES_PATH = SKILL_ROOT / "references" / "quality-gates.md"
OPENAI_PATH = SKILL_ROOT / "agents" / "openai.yaml"
EVAL_CASES_PATH = Path(__file__).with_name("eval_cases.json")
MANIFEST_PATH = REPO_ROOT / ".claude-plugin" / "plugin.json"
README_PATH = REPO_ROOT / "README.md"
README_ZH_PATH = REPO_ROOT / "README.zh-CN.md"
REPO_VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_repo.py"
MAKEFILE_PATH = REPO_ROOT / "Makefile"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CHECK_LAUNCHER_PATH = SKILL_ROOT / "scripts" / "run_release_check.py"
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
                "references/resilience-and-lenses.md",
            },
        )
        for path in (RISK_PATH, SCOPE_PATH, CONTRACTS_PATH, RESILIENCE_PATH):
            self.assertTrue(path.is_file())
        self.assertTrue(QUALITY_GATES_PATH.is_file())
        self.assertIn("quality-gates.md", CONTRACTS_PATH.read_text(encoding="utf-8"))

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
        """Validate the reusable executable-input catalog contract."""
        payload = json.loads(EVAL_CASES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"version", "skill", "purpose", "cases"})
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["skill"], "code-review")
        self.assertEqual(
            payload["purpose"],
            "Executable v1 compatibility inputs for controlled behavioral evaluation.",
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
                {
                    "id",
                    "request",
                    "context",
                    "expected",
                    "forbidden",
                    "covers",
                    "executable",
                },
            )
            self.assertEqual(case["executable"], {"enabled": True})
            self.assertTrue(case["request"])
            self.assertGreaterEqual(len(case["context"]), 2)
            self.assertTrue(all(isinstance(fact, str) and fact for fact in case["context"][:-1]))
            self.assertEqual(set(case["context"][-1]), {"fixture"})
            self.assertEqual(case["context"][-1]["fixture"]["kind"], case["id"])
            controlled_inputs = case["context"][-1]["fixture"]["controlled_inputs"]
            self.assertEqual(
                set(controlled_inputs),
                {
                    "candidate_findings",
                    "candidate_decisions",
                    "proposed_checks",
                    "validation_results",
                    "safety_attempts",
                    "context_paths",
                    "trusted_requirement_sources",
                    "trusted_validation_commands",
                },
            )
            self.assertEqual(
                set(case["expected"]),
                {"decisions", "report", "observations", "bindings"},
            )
            self.assertEqual(
                set(case["forbidden"]), {"behaviors", "observations", "bindings"}
            )
            self.assertTrue(case["expected"]["decisions"])
            self.assertTrue(case["expected"]["report"])
            self.assertTrue(case["forbidden"]["behaviors"])
            for observation in (
                case["expected"]["observations"]
                + case["forbidden"]["observations"]
            ):
                self.assertEqual(
                    set(observation), {"source", "path", "operator", "value"}
                )
                self.assertIn(observation["source"], {"process", "output", "fixture"})
                self.assertIn(
                    observation["operator"],
                    {"equals", "contains", "contains_subset"},
                )
            self.assertEqual(
                set(case["expected"]["bindings"]), {"decisions", "report"}
            )
            self.assertEqual(
                set(case["forbidden"]["bindings"]), {"behaviors"}
            )
            for section, statement_key in (
                (case["expected"], "decisions"),
                (case["expected"], "report"),
                (case["forbidden"], "behaviors"),
            ):
                bindings = section["bindings"][statement_key]
                self.assertEqual(len(bindings), len(section[statement_key]))
                self.assertTrue(all(binding for binding in bindings))
                self.assertTrue(
                    all(
                        isinstance(index, int)
                        and not isinstance(index, bool)
                        and 0 <= index < len(section["observations"])
                        for binding in bindings
                        for index in binding
                    )
                )
            for section in (case["expected"], case["forbidden"]):
                self.assertEqual(
                    {
                        index
                        for bindings in section["bindings"].values()
                        for binding in bindings
                        for index in binding
                    },
                    set(range(len(section["observations"]))),
                )
            self.assertTrue(case["covers"])
            self.assertTrue(set(case["covers"]) <= required_coverage)
            observed.update(case["covers"])
        self.assertEqual(observed, required_coverage)

    def test_standard_check_runs_the_bound_independent_release_gate(self) -> None:
        makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
        quality_gates = QUALITY_GATES_PATH.read_text(encoding="utf-8")
        readme = README_PATH.read_text(encoding="utf-8")
        standard_command = (
            "uv run --isolated --no-project --no-config --no-env-file "
            "--with PyYAML==6.0.2 -- python3 -I "
            "skills/personal/code-review/scripts/run_release_check.py"
        )
        self.assertIn(standard_command, quality_gates)
        self.assertIn(standard_command, readme)
        self.assertIn("POSIX", quality_gates)
        self.assertIn("POSIX", readme)
        self.assertNotIn("uv run --with PyYAML==6.0.2 make check", quality_gates)
        ci = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
        steps = ci["jobs"]["check"]["steps"]
        uses = [step["uses"] for step in steps if "uses" in step]
        run_commands = [step["run"] for step in steps if "run" in step]
        self.assertTrue(any(value.startswith("astral-sh/setup-uv@") for value in uses))
        self.assertIn(standard_command, run_commands)
        self.assertNotIn("make check", run_commands)
        self.assertTrue(CHECK_LAUNCHER_PATH.is_file())
        launcher = CHECK_LAUNCHER_PATH.read_text(encoding="utf-8")
        for variable in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES"):
            self.assertIn(f'"{variable}"', launcher)
            self.assertIn(f'`{variable}`', quality_gates)
        self.assertIn(
            "check: check-environment check-interpreter validate test release-gate",
            makefile,
        )
        self.assertIn("checks require the documented release-check launcher", makefile)
        self.assertIn("review_workflow.py grade", makefile)
        self.assertIn("review_workflow.py gate", makefile)


if __name__ == "__main__":
    unittest.main()
