from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).parents[1]
CONTRACT_CLI = SKILL_ROOT / "scripts" / "review_contract.py"
SCOPE_CLI = SKILL_ROOT / "scripts" / "review_scope.py"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import review_scope


def _run_contract(command: str, payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), command],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _resolve_local(
    repo: Path, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCOPE_CLI), "local", "--repo", str(repo)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def _reviewer_results(bundle: dict[str, object]) -> list[dict[str, object]]:
    return [
        {
            "schema_version": "2.0",
            "task_id": envelope["task_id"],
            "scope_id": envelope["scope_id"],
            "unit_id": envelope["unit_id"],
            "role": envelope["role"],
            "status": "completed",
            "inspected_record_ids": envelope["changed_record_ids"],
            "candidate_findings": [],
            "supporting_evidence": [],
            "proposed_checks": [],
            "unresolved_assumptions": [],
        }
        for envelope in bundle["reviewer_task_envelopes"]
    ]


def _init_repo(repo: Path, path: str, content: str) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Review Test")
    _git(repo, "config", "user.email", "review@example.com")
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "base")


def _record(record_id: str, path: str, line: int) -> dict[str, object]:
    return {
        "record_id": record_id,
        "source": "commit",
        "change_type": "text",
        "status": "M",
        "anchor": {"kind": "line", "side": "new", "path": path, "line": line},
    }


def _scope(*records: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "scope_id": "commit:0123456789abcdef",
        "target": {"kind": "commit"},
        "changed_records": list(records),
        "exclusions": [],
        "trusted_requirement_sources": [],
        "candidate_validation_commands": [],
    }


def _feature(feature_id: str, name: str, unit_ids: list[str]) -> dict[str, object]:
    return {
        "feature_id": feature_id,
        "name": name,
        "purpose": f"Deliver {name.lower()} behavior",
        "review_unit_ids": unit_ids,
        "child_feature_ids": [],
    }


def _unit(
    unit_id: str,
    feature_id: str,
    records: list[str],
    *,
    context: list[str] | None = None,
    estimated_context_tokens: int = 1_000,
) -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "feature_id": feature_id,
        "kind": "feature",
        "name": unit_id,
        "purpose": f"Review {unit_id}",
        "changed_record_ids": records,
        "context_record_ids": context or [],
        "estimated_context_tokens": estimated_context_tokens,
        "executable": True,
    }


def _integration_unit(
    unit_id: str,
    records: list[str],
    participating_unit_ids: list[str],
) -> dict[str, object]:
    return {
        "unit_id": unit_id,
        "kind": "integration",
        "name": "Identity-to-billing contract",
        "purpose": "Review tenant identity propagation into billing",
        "changed_record_ids": records,
        "context_record_ids": [],
        "estimated_context_tokens": 1_000,
        "executable": True,
        "integration_criteria": {
            "participating_unit_ids": participating_unit_ids,
            "material_contract": "Tenant identity propagation",
            "cannot_be_validated_end_to_end_by_participant": True,
            "material_impact": "security",
        },
    }


def _flat_graph(
    scope: dict[str, object], count: int
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "scope_id": scope["scope_id"],
        "features": [
            _feature(f"feature-{index}", f"Feature {index}", [f"unit-{index}"])
            for index in range(1, count + 1)
        ],
        "review_units": [
            _unit(
                f"unit-{index}",
                f"feature-{index}",
                [f"record-{index}"],
            )
            for index in range(1, count + 1)
        ],
        "ownership": {
            f"record-{index}": f"unit-{index}" for index in range(1, count + 1)
        },
        "edges": [],
    }


class V2FeatureGraphOrchestrationTest(unittest.TestCase):
    def test_same_major_localized_graph_without_unit_purpose_remains_valid(self) -> None:
        scope = _scope(_record("record-legacy", "legacy.py", 1))
        built = subprocess.run(
            [
                sys.executable,
                str(CONTRACT_CLI),
                "build-one-unit",
                "--unit-id",
                "unit-legacy",
                "--feature-name",
                "Legacy",
            ],
            input=json.dumps(scope),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        graph = json.loads(built.stdout)["feature_graph"]
        del graph["review_units"][0]["purpose"]

        result = subprocess.run(
            [
                sys.executable,
                str(CONTRACT_CLI),
                "validate",
                "--kind",
                "feature_graph",
            ],
            input=json.dumps(graph),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_scope_hunks_in_one_file_can_have_different_feature_owners(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            initial = "".join(
                f"VALUE_{line} = {line}\n" for line in range(1, 101)
            )
            _init_repo(repo, "account.py", initial)
            source = repo / "account.py"
            changed_lines = [
                f"VALUE_{line} = {line}\n" for line in range(1, 101)
            ]
            changed_lines[9] = "SIGN_IN_POLICY = 'strict'\n"
            changed_lines[89] = "PROFILE_VISIBILITY = 'private'\n"
            source.write_text("".join(changed_lines), encoding="utf-8")

            scope_result = subprocess.run(
                [
                    sys.executable,
                    str(SCOPE_CLI),
                    "local",
                    "--repo",
                    str(repo),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(scope_result.returncode, 0, scope_result.stderr)
            scope = json.loads(scope_result.stdout)
            shared_records = [
                record
                for record in scope["changed_records"]
                if record.get("new_path") == "account.py"
            ]
            self.assertEqual(len(shared_records), 2)
            self.assertEqual(
                {record["anchor"]["line"] for record in shared_records},
                {10, 90},
            )
            self.assertEqual(
                {record["region"]["kind"] for record in shared_records},
                {"diff_hunk"},
            )

            auth_record, profile_record = sorted(
                shared_records, key=lambda record: record["anchor"]["line"]
            )
            graph = {
                "schema_version": "2.0",
                "scope_id": scope["scope_id"],
                "features": [
                    _feature("feature-auth", "Authentication", ["unit-auth"]),
                    _feature("feature-profile", "Profile", ["unit-profile"]),
                ],
                "review_units": [
                    _unit(
                        "unit-auth",
                        "feature-auth",
                        [auth_record["record_id"]],
                    ),
                    _unit(
                        "unit-profile",
                        "feature-profile",
                        [profile_record["record_id"]],
                    ),
                ],
                "ownership": {
                    auth_record["record_id"]: "unit-auth",
                    profile_record["record_id"]: "unit-profile",
                },
                "edges": [],
            }
            graph_result = _run_contract(
                "build-graph",
                {
                    "scope_manifest": scope,
                    "feature_graph": graph,
                    "environment": {
                        "available_agent_slots": 5,
                        "usable_context_tokens": 10_000,
                    },
                },
            )
            self.assertEqual(graph_result.returncode, 0, graph_result.stderr)
            bundle = json.loads(graph_result.stdout)
            self.assertEqual(
                bundle["feature_graph"]["ownership"], graph["ownership"]
            )

    def test_embedded_unicode_separator_cannot_inject_a_hunk_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _init_repo(repo, "payload.txt", "safe\n")
            (repo / "payload.txt").write_text(
                "changed\u2028@@ -40 +50 @@\n", encoding="utf-8"
            )

            result = _resolve_local(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(result.stdout)["changed_records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["region"]["new_start"], 1)

    def test_edited_rename_uses_real_hunks_and_side_specific_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            initial_lines = [f"line {line}\n" for line in range(1, 101)]
            _init_repo(repo, "old.txt", "".join(initial_lines))
            _git(repo, "mv", "old.txt", "new.txt")
            changed_lines = initial_lines.copy()
            del changed_lines[9]
            changed_lines.insert(88, "inserted behavior\n")
            (repo / "new.txt").write_text("".join(changed_lines), encoding="utf-8")
            _git(repo, "add", "new.txt")

            result = _resolve_local(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(result.stdout)["changed_records"]
            self.assertEqual(len(records), 2)
            self.assertEqual(
                {(record["anchor"]["side"], record["anchor"]["path"]) for record in records},
                {("old", "old.txt"), ("new", "new.txt")},
            )
            self.assertTrue(all(record["status"] == "R" for record in records))

    def test_hunk_records_ignore_ambient_git_diff_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            initial_lines = [f"line {line}\n" for line in range(1, 101)]
            _init_repo(repo, "configured.txt", "".join(initial_lines))
            changed_lines = initial_lines.copy()
            changed_lines[9] = "first change\n"
            changed_lines[89] = "second change\n"
            (repo / "configured.txt").write_text(
                "".join(changed_lines), encoding="utf-8"
            )
            baseline = _resolve_local(repo)
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            _git(repo, "config", "color.ui", "always")
            _git(repo, "config", "diff.interHunkContext", "10000")
            _git(repo, "config", "diff.algorithm", "histogram")
            _git(repo, "config", "diff.indentHeuristic", "true")
            _git(repo, "config", "diff.renames", "false")

            configured = _resolve_local(repo)

            self.assertEqual(configured.returncode, 0, configured.stderr)
            self.assertEqual(json.loads(configured.stdout), json.loads(baseline.stdout))

    def test_canonical_patch_declares_all_output_indicators(self) -> None:
        with mock.patch.object(
            review_scope,
            "_output_indicator_options",
            return_value=list(review_scope.OUTPUT_INDICATOR_OPTIONS),
        ), mock.patch.object(review_scope, "_git", return_value=b"") as git:
            review_scope._diff_bytes(
                Path("/trusted/repository"),
                [],
                ["configured.txt"],
                patch=True,
            )

        self.assertTrue(
            set(review_scope.OUTPUT_INDICATOR_OPTIONS).issubset(git.call_args.args)
        )

    def test_hunk_records_ignore_ambient_rename_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _git(repo, "init", "-q")
            _git(repo, "config", "user.name", "Review Test")
            _git(repo, "config", "user.email", "review@example.com")
            for index in range(1, 5):
                content = [f"file {index}\n"] + [
                    f"shared line {line}\n" for line in range(1, 101)
                ]
                (repo / f"old-{index}.txt").write_text(
                    "".join(content), encoding="utf-8"
                )
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "base")
            for index in range(1, 5):
                _git(repo, "mv", f"old-{index}.txt", f"new-{index}.txt")
                path = repo / f"new-{index}.txt"
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                lines[50] = f"edited line for file {index}\n"
                path.write_text("".join(lines), encoding="utf-8")
            _git(repo, "add", "-A")
            baseline = _resolve_local(repo)
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            _git(repo, "config", "diff.renameLimit", "1")

            configured = _resolve_local(repo)

            self.assertEqual(configured.returncode, 0, configured.stderr)
            self.assertEqual(json.loads(configured.stdout), json.loads(baseline.stdout))

    def test_changed_lines_beginning_with_double_signs_keep_line_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            initial_lines = [f"line {line}\n" for line in range(1, 21)]
            initial_lines[9] = "--old flag\n"
            _init_repo(repo, "prefixes.txt", "".join(initial_lines))
            changed_lines = initial_lines.copy()
            changed_lines[9] = "++new counter\n"
            (repo / "prefixes.txt").write_text(
                "".join(changed_lines), encoding="utf-8"
            )

            result = _resolve_local(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            records = json.loads(result.stdout)["changed_records"]
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0]["anchor"],
                {"kind": "line", "side": "new", "path": "prefixes.txt", "line": 10},
            )

    def test_multi_feature_shared_file_has_unique_owners_and_paired_envelopes(self) -> None:
        scope = _scope(
            _record("record-signin", "account.py", 10),
            _record("record-profile", "account.py", 80),
            _record("record-email", "mailer.py", 12),
        )
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [
                _feature("feature-auth", "Authentication", ["unit-auth"]),
                _feature("feature-profile", "Profile", ["unit-profile"]),
            ],
            "review_units": [
                _unit(
                    "unit-auth",
                    "feature-auth",
                    ["record-signin"],
                    context=["record-email"],
                ),
                _unit(
                    "unit-profile",
                    "feature-profile",
                    ["record-profile", "record-email"],
                    context=["record-signin"],
                ),
            ],
            "ownership": {
                "record-signin": "unit-auth",
                "record-profile": "unit-profile",
                "record-email": "unit-profile",
            },
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 4,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        self.assertEqual(bundle["feature_graph"]["ownership"], graph["ownership"])
        self.assertEqual(len(bundle["reviewer_task_envelopes"]), 4)
        self.assertEqual(
            {
                (envelope["unit_id"], envelope["role"])
                for envelope in bundle["reviewer_task_envelopes"]
            },
            {
                ("unit-auth", "internal_behavior"),
                ("unit-auth", "external_contract"),
                ("unit-profile", "internal_behavior"),
                ("unit-profile", "external_contract"),
            },
        )
        auth_envelopes = [
            envelope
            for envelope in bundle["reviewer_task_envelopes"]
            if envelope["unit_id"] == "unit-auth"
        ]
        self.assertEqual(
            {tuple(envelope["changed_record_ids"]) for envelope in auth_envelopes},
            {("record-signin",)},
        )
        self.assertEqual(
            {tuple(envelope["context_record_ids"]) for envelope in auth_envelopes},
            {("record-email",)},
        )
        self.assertEqual(
            {entry["primary_owner"] for entry in bundle["coverage_ledger"]["records"]},
            {"unit-auth", "unit-profile"},
        )

    def test_complete_graph_accepts_every_current_paired_result(self) -> None:
        scope = _scope(
            _record("record-1", "auth.py", 1),
            _record("record-2", "profile.py", 1),
        )
        built = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": _flat_graph(scope, 2),
                "environment": {"available_agent_slots": 5},
            },
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        bundle = json.loads(built.stdout)

        completed = _run_contract(
            "complete-graph",
            {
                "bundle": bundle,
                "reviewer_results": _reviewer_results(bundle),
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        ledger = json.loads(completed.stdout)["coverage_ledger"]
        self.assertTrue(ledger["complete"])
        self.assertTrue(all(record["covered"] for record in ledger["records"]))

    def test_complete_graph_rejects_results_from_a_superseded_revision(self) -> None:
        scope = _scope(
            _record("record-1", "auth.py", 1),
            _record("record-2", "profile.py", 1),
        )
        graph = _flat_graph(scope, 2)
        first = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bundle = json.loads(first.stdout)
        revised_graph = copy.deepcopy(graph)
        revised_graph["review_units"][0]["purpose"] = "Review revised auth behavior"
        revised = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": revised_graph,
                "environment": {"available_agent_slots": 5},
                "previous_review_plan": first_bundle["review_plan"],
            },
        )
        self.assertEqual(revised.returncode, 0, revised.stderr)

        completed = _run_contract(
            "complete-graph",
            {
                "bundle": json.loads(revised.stdout),
                "reviewer_results": _reviewer_results(first_bundle),
            },
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("task_id", completed.stderr)

    def test_complete_graph_preserves_partial_review_gaps(self) -> None:
        scope = _scope(
            *[_record(f"record-{index}", f"file-{index}.py", 1) for index in range(1, 8)]
        )
        graph = _flat_graph(scope, 7)
        paused = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )
        self.assertEqual(paused.returncode, 0, paused.stderr)
        graph_revision = json.loads(paused.stdout)["review_plan"]["preflight"][
            "graph_revision"
        ]
        partial = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
                "budget_decision": {
                    "kind": "paired_partial_review",
                    "approved": True,
                    "graph_revision": graph_revision,
                    "selection_basis": "risk_prioritized",
                    "selected_unit_ids": ["unit-1", "unit-2"],
                },
            },
        )
        self.assertEqual(partial.returncode, 0, partial.stderr)
        bundle = json.loads(partial.stdout)

        completed = _run_contract(
            "complete-graph",
            {
                "bundle": bundle,
                "reviewer_results": _reviewer_results(bundle),
            },
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        ledger = json.loads(completed.stdout)["coverage_ledger"]
        self.assertFalse(ledger["complete"])
        covered = {
            entry["record_id"] for entry in ledger["records"] if entry["covered"]
        }
        self.assertEqual(covered, {"record-1", "record-2"})
        self.assertEqual(
            set(ledger["uncovered_record_ids"]),
            {f"record-{index}" for index in range(3, 8)},
        )

    def test_context_only_integration_unit_gates_graph_completion(self) -> None:
        scope = _scope(
            _record("record-auth", "auth.py", 1),
            _record("record-billing", "billing.py", 1),
        )
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [
                _feature("feature-auth", "Auth", ["unit-auth"]),
                _feature("feature-billing", "Billing", ["unit-billing"]),
            ],
            "review_units": [
                _unit("unit-auth", "feature-auth", ["record-auth"]),
                _unit("unit-billing", "feature-billing", ["record-billing"]),
                {
                    **_integration_unit(
                        "unit-integration",
                        [],
                        ["unit-auth", "unit-billing"],
                    ),
                    "context_record_ids": ["record-auth", "record-billing"],
                },
            ],
            "ownership": {
                "record-auth": "unit-auth",
                "record-billing": "unit-billing",
            },
            "edges": [
                {
                    "kind": "material_contract",
                    "from_unit_id": participant,
                    "to_unit_id": "unit-integration",
                }
                for participant in ("unit-auth", "unit-billing")
            ],
        }

        built = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )

        self.assertEqual(built.returncode, 0, built.stderr)
        bundle = json.loads(built.stdout)
        integration_state = next(
            unit
            for unit in bundle["coverage_ledger"]["units"]
            if unit["unit_id"] == "unit-integration"
        )
        self.assertFalse(integration_state["covered"])
        incomplete_results = [
            result
            for result in _reviewer_results(bundle)
            if result["unit_id"] != "unit-integration"
        ]

        incomplete = _run_contract(
            "complete-graph",
            {"bundle": bundle, "reviewer_results": incomplete_results},
        )
        complete = _run_contract(
            "complete-graph",
            {"bundle": bundle, "reviewer_results": _reviewer_results(bundle)},
        )

        self.assertEqual(incomplete.returncode, 2)
        self.assertIn("missing successful reviewer roles", incomplete.stderr)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        completed_ledger = json.loads(complete.stdout)["coverage_ledger"]
        self.assertTrue(completed_ledger["complete"])
        self.assertTrue(
            next(
                unit["covered"]
                for unit in completed_ledger["units"]
                if unit["unit_id"] == "unit-integration"
            )
        )

    def test_incidental_dependency_cannot_masquerade_as_integration_work(self) -> None:
        scope = _scope(
            _record("record-auth", "auth.py", 5),
            _record("record-billing", "billing.py", 7),
            _record("record-import", "shared.py", 1),
        )
        incidental = _integration_unit(
            "unit-integration",
            ["record-import"],
            ["unit-auth", "unit-billing"],
        )
        incidental["integration_criteria"] = {"basis": "shared_import"}
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [
                _feature("feature-auth", "Authentication", ["unit-auth"]),
                _feature("feature-billing", "Billing", ["unit-billing"]),
            ],
            "review_units": [
                _unit("unit-auth", "feature-auth", ["record-auth"]),
                _unit("unit-billing", "feature-billing", ["record-billing"]),
                incidental,
            ],
            "ownership": {
                "record-auth": "unit-auth",
                "record-billing": "unit-billing",
                "record-import": "unit-integration",
            },
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 4,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("material", result.stderr)

    def test_material_integration_requires_edges_from_every_participant(self) -> None:
        scope = _scope(
            _record("record-auth", "auth.py", 5),
            _record("record-billing", "billing.py", 7),
            _record("record-contract", "contract.py", 1),
        )
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [
                _feature("feature-auth", "Authentication", ["unit-auth"]),
                _feature("feature-billing", "Billing", ["unit-billing"]),
            ],
            "review_units": [
                _unit("unit-auth", "feature-auth", ["record-auth"]),
                _unit("unit-billing", "feature-billing", ["record-billing"]),
                _integration_unit(
                    "unit-contract",
                    ["record-contract"],
                    ["unit-auth", "unit-billing"],
                ),
            ],
            "ownership": {
                "record-auth": "unit-auth",
                "record-billing": "unit-billing",
                "record-contract": "unit-contract",
            },
            "edges": [
                {
                    "kind": "material_contract",
                    "from_unit_id": "unit-auth",
                    "to_unit_id": "unit-contract",
                }
            ],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("edge from every participant", result.stderr)

    def test_feature_and_unit_links_must_be_bidirectional(self) -> None:
        scope = _scope(_record("record-child", "child.py", 1))
        parent = _feature("feature-parent", "Parent", [])
        parent["child_feature_ids"] = ["feature-child"]
        parent["subdivision"] = {"reason": "large", "boundary": "behavior"}
        child = _feature("feature-child", "Child", ["unit-child"])
        child["parent_feature_id"] = "feature-parent"
        mismatched_unit = _unit(
            "unit-child", "feature-parent", ["record-child"]
        )
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [parent, child],
            "review_units": [mismatched_unit],
            "ownership": {"record-child": "unit-child"},
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("bidirectional", result.stderr)

    def test_subdivided_parent_cannot_receive_a_redundant_reviewer_pair(self) -> None:
        scope = _scope(
            _record("record-parent", "checkout.py", 1),
            _record("record-charge", "checkout.py", 20),
            _record("record-receipt", "checkout.py", 80),
        )
        parent = _feature("feature-checkout", "Checkout", ["unit-parent"])
        parent["child_feature_ids"] = ["feature-charge", "feature-receipt"]
        parent["subdivision"] = {
            "reason": "context_budget",
            "boundary": "behavior",
        }
        charge = _feature("feature-charge", "Charge", ["unit-charge"])
        charge["parent_feature_id"] = "feature-checkout"
        receipt = _feature("feature-receipt", "Receipt", ["unit-receipt"])
        receipt["parent_feature_id"] = "feature-checkout"
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [parent, charge, receipt],
            "review_units": [
                _unit("unit-parent", "feature-checkout", ["record-parent"]),
                _unit("unit-charge", "feature-charge", ["record-charge"]),
                _unit("unit-receipt", "feature-receipt", ["record-receipt"]),
            ],
            "ownership": {
                "record-parent": "unit-parent",
                "record-charge": "unit-charge",
                "record-receipt": "unit-receipt",
            },
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 4,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("logical parent", result.stderr)

    def test_subdivided_children_and_material_integration_are_paired_without_parent(self) -> None:
        scope = _scope(
            _record("record-charge", "checkout.py", 20),
            _record("record-receipt", "checkout.py", 80),
            _record("record-contract", "events.py", 5),
        )
        parent = _feature("feature-checkout", "Checkout", [])
        parent["child_feature_ids"] = ["feature-charge", "feature-receipt"]
        parent["subdivision"] = {
            "reason": "independently_verifiable_workflows",
            "boundary": "state_transition",
        }
        charge = _feature("feature-charge", "Charge", ["unit-charge"])
        charge["parent_feature_id"] = "feature-checkout"
        receipt = _feature("feature-receipt", "Receipt", ["unit-receipt"])
        receipt["parent_feature_id"] = "feature-checkout"
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [parent, charge, receipt],
            "review_units": [
                _unit("unit-charge", "feature-charge", ["record-charge"]),
                _unit("unit-receipt", "feature-receipt", ["record-receipt"]),
                _integration_unit(
                    "unit-contract",
                    ["record-contract"],
                    ["unit-charge", "unit-receipt"],
                ),
            ],
            "ownership": {
                "record-charge": "unit-charge",
                "record-receipt": "unit-receipt",
                "record-contract": "unit-contract",
            },
            "edges": [
                {
                    "kind": "material_contract",
                    "from_unit_id": "unit-charge",
                    "to_unit_id": "unit-contract",
                },
                {
                    "kind": "material_contract",
                    "from_unit_id": "unit-receipt",
                    "to_unit_id": "unit-contract",
                },
            ],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        self.assertEqual(bundle["review_plan"]["preflight"]["executable_units"], 3)
        self.assertEqual(len(bundle["reviewer_task_envelopes"]), 6)
        self.assertNotIn(
            "feature-checkout",
            {envelope["unit_id"] for envelope in bundle["reviewer_task_envelopes"]},
        )
        self.assertEqual(
            {
                envelope["role"]
                for envelope in bundle["reviewer_task_envelopes"]
                if envelope["unit_id"] == "unit-contract"
            },
            {"internal_behavior", "external_contract"},
        )

    def test_standard_budget_keeps_six_pairs_and_adapts_waves_to_capacity(self) -> None:
        records = [
            _record(f"record-{index}", f"feature_{index}.py", 1)
            for index in range(1, 7)
        ]
        scope = _scope(*records)
        features = [
            _feature(f"feature-{index}", f"Feature {index}", [f"unit-{index}"])
            for index in range(1, 7)
        ]
        units = [
            _unit(
                f"unit-{index}",
                f"feature-{index}",
                [f"record-{index}"],
            )
            for index in range(1, 7)
        ]
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": features,
            "review_units": units,
            "ownership": {
                f"record-{index}": f"unit-{index}" for index in range(1, 7)
            },
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 3,
                    "usable_context_tokens": 10_000,
                    "resource_limits": {
                        "user": {
                            "command_limit": 8,
                            "per_pass_timeout_seconds": 120,
                        },
                        "host": {
                            "per_pass_timeout_seconds": 90,
                            "total_deadline_seconds": 600,
                        },
                    },
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        preflight = bundle["review_plan"]["preflight"]
        self.assertEqual(preflight["status"], "ready")
        self.assertEqual(preflight["executable_units"], 6)
        self.assertEqual(preflight["required_reviewer_passes"], 12)
        self.assertEqual(preflight["effective_concurrency"], 2)
        self.assertEqual(preflight["waves"], 6)
        self.assertEqual(
            preflight["context_budget"],
            {
                "usable_tokens": 10_000,
                "reserved_tokens": 3_000,
                "max_supplied_tokens_per_unit": 7_000,
                "largest_unit_estimate_tokens": 1_000,
                "estimate_kind": "declared",
            },
        )
        self.assertEqual(
            preflight["resource_limits"]["effective"],
            {
                "command_limit": 8,
                "per_pass_timeout_seconds": 90,
                "total_deadline_seconds": 600,
            },
        )
        self.assertEqual(len(bundle["reviewer_task_envelopes"]), 12)

    def test_context_overflow_pauses_before_dispatch_for_subdivision(self) -> None:
        scope = _scope(_record("record-large", "large.py", 1))
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [
                _feature("feature-large", "Large workflow", ["unit-large"])
            ],
            "review_units": [
                _unit(
                    "unit-large",
                    "feature-large",
                    ["record-large"],
                    estimated_context_tokens=7_001,
                )
            ],
            "ownership": {"record-large": "unit-large"},
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        self.assertEqual(bundle["review_plan"]["preflight"]["status"], "paused")
        self.assertEqual(
            bundle["review_plan"]["preflight"]["blocking_reasons"],
            ["unit_context_overflow:unit-large"],
        )
        self.assertEqual(
            bundle["review_plan"]["preflight"]["required_action"],
            "subdivide_by_behavior_state_transition_or_contract",
        )
        self.assertEqual(bundle["reviewer_task_envelopes"], [])

    def test_effective_concurrency_never_exceeds_pending_passes(self) -> None:
        scope = _scope(_record("record-1", "small.py", 1))

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": _flat_graph(scope, 1),
                "environment": {
                    "available_agent_slots": 20,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        preflight = json.loads(result.stdout)["review_plan"]["preflight"]
        self.assertEqual(preflight["required_reviewer_passes"], 2)
        self.assertEqual(preflight["effective_concurrency"], 2)
        self.assertEqual(preflight["waves"], 1)

    def test_build_graph_rejects_empty_scope_before_dispatch(self) -> None:
        scope = _scope()
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [_feature("feature-empty", "Empty", ["unit-empty"])],
            "review_units": [
                _unit("unit-empty", "feature-empty", [])
            ],
            "ownership": {},
            "edges": [],
        }

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("empty scope", result.stderr)

    def test_seventh_unit_pauses_with_explicit_overflow_choices(self) -> None:
        scope = _scope(
            *[
                _record(f"record-{index}", f"feature_{index}.py", 1)
                for index in range(1, 8)
            ]
        )

        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": _flat_graph(scope, 7),
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        preflight = bundle["review_plan"]["preflight"]
        self.assertEqual(preflight["status"], "paused")
        self.assertEqual(
            preflight["blocking_reasons"],
            [
                "standard_unit_limit_exceeded",
                "standard_reviewer_pass_limit_exceeded",
            ],
        )
        self.assertEqual(
            preflight["overflow_choices"],
            ["scope_reduction", "budget_expansion", "paired_partial_review"],
        )
        self.assertEqual(
            preflight["standard_profile"],
            {
                "max_executable_units": 6,
                "max_reviewer_passes": 12,
                "max_active_reviewers": 4,
                "context_reserve_ratio": 0.3,
            },
        )
        self.assertEqual(bundle["reviewer_task_envelopes"], [])

    def test_explicit_budget_expansion_preserves_all_seven_pairs(self) -> None:
        scope = _scope(
            *[
                _record(f"record-{index}", f"feature_{index}.py", 1)
                for index in range(1, 8)
            ]
        )

        initial = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": _flat_graph(scope, 7),
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        graph_revision = json.loads(initial.stdout)["review_plan"]["preflight"][
            "graph_revision"
        ]
        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": _flat_graph(scope, 7),
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
                "budget_decision": {
                    "kind": "budget_expansion",
                    "approved": True,
                    "graph_revision": graph_revision,
                    "max_executable_units": 7,
                    "max_reviewer_passes": 14,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        preflight = bundle["review_plan"]["preflight"]
        self.assertEqual(preflight["status"], "ready")
        self.assertEqual(
            preflight["budget_decision"],
            {
                "kind": "budget_expansion",
                "approved": True,
                "graph_revision": graph_revision,
                "max_executable_units": 7,
                "max_reviewer_passes": 14,
            },
        )
        self.assertEqual(preflight["effective_unit_limit"], 7)
        self.assertEqual(preflight["effective_reviewer_pass_limit"], 14)
        self.assertEqual(len(bundle["reviewer_task_envelopes"]), 14)

    def test_partial_review_selects_whole_pairs_and_discloses_uncovered_records(self) -> None:
        scope = _scope(
            *[
                _record(f"record-{index}", f"feature_{index}.py", 1)
                for index in range(1, 8)
            ]
        )
        selected_units = [f"unit-{index}" for index in range(1, 7)]

        graph = _flat_graph(scope, 7)
        initial = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
            },
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        graph_revision = json.loads(initial.stdout)["review_plan"]["preflight"][
            "graph_revision"
        ]
        result = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {
                    "available_agent_slots": 5,
                    "usable_context_tokens": 10_000,
                },
                "budget_decision": {
                    "kind": "paired_partial_review",
                    "approved": True,
                    "graph_revision": graph_revision,
                    "selection_basis": "risk_prioritized",
                    "selected_unit_ids": selected_units,
                },
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = json.loads(result.stdout)
        preflight = bundle["review_plan"]["preflight"]
        self.assertEqual(preflight["status"], "ready_partial")
        self.assertEqual(preflight["graph_executable_units"], 7)
        self.assertEqual(preflight["selected_executable_units"], 6)
        self.assertEqual(preflight["required_reviewer_passes"], 12)
        self.assertEqual(preflight["unselected_unit_ids"], ["unit-7"])
        self.assertEqual(preflight["unselected_record_ids"], ["record-7"])
        self.assertEqual(len(bundle["reviewer_task_envelopes"]), 12)
        self.assertTrue(
            all(
                envelope["unit_id"] in selected_units
                for envelope in bundle["reviewer_task_envelopes"]
            )
        )
        unselected = next(
            entry
            for entry in bundle["coverage_ledger"]["records"]
            if entry["record_id"] == "record-7"
        )
        self.assertFalse(unselected["selected_for_review"])
        self.assertEqual(unselected["uncovered_reason"], "not_selected_by_partial_review")
        self.assertFalse(unselected["covered"])

    def test_graph_growth_requires_a_new_revision_bound_budget_approval(self) -> None:
        scope = _scope(
            *[
                _record(f"record-{index}", f"feature_{index}.py", 1)
                for index in range(1, 9)
            ]
        )
        initial_graph = _flat_graph(scope, 7)
        initial_graph["review_units"][-1]["changed_record_ids"].append("record-8")
        initial_graph["ownership"]["record-8"] = "unit-7"
        environment = {
            "available_agent_slots": 5,
            "usable_context_tokens": 10_000,
        }
        paused = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": initial_graph,
                "environment": environment,
            },
        )
        self.assertEqual(paused.returncode, 0, paused.stderr)
        paused_plan = json.loads(paused.stdout)["review_plan"]
        old_revision = paused_plan["preflight"]["graph_revision"]
        approved = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": initial_graph,
                "environment": environment,
                "budget_decision": {
                    "kind": "budget_expansion",
                    "approved": True,
                    "graph_revision": old_revision,
                    "max_executable_units": 8,
                    "max_reviewer_passes": 16,
                },
            },
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        approved_plan = json.loads(approved.stdout)["review_plan"]

        expanded_graph = _flat_graph(scope, 7)
        expanded_graph["review_units"].append(
            _integration_unit(
                "unit-contract", ["record-8"], ["unit-1", "unit-7"]
            )
        )
        expanded_graph["ownership"]["record-8"] = "unit-contract"
        expanded_graph["edges"] = [
            {
                "kind": "material_contract",
                "from_unit_id": participant,
                "to_unit_id": "unit-contract",
            }
            for participant in ["unit-1", "unit-7"]
        ]
        stale_approval = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": expanded_graph,
                "environment": environment,
                "previous_review_plan": approved_plan,
                "budget_decision": {
                    "kind": "budget_expansion",
                    "approved": True,
                    "graph_revision": old_revision,
                    "max_executable_units": 8,
                    "max_reviewer_passes": 16,
                },
            },
        )

        self.assertEqual(stale_approval.returncode, 2)
        self.assertIn("current graph revision", stale_approval.stderr)

    def test_late_integration_unit_updates_ownership_and_repreflights_budget(self) -> None:
        scope = _scope(
            *[
                _record(f"record-{index}", f"feature_{index}.py", 1)
                for index in range(1, 8)
            ]
        )
        initial_graph = _flat_graph(scope, 6)
        initial_graph["review_units"][-1]["changed_record_ids"].append("record-7")
        initial_graph["ownership"]["record-7"] = "unit-6"
        environment = {
            "available_agent_slots": 5,
            "usable_context_tokens": 10_000,
        }
        initial = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": initial_graph,
                "environment": environment,
            },
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        initial_bundle = json.loads(initial.stdout)
        self.assertEqual(initial_bundle["review_plan"]["preflight"]["status"], "ready")

        expanded_graph = _flat_graph(scope, 6)
        expanded_graph["review_units"].append(
            _integration_unit(
                "unit-contract",
                ["record-7"],
                ["unit-1", "unit-6"],
            )
        )
        expanded_graph["ownership"]["record-7"] = "unit-contract"
        expanded_graph["edges"] = [
            {
                "kind": "material_contract",
                "from_unit_id": "unit-1",
                "to_unit_id": "unit-contract",
            },
            {
                "kind": "material_contract",
                "from_unit_id": "unit-6",
                "to_unit_id": "unit-contract",
            },
        ]

        repreflight = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": expanded_graph,
                "environment": environment,
                "previous_review_plan": initial_bundle["review_plan"],
            },
        )

        self.assertEqual(repreflight.returncode, 0, repreflight.stderr)
        bundle = json.loads(repreflight.stdout)
        preflight = bundle["review_plan"]["preflight"]
        self.assertEqual(preflight["sequence"], 2)
        self.assertEqual(preflight["trigger"], "late_integration_unit")
        self.assertEqual(preflight["added_unit_ids"], ["unit-contract"])
        self.assertTrue(preflight["graph_changed"])
        self.assertNotEqual(
            preflight["graph_revision"],
            initial_bundle["review_plan"]["preflight"]["graph_revision"],
        )
        self.assertEqual(preflight["status"], "paused")
        self.assertIn("standard_unit_limit_exceeded", preflight["blocking_reasons"])
        self.assertEqual(bundle["feature_graph"]["ownership"]["record-7"], "unit-contract")
        self.assertEqual(bundle["reviewer_task_envelopes"], [])

    def test_repreflight_changes_task_ids_when_unit_evidence_changes(self) -> None:
        scope = _scope(
            _record("record-owned", "service.py", 1),
            _record("record-context", "caller.py", 1),
        )
        graph = {
            "schema_version": "2.0",
            "scope_id": scope["scope_id"],
            "features": [_feature("feature-service", "Service", ["unit-service"])],
            "review_units": [
                _unit(
                    "unit-service",
                    "feature-service",
                    ["record-owned", "record-context"],
                    context=["record-context"],
                )
            ],
            "ownership": {
                "record-owned": "unit-service",
                "record-context": "unit-service",
            },
            "edges": [],
        }
        initial = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": graph,
                "environment": {"available_agent_slots": 5},
            },
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        initial_bundle = json.loads(initial.stdout)
        revised_graph = copy.deepcopy(graph)
        revised_graph["review_units"][0]["context_record_ids"] = []

        revised = _run_contract(
            "build-graph",
            {
                "scope_manifest": scope,
                "feature_graph": revised_graph,
                "environment": {"available_agent_slots": 5},
                "previous_review_plan": initial_bundle["review_plan"],
            },
        )

        self.assertEqual(revised.returncode, 0, revised.stderr)
        revised_bundle = json.loads(revised.stdout)
        self.assertNotEqual(
            {
                envelope["task_id"]
                for envelope in initial_bundle["reviewer_task_envelopes"]
            },
            {
                envelope["task_id"]
                for envelope in revised_bundle["reviewer_task_envelopes"]
            },
        )
        self.assertEqual(
            {
                envelope["graph_revision"]
                for envelope in revised_bundle["reviewer_task_envelopes"]
            },
            {revised_bundle["review_plan"]["preflight"]["graph_revision"]},
        )


if __name__ == "__main__":
    unittest.main()
