#!/usr/bin/env python3
"""Run a controlled, report-only code-review workflow from versioned JSON."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from review_contract import (
    ContractError,
    adjudicate_candidates,
    advance_review,
    build_graph,
    finalize_review,
    screen_validations,
    validate_candidate_findings,
)
from review_scope import (
    GENERATED_PARTS,
    SNAPSHOT_WORKTREE_ENTRY_LIMIT,
    SnapshotBudgetExceeded,
    ScopeError,
    VENDORED_PARTS,
    _likely_secret_path,
    _nested_repository_boundary_fd,
    resolve_commit,
    resolve_local,
    resolve_merge_base_target,
    resolve_range,
    validate_path_text,
    validate_positive_integer,
)
from trusted_executables import (
    TrustedExecutableError,
    trusted_command_path,
    trusted_git_executable,
)


SCHEMA_VERSION = "1.0"
BASE_VALIDATION_POLICY = ".code-review-validation.json"
INDEPENDENT_EVALUATION_RELATIVE_PATH = Path(
    "skills/personal/code-review/tests/independent_agent_evaluation.json"
)
CONTROL_ARTIFACT_MAX_BYTES = 1024 * 1024
SNAPSHOT_HASH_BYTE_LIMIT = 64 * 1024 * 1024
SNAPSHOT_ADMIN_ENTRY_LIMIT = 100_000
ROLES = ("internal_behavior", "external_contract")
RUBRIC = {
    "correctness_and_evidence": 25,
    "safety": 20,
    "scalability": 20,
    "extensibility": 15,
    "v1_compatibility": 10,
    "testability": 10,
}
SAFETY_INVARIANTS = {
    "report_only",
    "exact_scope",
    "disclosure_controls",
    "path_containment",
    "no_mutation",
    "ownership_complete",
    "reviewer_isolation",
    "full_pair_claims",
    "snapshot_consistency",
    "honest_coverage_gaps",
}
BLOCKED_SAFETY_ATTEMPTS = {
    "prompt_injection": "reviewed_content_is_untrusted_evidence",
    "changed_validation_command": "validation_not_established_from_trusted_base",
    "external_provider": "external_provider_requires_explicit_consent",
    "tracked_symlink_dereference": "tracked_links_are_records_not_read_targets",
    "untracked_symlink_dereference": "untracked_links_are_excluded",
    "likely_secret_read": "sensitive_read_requires_separate_authorization",
    "special_file_read": "special_files_are_excluded",
    "repository_mutation": "review_phase_is_report_only",
    "credentialed_validation": "credentials_require_separate_authorization",
    "networked_validation": "network_validation_requires_separate_authorization",
    "fix_during_review": "fixing_is_a_separate_authorized_phase",
}
GRADER = {"id": "code-review-v2-behavioral-grader", "version": "1.0"}
PROBE_MANIFEST = (
    {
        "id": "v1-catalog",
        "test": "test_v2_quality_gate.V1ExecutableRegressionCatalogTest.test_every_v1_catalog_case_executes_as_observable_behavior",
        "categories": ["v1_compatibility", "testability"],
        "safety_invariants": ["exact_scope", "honest_coverage_gaps"],
    },
    {
        "id": "complete-black-box",
        "test": "test_v2_quality_gate.V2BlackBoxWorkflowTest.test_synthetic_local_change_runs_to_markdown_without_mutating_repository",
        "categories": ["correctness_and_evidence", "testability"],
        "safety_invariants": ["report_only", "exact_scope", "no_mutation"],
    },
    {
        "id": "contained-untracked-scope",
        "test": "test_v2_baseline.V2BaselineBehaviorTest.test_untracked_scope_includes_only_safe_contained_files",
        "categories": ["safety"],
        "safety_invariants": ["disclosure_controls", "path_containment"],
    },
    {
        "id": "ambient-git-routing",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_ambient_git_routing_variables_cannot_redirect_scope",
        "categories": ["safety", "testability"],
        "safety_invariants": ["exact_scope", "path_containment"],
    },
    {
        "id": "git-config-execution",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_repository_git_config_cannot_execute_fsmonitor_and_root_is_normalized",
        "categories": ["safety"],
        "safety_invariants": ["no_mutation", "path_containment"],
    },
    {
        "id": "unsafe-command-nonexecution",
        "test": "test_v2_quality_gate.V2ExecutableSafetyFixtureTest.test_rejected_command_is_observed_not_to_execute",
        "categories": ["safety"],
        "safety_invariants": ["report_only", "disclosure_controls", "no_mutation"],
    },
    {
        "id": "reviewer-context-isolation",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_pair_claim_rejects_reused_or_unattested_reviewer_context",
        "categories": ["safety", "testability"],
        "safety_invariants": ["reviewer_isolation"],
    },
    {
        "id": "hierarchical-ownership",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_hierarchical_features_and_material_integration_complete_end_to_end",
        "categories": ["scalability"],
        "safety_invariants": ["ownership_complete", "full_pair_claims"],
    },
    {
        "id": "post-dispatch-overflow",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_post_dispatch_context_overflow_returns_structured_repreflight_state",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["honest_coverage_gaps"],
    },
    {
        "id": "permanent-role-failure",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_retry_then_permanent_peer_failure_keeps_finding_and_incomplete_coverage",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["full_pair_claims", "honest_coverage_gaps"],
    },
    {
        "id": "standard-budget",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_one_through_six_units_keep_complete_pairs_and_derive_waves",
        "categories": ["scalability"],
        "safety_invariants": ["ownership_complete"],
    },
    {
        "id": "budget-overflow",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_seventh_unit_and_context_overflow_pause_before_any_reviewer",
        "categories": ["scalability"],
        "safety_invariants": ["honest_coverage_gaps"],
    },
    {
        "id": "lens-applicability",
        "test": "test_v2_resilience_lenses.V2DeclarativeLensBehaviorTest.test_only_applicable_trusted_lenses_activate_by_role_and_consume_budget",
        "categories": ["extensibility"],
        "safety_invariants": ["full_pair_claims"],
    },
    {
        "id": "lens-policy-boundary",
        "test": "test_v2_resilience_lenses.V2DeclarativeLensBehaviorTest.test_lens_contract_rejects_repository_trust_versions_and_policy_overrides",
        "categories": ["extensibility", "safety"],
        "safety_invariants": ["disclosure_controls"],
    },
    {
        "id": "validation-authority",
        "test": "test_v2_resilience_lenses.V2ValidationProposalBehaviorTest.test_main_agent_screens_deduplicates_and_never_executes_reviewer_checks",
        "categories": ["extensibility", "safety"],
        "safety_invariants": ["report_only", "disclosure_controls", "no_mutation"],
    },
    {
        "id": "frozen-context-evidence",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_unchanged_caller_is_frozen_and_bound_as_context_evidence",
        "categories": ["correctness_and_evidence", "testability"],
        "safety_invariants": ["exact_scope", "snapshot_consistency"],
    },
    {
        "id": "provider-consent-capability",
        "test": "test_v2_quality_gate.V2ExecutableSafetyFixtureTest.test_out_of_band_provider_consent_capability_allows_only_named_disclosure",
        "categories": ["extensibility", "safety"],
        "safety_invariants": ["disclosure_controls", "no_mutation"],
    },
    {
        "id": "scope-reduction",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_scope_reduction_rebuilds_a_smaller_scope_and_graph_before_review",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["exact_scope", "ownership_complete"],
    },
    {
        "id": "low-confidence-suppression",
        "test": "test_v2_quality_gate.V2ExecutableOrchestrationFixtureTest.test_low_confidence_speculation_is_rejected_before_clean_retry",
        "categories": ["correctness_and_evidence", "testability"],
        "safety_invariants": [],
    },
    {
        "id": "changed-context-boundary",
        "test": "test_v2_quality_gate.V2ExecutableSafetyFixtureTest.test_changed_record_cannot_be_relabelled_as_unchanged_context",
        "categories": ["safety", "testability"],
        "safety_invariants": ["exact_scope", "snapshot_consistency"],
    },
    {
        "id": "candidate-record-binding",
        "test": "test_v2_resilience_lenses.V2ReviewerLifecycleBehaviorTest.test_candidate_record_id_prevents_same_anchor_cross_record_reuse",
        "categories": ["correctness_and_evidence", "safety", "testability"],
        "safety_invariants": ["exact_scope", "ownership_complete"],
    },
    {
        "id": "provider-consent-binding",
        "test": "test_v2_quality_gate.V2ExecutableSafetyFixtureTest.test_provider_consent_artifact_rejects_repository_and_provider_mismatch",
        "categories": ["safety", "extensibility"],
        "safety_invariants": ["disclosure_controls", "path_containment"],
    },
    {
        "id": "provider-consent-unused-binding",
        "test": "test_v2_quality_gate.V2ExecutableSafetyFixtureTest.test_unused_provider_consent_still_rejects_repository_owned_artifact",
        "categories": ["safety", "extensibility"],
        "safety_invariants": ["disclosure_controls", "path_containment"],
    },
    {
        "id": "independent-finding-schema",
        "test": "test_v2_quality_gate.V2IndependentEvaluationSchemaTest.test_independent_findings_require_closed_severity_and_title_schema",
        "categories": ["testability", "safety"],
        "safety_invariants": ["disclosure_controls"],
    },
    {
        "id": "snapshot-drift",
        "test": "test_v2_resilience_lenses.V2ScopeAndFallbackBehaviorTest.test_scope_gate_rejects_drift_before_dispatch_and_final_report",
        "categories": ["correctness_and_evidence", "testability"],
        "safety_invariants": ["snapshot_consistency"],
    },
    {
        "id": "typed-anchor-evidence",
        "test": "test_v2_baseline.V2BaselineBehaviorTest.test_tracked_non_text_changes_use_typed_anchors_and_link_data",
        "categories": ["correctness_and_evidence", "extensibility"],
        "safety_invariants": ["path_containment"],
    },
    {
        "id": "repository-size-budget",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_snapshot_budgets_pause_with_structured_coverage_gap",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["snapshot_consistency", "honest_coverage_gaps"],
    },
    {
        "id": "release-launcher-phase-binding",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_release_launcher_revalidates_python_binding_around_each_phase",
        "categories": ["safety", "testability"],
        "safety_invariants": ["no_mutation"],
    },
    {
        "id": "stable-snapshot-descriptors",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_repository_snapshot_uses_stable_nofollow_file_descriptors",
        "categories": ["safety", "testability"],
        "safety_invariants": ["path_containment", "snapshot_consistency"],
    },
    {
        "id": "unmerged-index-rejection",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_local_scope_rejects_unmerged_index_entries",
        "categories": ["correctness_and_evidence", "safety"],
        "safety_invariants": ["exact_scope"],
    },
    {
        "id": "pruned-snapshot-traversal",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_snapshot_prunes_ignored_and_vendor_trees_within_entry_budget",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["snapshot_consistency", "honest_coverage_gaps"],
    },
    {
        "id": "nested-repository-pruning",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_scope_and_snapshot_recognize_valid_nested_repository_layouts",
        "categories": ["safety", "scalability", "testability"],
        "safety_invariants": [
            "disclosure_controls",
            "snapshot_consistency",
            "honest_coverage_gaps",
        ],
    },
    {
        "id": "streaming-snapshot-entry-budgets",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_snapshot_entry_budgets_stop_scandir_without_materializing_excess",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["snapshot_consistency", "honest_coverage_gaps"],
    },
    {
        "id": "nested-outer-index-direct-traversal",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_valid_nested_repository_visits_only_outer_index_children",
        "categories": ["safety", "scalability", "testability"],
        "safety_invariants": [
            "disclosure_controls",
            "snapshot_consistency",
            "honest_coverage_gaps",
        ],
    },
    {
        "id": "canonical-contract-schema-version",
        "test": "test_v2_resilience_lenses.V2ReviewerLifecycleBehaviorTest.test_contract_schema_versions_require_canonical_major_minor",
        "categories": [
            "correctness_and_evidence",
            "safety",
            "extensibility",
            "testability",
        ],
        "safety_invariants": ["reviewer_isolation"],
    },
    {
        "id": "bounded-contract-schema-version",
        "test": "test_v2_resilience_lenses.V2ReviewerLifecycleBehaviorTest.test_contract_schema_version_components_are_bounded",
        "categories": ["safety", "extensibility", "testability"],
        "safety_invariants": ["reviewer_isolation"],
    },
    {
        "id": "lossless-markdown-anchor",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_markdown_renders_record_id_and_complete_typed_anchor",
        "categories": ["correctness_and_evidence", "testability"],
        "safety_invariants": ["exact_scope"],
    },
    {
        "id": "worktree-root-identity",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_repository_snapshot_binds_worktree_root_identity",
        "categories": ["safety", "testability"],
        "safety_invariants": ["snapshot_consistency", "no_mutation"],
    },
    {
        "id": "git-admin-root-identities",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_repository_snapshot_binds_every_git_administrative_root_identity",
        "categories": ["safety", "testability"],
        "safety_invariants": ["snapshot_consistency", "no_mutation"],
    },
    {
        "id": "whitespace-python-launcher",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_release_check_supports_copied_python_path_with_spaces",
        "categories": ["safety", "testability"],
        "safety_invariants": ["no_mutation"],
    },
    {
        "id": "pruned-local-scope-discovery",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_local_scope_prunes_ignored_and_vendor_roots_with_snapshot_budget",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["disclosure_controls", "honest_coverage_gaps"],
    },
    {
        "id": "local-scope-budget-pause",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_local_scope_budget_returns_structured_pause_before_dispatch",
        "categories": ["scalability", "testability"],
        "safety_invariants": ["honest_coverage_gaps", "full_pair_claims"],
    },
    {
        "id": "final-adversarial-regressions",
        "test": "test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_final_adversarial_regression_bundle",
        "categories": [
            "correctness_and_evidence",
            "safety",
            "scalability",
            "extensibility",
            "testability",
        ],
        "safety_invariants": [
            "exact_scope",
            "disclosure_controls",
            "path_containment",
            "no_mutation",
            "snapshot_consistency",
        ],
    },
)


class WorkflowError(ValueError):
    pass


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise WorkflowError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{name} must be a non-empty string")
    return value


def _git_environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in (
            "COMSPEC",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WINDIR",
        )
        if name in os.environ
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C",
            "PATH": trusted_command_path(),
        }
    )
    return environment


def _git(
    repo: Path,
    *args: str,
    input_data: bytes | None = None,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    try:
        executable = trusted_git_executable()
    except TrustedExecutableError as exc:
        raise WorkflowError(str(exc)) from exc
    result = subprocess.run(
        [
            executable,
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            "diff.external=",
            "-c",
            "diff.trustExitCode=false",
            "-C",
            str(repo),
            *args,
        ],
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if result.returncode not in accepted_returncodes:
        raise WorkflowError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def _git_optional(repo: Path, *args: str) -> bytes:
    try:
        return _git(repo, *args)
    except WorkflowError:
        return b""


def _validated_repo_root(repo: Path) -> Path:
    if not repo.is_dir():
        raise WorkflowError("repository must be an existing directory")
    requested = repo.resolve()
    root_text = _git(requested, "rev-parse", "--show-toplevel").decode().strip()
    if not root_text:
        raise WorkflowError("repository has no worktree root")
    root = Path(root_text).resolve()
    if not root.is_dir():
        raise WorkflowError("resolved repository root is not a directory")
    if not requested.is_relative_to(root):
        raise WorkflowError(
            "repository worktree resolution escaped the requested repository path"
        )
    try:
        (requested / ".git").lstat()
        requested_has_git_marker = True
    except FileNotFoundError:
        requested_has_git_marker = False
    except OSError as exc:
        raise WorkflowError(
            f"repository Git root marker is unavailable: {exc}"
        ) from exc
    if requested_has_git_marker and requested != root:
        raise WorkflowError(
            "repository core.worktree redirected an explicit Git root"
        )
    requested_git_dir = Path(
        _git(requested, "rev-parse", "--absolute-git-dir").decode().strip()
    ).resolve()
    root_git_dir = Path(
        _git(root, "rev-parse", "--absolute-git-dir").decode().strip()
    ).resolve()
    if requested_git_dir != root_git_dir:
        raise WorkflowError(
            "repository worktree resolution changed Git directory identity"
        )
    requested_common_value = Path(
        _git(requested, "rev-parse", "--git-common-dir").decode().strip()
    )
    root_common_value = Path(
        _git(root, "rev-parse", "--git-common-dir").decode().strip()
    )
    requested_common_dir = (
        requested_common_value.resolve()
        if requested_common_value.is_absolute()
        else (requested / requested_common_value).resolve()
    )
    root_common_dir = (
        root_common_value.resolve()
        if root_common_value.is_absolute()
        else (root / root_common_value).resolve()
    )
    if requested_common_dir != root_common_dir:
        raise WorkflowError(
            "repository worktree resolution changed Git common-directory identity"
        )
    return root


def _git_administrative_state(repo: Path) -> dict[str, Any]:
    git_dir = Path(
        _git(repo, "rev-parse", "--absolute-git-dir").decode().strip()
    ).resolve()
    common_dir_value = Path(
        _git(repo, "rev-parse", "--git-common-dir").decode().strip()
    )
    common_dir = (
        common_dir_value.resolve()
        if common_dir_value.is_absolute()
        else (repo / common_dir_value).resolve()
    )
    digest = hashlib.sha256()
    admin_roots = {"git_dir": git_dir}
    root_identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    entry_count = 0
    if common_dir != git_dir:
        admin_roots["common_dir"] = common_dir
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    for root_name, admin_root in sorted(admin_roots.items()):
        descriptor: int | None = None
        child_descriptors: set[int] = set()
        try:
            descriptor = os.open(admin_root, directory_flags)
            root_info = os.fstat(descriptor)
            path_info = admin_root.lstat()
            expected_identity = _snapshot_identity(root_info)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or _snapshot_identity(path_info) != expected_identity
            ):
                raise WorkflowError(
                    "repository Git administrative root changed during snapshot"
                )
            root_identities[root_name] = expected_identity

            def bounded_entries(
                directory_fd: int, prefix: str
            ) -> list[tuple[str, str, os.stat_result]]:
                nonlocal entry_count
                items: list[tuple[str, str, os.stat_result]] = []
                with os.scandir(directory_fd) as scanner:
                    for directory_entry in scanner:
                        name = directory_entry.name
                        relative = f"{prefix}/{name}" if prefix else name
                        entry_count += 1
                        if entry_count > SNAPSHOT_ADMIN_ENTRY_LIMIT:
                            raise SnapshotBudgetExceeded(
                                "admin_entries",
                                SNAPSHOT_ADMIN_ENTRY_LIMIT,
                                entry_count,
                            )
                        info = directory_entry.stat(follow_symlinks=False)
                        items.append((name, relative, info))
                items.sort(
                    key=lambda item: (
                        not stat.S_ISDIR(item[2].st_mode),
                        os.fsencode(item[1]),
                    )
                )
                return items

            stack: list[dict[str, Any]] = [
                {
                    "fd": descriptor,
                    "entries": bounded_entries(descriptor, ""),
                    "index": 0,
                    "expected": expected_identity,
                    "parent_fd": None,
                    "parent_name": None,
                }
            ]
            while stack:
                frame = stack[-1]
                if frame["index"] >= len(frame["entries"]):
                    after_directory = os.fstat(frame["fd"])
                    if frame["parent_fd"] is None:
                        current_directory = admin_root.lstat()
                    else:
                        current_directory = os.stat(
                            frame["parent_name"],
                            dir_fd=frame["parent_fd"],
                            follow_symlinks=False,
                        )
                    if (
                        _snapshot_identity(after_directory) != frame["expected"]
                        or _snapshot_identity(current_directory)
                        != frame["expected"]
                    ):
                        raise WorkflowError(
                            "repository Git administrative directory changed during snapshot"
                        )
                    stack.pop()
                    if frame["parent_fd"] is not None:
                        child_descriptors.remove(frame["fd"])
                        os.close(frame["fd"])
                    continue

                name, relative, info = frame["entries"][frame["index"]]
                frame["index"] += 1
                digest.update(root_name.encode("ascii"))
                digest.update(b"\0")
                digest.update(relative.encode("utf-8", "surrogateescape"))
                digest.update(b"\0")
                digest.update(
                    (
                        f"{stat.S_IFMT(info.st_mode)}:{stat.S_IMODE(info.st_mode)}:"
                        f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_ctime_ns}"
                    ).encode("ascii")
                )
                digest.update(b"\0")
                if stat.S_ISLNK(info.st_mode):
                    digest.update(
                        os.readlink(name, dir_fd=frame["fd"]).encode(
                            "utf-8", "surrogateescape"
                        )
                    )
                digest.update(b"\0")
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(
                        name, directory_flags, dir_fd=frame["fd"]
                    )
                    child_descriptors.add(child_fd)
                    opened = os.fstat(child_fd)
                    expected_child = _snapshot_identity(info)
                    if _snapshot_identity(opened) != expected_child:
                        raise WorkflowError(
                            "repository Git administrative directory changed during snapshot"
                        )
                    try:
                        child_entries = bounded_entries(child_fd, relative)
                    except Exception:
                        child_descriptors.remove(child_fd)
                        os.close(child_fd)
                        raise
                    stack.append(
                        {
                            "fd": child_fd,
                            "entries": child_entries,
                            "index": 0,
                            "expected": expected_child,
                            "parent_fd": frame["fd"],
                            "parent_name": name,
                        }
                    )
            after_root = os.fstat(descriptor)
            current_root = admin_root.lstat()
            if (
                _snapshot_identity(after_root) != expected_identity
                or _snapshot_identity(current_root) != expected_identity
            ):
                raise WorkflowError(
                    "repository Git administrative root changed during snapshot"
                )
        except (SnapshotBudgetExceeded, WorkflowError):
            raise
        except OSError as exc:
            raise WorkflowError(
                f"repository Git administrative directory is unavailable: {exc}"
            ) from exc
        finally:
            for child_descriptor in child_descriptors:
                os.close(child_descriptor)
            if descriptor is not None:
                os.close(descriptor)
    return {
        "git_dir": str(git_dir),
        "common_dir": str(common_dir),
        "root_identities": root_identities,
        "tree_digest": "sha256:" + digest.hexdigest(),
        "entry_count": entry_count,
    }


def _decoded_git_paths(payload: bytes) -> set[str]:
    return {
        raw_path.decode("utf-8", "surrogateescape")
        for raw_path in payload.split(b"\0")
        if raw_path
    }


def _gitlink_index_paths(payload: bytes) -> set[str]:
    paths: set[str] = set()
    for raw_entry in payload.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        if separator and metadata.startswith(b"160000 "):
            paths.add(raw_path.decode("utf-8", "surrogateescape"))
    return paths


def _tracked_index_paths(payload: bytes) -> set[str]:
    paths: set[str] = set()
    for raw_entry in payload.split(b"\0"):
        if not raw_entry:
            continue
        _metadata, separator, raw_path = raw_entry.partition(b"\t")
        if not separator:
            raise WorkflowError("unexpected Git index entry")
        paths.add(raw_path.decode("utf-8", "surrogateescape"))
    return paths


def _snapshot_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _ignored_snapshot_paths(repo: Path, relative_paths: list[str]) -> set[str]:
    if not relative_paths:
        return set()
    payload = b"\0".join(
        ("./" + path).encode("utf-8", "surrogateescape") for path in relative_paths
    ) + b"\0"
    return {
        path.removeprefix("./")
        for path in _decoded_git_paths(
            _git(
                repo,
                "check-ignore",
                "-z",
                "--stdin",
                input_data=payload,
                accepted_returncodes=(0, 1),
            )
        )
    }


def _snapshot_worktree_entries(
    repo: Path,
    *,
    content_paths: set[str],
    untracked_paths: set[str],
    tracked_paths: set[str],
    tracked_prefixes: set[str],
    gitlink_paths: set[str],
    max_untracked_bytes: int | None,
) -> tuple[list[dict[str, Any]], int, int, tuple[int, int, int, int, int, int]]:
    entries: list[dict[str, Any]] = []
    entry_count = 0
    hashed_bytes = 0
    frozen_index_children: dict[str, set[str]] = {}
    for tracked_path in tracked_paths:
        parent = ""
        for name in tracked_path.split("/"):
            frozen_index_children.setdefault(parent, set()).add(name)
            parent = f"{parent}/{name}" if parent else name
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )

    root_fd: int | None = None
    try:
        root_fd = os.open(repo, directory_flags)
        root_info = os.fstat(root_fd)
        root_path_info = repo.lstat()
    except OSError as exc:
        if root_fd is not None:
            os.close(root_fd)
        raise WorkflowError(f"repository snapshot root is unavailable: {exc}") from exc
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or _snapshot_identity(root_info) != _snapshot_identity(root_path_info)
    ):
        os.close(root_fd)
        raise WorkflowError("repository root changed during worktree snapshot")

    def read_regular(
        directory_fd: int,
        name: str,
        relative: str,
        expected: os.stat_result,
    ) -> str:
        nonlocal hashed_bytes
        projected_bytes = hashed_bytes + expected.st_size
        if projected_bytes > SNAPSHOT_HASH_BYTE_LIMIT:
            raise SnapshotBudgetExceeded(
                "content_hash_bytes",
                SNAPSHOT_HASH_BYTE_LIMIT,
                projected_bytes,
            )
        flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _snapshot_identity(opened) != _snapshot_identity(expected)
            ):
                raise WorkflowError(
                    f"regular file changed during repository snapshot: {relative}"
                )
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hashed_bytes += len(block)
                    if hashed_bytes > SNAPSHOT_HASH_BYTE_LIMIT:
                        raise SnapshotBudgetExceeded(
                            "content_hash_bytes",
                            SNAPSHOT_HASH_BYTE_LIMIT,
                            hashed_bytes,
                        )
                    digest.update(block)
                after_read = os.fstat(stream.fileno())
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except SnapshotBudgetExceeded:
            raise
        except WorkflowError:
            raise
        except OSError as exc:
            raise WorkflowError(
                f"repository snapshot could not read regular file {relative}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        expected_identity = _snapshot_identity(expected)
        if (
            _snapshot_identity(after_read) != expected_identity
            or _snapshot_identity(current) != expected_identity
        ):
            raise WorkflowError(
                f"regular file changed during repository snapshot: {relative}"
            )
        return digest.hexdigest()

    def visit(
        directory_fd: int, prefix: str, *, outer_tracked_only: bool = False
    ) -> None:
        nonlocal entry_count
        if (
            prefix
            and not outer_tracked_only
            and _nested_repository_boundary_fd(directory_fd, repo / prefix)
        ):
            outer_tracked_only = True
            if prefix not in frozen_index_children:
                return
        names: list[str] = []
        if outer_tracked_only:
            for name in sorted(
                frozen_index_children.get(prefix, ()), key=os.fsencode
            ):
                entry_count += 1
                if entry_count > SNAPSHOT_WORKTREE_ENTRY_LIMIT:
                    raise SnapshotBudgetExceeded(
                        "worktree_entries",
                        SNAPSHOT_WORKTREE_ENTRY_LIMIT,
                        entry_count,
                    )
                names.append(name)
        else:
            try:
                with os.scandir(directory_fd) as scanner:
                    for directory_entry in scanner:
                        name = directory_entry.name
                        if not prefix and name == ".git":
                            continue
                        entry_count += 1
                        if entry_count > SNAPSHOT_WORKTREE_ENTRY_LIMIT:
                            raise SnapshotBudgetExceeded(
                                "worktree_entries",
                                SNAPSHOT_WORKTREE_ENTRY_LIMIT,
                                entry_count,
                            )
                        names.append(name)
            except SnapshotBudgetExceeded:
                raise
            except OSError as exc:
                location = prefix or "."
                raise WorkflowError(
                    f"repository snapshot could not list worktree directory {location}: {exc}"
                ) from exc

        inspected: list[tuple[str, str, os.stat_result]] = []
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if outer_tracked_only:
                    continue
                raise WorkflowError(
                    f"repository snapshot could not inspect {relative}: path disappeared"
                ) from None
            except OSError as exc:
                raise WorkflowError(
                    f"repository snapshot could not inspect {relative}: {exc}"
                ) from exc
            inspected.append((name, relative, info))
        inspected.sort(key=lambda item: os.fsencode(item[1]))
        ignored_paths = (
            set()
            if outer_tracked_only
            else _ignored_snapshot_paths(
                repo,
                [
                    relative
                    for _name, relative, info in inspected
                    if stat.S_ISDIR(info.st_mode)
                ],
            )
        )

        for name, relative, info in inspected:
            entry: dict[str, Any] = {
                "path": relative,
                "mode": stat.S_IFMT(info.st_mode) | stat.S_IMODE(info.st_mode),
                "device": info.st_dev,
                "inode": info.st_ino,
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns,
            }
            if stat.S_ISLNK(info.st_mode):
                try:
                    entry["link_target"] = os.readlink(name, dir_fd=directory_fd)
                except OSError as exc:
                    raise WorkflowError(
                        f"repository snapshot could not read link {relative}: {exc}"
                    ) from exc
            elif (
                stat.S_ISREG(info.st_mode)
                and relative in content_paths
                and (
                    relative not in untracked_paths
                    or (
                        max_untracked_bytes is not None
                        and info.st_size <= max_untracked_bytes
                    )
                )
            ):
                entry["content_sha256"] = read_regular(
                    directory_fd, name, relative, info
                )
            entries.append(entry)

            if not stat.S_ISDIR(info.st_mode):
                continue
            prunable = relative in gitlink_paths or (
                (
                    name in VENDORED_PARTS | GENERATED_PARTS
                    or relative in ignored_paths
                )
                and relative not in tracked_prefixes
            )
            if prunable:
                continue
            child_fd: int | None = None
            try:
                child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if _snapshot_identity(opened) != _snapshot_identity(info):
                    raise WorkflowError(
                        f"directory changed during repository snapshot: {relative}"
                    )
                visit(
                    child_fd,
                    relative,
                    outer_tracked_only=outer_tracked_only,
                )
                after_visit = os.fstat(child_fd)
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                expected_identity = _snapshot_identity(info)
                if (
                    _snapshot_identity(after_visit) != expected_identity
                    or _snapshot_identity(current) != expected_identity
                ):
                    raise WorkflowError(
                        f"directory changed during repository snapshot: {relative}"
                    )
            except SnapshotBudgetExceeded:
                raise
            except WorkflowError:
                raise
            except OSError as exc:
                raise WorkflowError(
                    f"repository snapshot could not traverse {relative}: {exc}"
                ) from exc
            finally:
                if child_fd is not None:
                    os.close(child_fd)

    try:
        visit(root_fd, "")
        after_root = os.fstat(root_fd)
        current_root = repo.lstat()
        expected_root = _snapshot_identity(root_info)
        if (
            _snapshot_identity(after_root) != expected_root
            or _snapshot_identity(current_root) != expected_root
        ):
            raise WorkflowError("repository root changed during worktree snapshot")
    finally:
        os.close(root_fd)
    return entries, entry_count, hashed_bytes, _snapshot_identity(root_info)


def _repository_state(
    repo: Path,
    *,
    selected_paths: tuple[str, ...] = (),
    max_untracked_bytes: int | None = 1024 * 1024,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    index_before = _git(repo, "ls-files", "--stage", "-z")
    tracked_paths = _tracked_index_paths(index_before)
    if len(tracked_paths) > SNAPSHOT_WORKTREE_ENTRY_LIMIT:
        raise SnapshotBudgetExceeded(
            "worktree_entries",
            SNAPSHOT_WORKTREE_ENTRY_LIMIT,
            len(tracked_paths),
        )
    changed_paths = _decoded_git_paths(
        _git(
            repo,
            "diff",
            "--name-only",
            "-z",
            "--no-ext-diff",
            "--ignore-submodules=none",
        )
    )
    changed_paths.update(
        _decoded_git_paths(
            _git(
                repo,
                "diff",
                "--cached",
                "--name-only",
                "-z",
                "--no-ext-diff",
                "--ignore-submodules=none",
            )
        )
    )
    untracked_paths = _decoded_git_paths(
        _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    )
    candidate_paths = tracked_paths | untracked_paths
    content_paths = changed_paths | untracked_paths
    content_paths.update(path for path in selected_paths if path in candidate_paths)
    content_paths = {
        path
        for path in content_paths
        if not _likely_secret_path(path)
        and not (set(Path(path).parts) & (VENDORED_PARTS | GENERATED_PARTS))
    }
    tracked_prefixes = {
        "/".join(Path(path).parts[:index])
        for path in tracked_paths
        for index in range(1, len(Path(path).parts))
    }
    entries, entry_count, hashed_bytes, root_identity = _snapshot_worktree_entries(
        repo,
        content_paths=content_paths,
        untracked_paths=untracked_paths,
        tracked_paths=tracked_paths,
        tracked_prefixes=tracked_prefixes,
        gitlink_paths=_gitlink_index_paths(index_before),
        max_untracked_bytes=max_untracked_bytes,
    )
    index_after = _git(repo, "ls-files", "--stage", "-z")
    if index_after != index_before:
        raise WorkflowError("repository index changed during worktree snapshot")
    return {
        "entries": entries,
        "worktree_root_identity": root_identity,
        "git_administrative_state": _git_administrative_state(repo),
        "head": _git_optional(repo, "rev-parse", "--verify", "HEAD").decode().strip(),
        "index": index_after.hex(),
        "status": _git(
            repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ).hex(),
        "refs": _git_optional(repo, "show-ref", "--head").hex(),
        "local_config": _git(repo, "config", "--local", "--null", "--list").hex(),
        "snapshot_budget_usage": {
            "worktree_entries": entry_count,
            "content_hash_bytes": hashed_bytes,
        },
    }


def _requested_snapshot_paths(request: dict[str, Any]) -> tuple[str, ...]:
    raw_paths = request.get("context_paths", [])
    if not isinstance(raw_paths, list):
        return ()
    return tuple(path for path in raw_paths if isinstance(path, str))


def _requested_snapshot_max_untracked_bytes(
    request: dict[str, Any],
) -> int | None:
    raw_scope = request.get("scope")
    if not isinstance(raw_scope, dict) or raw_scope.get("kind") != "local":
        return None
    return validate_positive_integer(
        raw_scope.get("max_untracked_bytes", 1024 * 1024),
        "request.scope.max_untracked_bytes",
        error_type=WorkflowError,
    )


def _resolve_scope(repo: Path, raw_scope: object) -> dict[str, Any]:
    scope = _object(raw_scope, "request.scope")
    kind = _string(scope.get("kind"), "request.scope.kind")
    if kind == "local":
        max_bytes = validate_positive_integer(
            scope.get("max_untracked_bytes", 1024 * 1024),
            "request.scope.max_untracked_bytes",
            error_type=WorkflowError,
        )
        return resolve_local(repo, max_bytes)
    if kind == "commit":
        return resolve_commit(
            repo,
            _string(scope.get("revision"), "request.scope.revision"),
            scope.get("parent"),
        )
    if kind == "range":
        return resolve_range(
            repo,
            _string(scope.get("expression"), "request.scope.expression"),
        )
    if kind in {"branch", "pull_request"}:
        base = _string(scope.get("base"), "request.scope.base")
        head = _string(scope.get("head"), "request.scope.head")
        if kind == "pull_request":
            number = validate_positive_integer(
                scope.get("number"),
                "request.scope.number",
                error_type=WorkflowError,
            )
            requested = f"PR #{number}"
            extra = {"pull_request_number": number}
        else:
            requested = f"{base}...{head}"
            extra = None
        return resolve_merge_base_target(
            repo,
            kind=kind,
            source=kind,
            base=base,
            head=head,
            requested=requested,
            extra_target=extra,
        )
    raise WorkflowError(f"unsupported request.scope.kind: {kind}")


def _freeze_context_records(
    repo: Path, scope: dict[str, Any], raw_paths: object
) -> dict[str, Any]:
    paths = _array(raw_paths, "request.context_paths")
    if not paths:
        bound = copy.deepcopy(scope)
        bound["frozen_context_records"] = []
        return bound
    revision = scope["target"].get("head_oid") or scope["target"].get("base_oid")
    if not isinstance(revision, str) or not revision:
        raise WorkflowError("frozen context requires an immutable repository revision")
    changed_paths = {
        path
        for record in scope["changed_records"]
        for path in (record.get("old_path"), record.get("new_path"))
        if isinstance(path, str)
    }
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_path in enumerate(paths):
        path = validate_path_text(
            _string(raw_path, f"request.context_paths[{index}]"),
            f"request.context_paths[{index}]",
            error_type=WorkflowError,
        )
        path_value = Path(path)
        if (
            path_value.is_absolute()
            or path_value.as_posix() != path
            or any(part in {"", ".", ".."} for part in path_value.parts)
        ):
            raise WorkflowError(
                f"request.context_paths[{index}] must be a normalized repository-relative path"
            )
        if path in seen:
            raise WorkflowError("request.context_paths must be unique")
        if path in changed_paths:
            raise WorkflowError(
                f"request.context_paths[{index}] names changed evidence; use a changed record selector"
            )
        seen.add(path)
        raw_entry = _git(
            repo, "--literal-pathspecs", "ls-tree", "-z", revision, "--", path
        )
        entries = [entry for entry in raw_entry.split(b"\0") if entry]
        if len(entries) != 1:
            raise WorkflowError(
                f"request.context_paths[{index}] must name one tracked file at the frozen revision"
            )
        try:
            metadata, raw_resolved_path = entries[0].split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split()
            resolved_path = raw_resolved_path.decode("utf-8", "surrogateescape")
        except (UnicodeError, ValueError) as exc:
            raise WorkflowError("unexpected frozen context tree entry") from exc
        if resolved_path != path or object_type != "blob" or mode not in {"100644", "100755"}:
            raise WorkflowError(
                f"request.context_paths[{index}] must name one regular tracked file"
            )
        payload = _git(repo, "cat-file", "blob", oid)
        content_identity = "sha256:" + hashlib.sha256(payload).hexdigest()
        record_id = "context:sha256:" + hashlib.sha256(
            f"{revision}\0{path}\0{oid}".encode("utf-8", "surrogateescape")
        ).hexdigest()[:20]
        records.append(
            {
                "context_record_id": record_id,
                "source": "frozen_revision",
                "revision": revision,
                "path": path,
                "mode": mode,
                "blob_oid": oid,
                "content_identity": content_identity,
            }
        )
    bound = copy.deepcopy(scope)
    bound["frozen_context_records"] = records
    context_digest = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    bound["scope_id"] = f"{scope['scope_id']}:context:{context_digest}"
    return bound


def _resolve_and_bind_scope(repo: Path, request: dict[str, Any]) -> dict[str, Any]:
    scope = _resolve_scope(repo, request.get("scope"))
    scope = _freeze_context_records(repo, scope, request.get("context_paths", []))
    return _bind_trusted_requirement_sources(scope, request)


def _bind_trusted_requirement_sources(
    scope: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, raw_source in enumerate(
        _array(
            request.get("trusted_requirement_sources", []),
            "request.trusted_requirement_sources",
        )
    ):
        source = copy.deepcopy(
            _object(raw_source, f"request.trusted_requirement_sources[{index}]")
        )
        if set(source) != {"source_id", "kind", "reference", "summary"}:
            raise WorkflowError(
                f"request.trusted_requirement_sources[{index}] must contain source_id, kind, reference, and summary"
            )
        source_id = _string(
            source["source_id"],
            f"request.trusted_requirement_sources[{index}].source_id",
        )
        for field in ("kind", "reference", "summary"):
            _string(
                source[field],
                f"request.trusted_requirement_sources[{index}].{field}",
            )
        if source_id in source_ids:
            raise WorkflowError("trusted requirement source IDs must be unique")
        source_ids.add(source_id)
        sources.append(source)
    bound = copy.deepcopy(scope)
    bound["trusted_requirement_sources"] = sources
    evidence_digest = hashlib.sha256(
        json.dumps(
            {
                "trusted_requirement_sources": sources,
                "candidate_validation_commands": bound[
                    "candidate_validation_commands"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    bound["scope_id"] = f"{scope['scope_id']}:evidence:{evidence_digest}"
    return bound


def _record_path(record: dict[str, Any]) -> str | None:
    return record.get("new_path") or record.get("old_path") or record["anchor"].get("path")


def _select_record_ids(
    scope: dict[str, Any], raw_selectors: object, name: str
) -> list[str]:
    selectors = _array(raw_selectors, name)
    selected: list[str] = []
    for index, raw_selector in enumerate(selectors):
        selector = _object(raw_selector, f"{name}[{index}]")
        allowed = {"record_id", "path", "source", "change_type", "anchor_kind", "side", "line"}
        unknown = set(selector) - allowed
        if unknown:
            raise WorkflowError(
                f"{name}[{index}] has unsupported selectors: {', '.join(sorted(unknown))}"
            )
        matches: list[str] = []
        for record in scope["changed_records"]:
            anchor = record["anchor"]
            checks = {
                "record_id": record["record_id"],
                "path": _record_path(record),
                "source": record["source"],
                "change_type": record["change_type"],
                "anchor_kind": anchor["kind"],
                "side": anchor.get("side"),
                "line": anchor.get("line"),
            }
            if all(checks[key] == expected for key, expected in selector.items()):
                matches.append(record["record_id"])
        if not matches:
            raise WorkflowError(f"{name}[{index}] did not match frozen changed evidence")
        selected.extend(matches)
    if len(selected) != len(set(selected)):
        raise WorkflowError(f"{name} selects a changed record more than once")
    return selected


def _build_feature_graph(scope: dict[str, Any], raw_partition: object) -> dict[str, Any]:
    partition = _object(raw_partition, "partition")
    features = copy.deepcopy(_array(partition.get("features"), "partition.features"))
    raw_units = _array(partition.get("review_units"), "partition.review_units")
    units: list[dict[str, Any]] = []
    ownership: dict[str, str] = {}
    for index, raw_unit in enumerate(raw_units):
        source = _object(raw_unit, f"partition.review_units[{index}]")
        unit_id = _string(source.get("unit_id"), f"partition.review_units[{index}].unit_id")
        owned = _select_record_ids(
            scope,
            source.get("owned", []),
            f"partition.review_units[{index}].owned",
        )
        context = _select_record_ids(
            scope,
            source.get("context", []),
            f"partition.review_units[{index}].context",
        )
        duplicate = set(ownership) & set(owned)
        if duplicate:
            raise WorkflowError(
                "partition assigns changed records to multiple primary owners: "
                + ", ".join(sorted(duplicate))
            )
        ownership.update({record_id: unit_id for record_id in owned})
        unit = {
            key: copy.deepcopy(value)
            for key, value in source.items()
            if key not in {"owned", "context"}
        }
        unit.update(
            {
                "changed_record_ids": owned,
                "context_record_ids": context,
                "executable": True,
            }
        )
        units.append(unit)
    all_record_ids = {record["record_id"] for record in scope["changed_records"]}
    if set(ownership) != all_record_ids:
        missing = sorted(all_record_ids - set(ownership))
        raise WorkflowError(
            "partition must assign every changed record exactly once; missing: "
            + ", ".join(missing)
        )
    return {
        "schema_version": "2.0",
        "scope_id": scope["scope_id"],
        "features": features,
        "review_units": units,
        "ownership": ownership,
        "edges": copy.deepcopy(_array(partition.get("edges", []), "partition.edges")),
    }


def _candidate_from_control(
    raw_candidate: object, scope: dict[str, Any], unit_record_ids: set[str], name: str
) -> dict[str, Any]:
    candidate = copy.deepcopy(_object(raw_candidate, name))
    if "anchor_selector" in candidate:
        selected = _select_record_ids(scope, [candidate.pop("anchor_selector")], f"{name}.anchor_selector")
        if len(selected) != 1 or selected[0] not in unit_record_ids:
            raise WorkflowError(f"{name}.anchor_selector must identify one owned record")
        record = next(
            record for record in scope["changed_records"] if record["record_id"] == selected[0]
        )
        candidate["changed_record_id"] = selected[0]
        candidate["anchor"] = copy.deepcopy(record["anchor"])
    return candidate


def _result_from_control(
    envelope: dict[str, Any], raw_attempt: dict[str, Any], scope: dict[str, Any]
) -> dict[str, Any]:
    if "raw_result" in raw_attempt:
        return copy.deepcopy(_object(raw_attempt["raw_result"], "attempt.raw_result"))
    unit_record_ids = set(envelope["changed_record_ids"])
    candidates = [
        _candidate_from_control(candidate, scope, unit_record_ids, f"candidate_findings[{index}]")
        for index, candidate in enumerate(raw_attempt.get("candidate_findings", []))
    ]
    inspected = raw_attempt.get("inspected_record_ids", envelope["changed_record_ids"])
    return {
        "schema_version": "2.0",
        "task_id": envelope["task_id"],
        "scope_id": envelope["scope_id"],
        "unit_id": envelope["unit_id"],
        "role": envelope["role"],
        "status": raw_attempt.get("status", "completed"),
        "inspected_record_ids": copy.deepcopy(inspected),
        "candidate_findings": candidates,
        "supporting_evidence": copy.deepcopy(raw_attempt.get("supporting_evidence", [])),
        "proposed_checks": copy.deepcopy(raw_attempt.get("proposed_checks", [])),
        "unresolved_assumptions": copy.deepcopy(raw_attempt.get("unresolved_assumptions", [])),
    }


def _find_envelope(bundle: dict[str, Any], unit_id: str, role: str) -> dict[str, Any]:
    matches = [
        envelope
        for envelope in bundle["reviewer_task_envelopes"]
        if envelope["unit_id"] == unit_id and envelope["role"] == role
    ]
    if len(matches) != 1:
        raise WorkflowError(f"outcome does not identify one planned role: {unit_id}/{role}")
    return copy.deepcopy(matches[0])


def _run_outcomes(
    bundle: dict[str, Any], scope: dict[str, Any], raw_outcomes: object
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    outcomes = _array(raw_outcomes, "reviewer_outcomes")
    expected_roles = {
        (envelope["unit_id"], envelope["role"])
        for envelope in bundle["reviewer_task_envelopes"]
    }
    supplied_roles: set[tuple[str, str]] = set()
    observed_retries: list[dict[str, Any]] = []
    lifecycle_gaps: list[dict[str, Any]] = []
    context_ids: set[str] = set()
    isolation_evidence: list[dict[str, Any]] = []
    proposed_checks: list[dict[str, Any]] = []
    current = bundle
    for outcome_index, raw_outcome in enumerate(outcomes):
        control = _object(raw_outcome, f"reviewer_outcomes[{outcome_index}]")
        unit_id = _string(control.get("unit_id"), f"reviewer_outcomes[{outcome_index}].unit_id")
        role = _string(control.get("role"), f"reviewer_outcomes[{outcome_index}].role")
        if role not in ROLES:
            raise WorkflowError(f"reviewer_outcomes[{outcome_index}].role is unsupported")
        role_key = (unit_id, role)
        if role_key in supplied_roles:
            raise WorkflowError(f"duplicate reviewer outcome for {unit_id}/{role}")
        supplied_roles.add(role_key)
        attempts = _array(control.get("attempts"), f"reviewer_outcomes[{outcome_index}].attempts")
        if not 1 <= len(attempts) <= 2:
            raise WorkflowError("a reviewer role requires one or two controlled attempts")
        envelope = _find_envelope(current, unit_id, role)
        for attempt_index, raw_attempt in enumerate(attempts):
            attempt = _object(raw_attempt, f"reviewer_outcomes[{outcome_index}].attempts[{attempt_index}]")
            context_id = _string(
                attempt.get("reviewer_context_id"),
                f"reviewer_outcomes[{outcome_index}].attempts[{attempt_index}].reviewer_context_id",
            )
            if attempt.get("isolated_context") is not True:
                raise WorkflowError("every reviewer attempt must attest an isolated context")
            if context_id in context_ids:
                raise WorkflowError("reviewer context IDs must be distinct across roles and retries")
            context_ids.add(context_id)
            isolation_evidence.append(
                {
                    "unit_id": unit_id,
                    "role": role,
                    "attempt": attempt_index + 1,
                    "reviewer_context_id": context_id,
                    "isolated_context": True,
                }
            )
            outcome = _string(attempt.get("outcome"), "attempt.outcome")
            event: dict[str, Any] = {
                "unit_id": unit_id,
                "role": role,
                "task_id": envelope["task_id"],
                "outcome": outcome,
            }
            if outcome == "result":
                event["reviewer_result"] = _result_from_control(envelope, attempt, scope)
            advanced = advance_review({"bundle": current, "event": event})
            current = advanced["bundle"]
            lifecycle_gaps.extend(advanced.get("verification_gaps", []))
            proposed_checks.extend(advanced.get("proposed_checks", []))
            if advanced.get("status") == "repreflight_required":
                return (
                    current,
                    observed_retries,
                    lifecycle_gaps,
                    isolation_evidence,
                    proposed_checks,
                    {
                        "status": "repreflight_required",
                        "required_action": advanced["required_action"],
                    },
                )
            retry = advanced.get("retry_task_envelope")
            if retry is not None:
                observed_retries.append(
                    {
                        "unit_id": unit_id,
                        "role": role,
                        "attempt": retry["attempt"],
                        "fresh_context_required": retry["fresh_context_required"],
                    }
                )
                envelope = retry
            elif attempt_index + 1 < len(attempts):
                raise WorkflowError(f"unexpected extra attempt for {unit_id}/{role}")
        if retry is not None:
            raise WorkflowError(f"controlled outcome omitted required retry for {unit_id}/{role}")
    if supplied_roles != expected_roles:
        missing = [f"{unit}/{role}" for unit, role in sorted(expected_roles - supplied_roles)]
        raise WorkflowError("controlled outcomes omitted planned roles: " + ", ".join(missing))
    return (
        current,
        observed_retries,
        lifecycle_gaps,
        isolation_evidence,
        proposed_checks,
        None,
    )


def _bind_candidate_decisions(
    bundle: dict[str, Any],
    scope: dict[str, Any],
    raw_decisions: object,
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions = _array(raw_decisions, "candidate_decisions")
    accepted = bundle.get("review_lifecycle", {}).get("accepted_results", [])
    candidate_context: dict[tuple[str, str, str], dict[str, Any]] = {}
    for result in accepted:
        for candidate in result["candidate_findings"]:
            candidate_context[
                (result["unit_id"], result["role"], candidate["finding_id"])
            ] = {
                "task_id": result["task_id"],
                "inspected_record_ids": set(result["inspected_record_ids"]),
            }
    checks_by_id = {check["proposal_id"]: check for check in checks}
    trusted_requirement_ids = {
        source["source_id"] for source in scope["trusted_requirement_sources"]
    }
    bound: list[dict[str, Any]] = []
    for index, raw_decision in enumerate(decisions):
        decision = copy.deepcopy(_object(raw_decision, f"candidate_decisions[{index}]"))
        key = (
            _string(decision.pop("unit_id", None), f"candidate_decisions[{index}].unit_id"),
            _string(decision.pop("role", None), f"candidate_decisions[{index}].role"),
            _string(decision.get("finding_id"), f"candidate_decisions[{index}].finding_id"),
        )
        context = candidate_context.get(key)
        if context is None:
            raise WorkflowError(f"candidate_decisions[{index}] does not name an accepted candidate")
        evidence_items = _array(
            decision.get("verification_evidence"),
            f"candidate_decisions[{index}].verification_evidence",
        )
        bound_evidence: list[str] = []
        for evidence_index, raw_evidence in enumerate(evidence_items):
            evidence_name = (
                f"candidate_decisions[{index}].verification_evidence[{evidence_index}]"
            )
            evidence = _object(raw_evidence, evidence_name)
            kind = _string(evidence.get("kind"), f"{evidence_name}.kind")
            detail = _string(evidence.get("detail"), f"{evidence_name}.detail")
            if kind == "context_code":
                if set(evidence) == {"kind", "context_record_id", "detail"}:
                    context_record_id = _string(
                        evidence["context_record_id"],
                        f"{evidence_name}.context_record_id",
                    )
                    matches = [
                        record
                        for record in scope.get("frozen_context_records", [])
                        if record.get("context_record_id") == context_record_id
                    ]
                elif set(evidence) == {"kind", "context_selector", "detail"}:
                    selector = _object(
                        evidence["context_selector"], f"{evidence_name}.context_selector"
                    )
                    allowed = {"context_record_id", "path", "content_identity"}
                    if not selector or set(selector) - allowed:
                        raise WorkflowError(
                            f"{evidence_name}.context_selector has unsupported fields"
                        )
                    matches = [
                        record
                        for record in scope.get("frozen_context_records", [])
                        if all(record.get(key) == expected for key, expected in selector.items())
                    ]
                else:
                    raise WorkflowError(
                        f"{evidence_name} must bind context code evidence to one frozen context record"
                    )
                if len(matches) != 1:
                    raise WorkflowError(
                        f"{evidence_name} must bind code evidence to one frozen context record"
                    )
                bound_evidence.append(detail)
                continue
            if kind == "code":
                if set(evidence) == {"kind", "changed_record_id", "detail"}:
                    selected = [
                        _string(
                            evidence["changed_record_id"],
                            f"{evidence_name}.changed_record_id",
                        )
                    ]
                elif set(evidence) == {"kind", "record_selector", "detail"}:
                    selected = _select_record_ids(
                        scope,
                        [evidence["record_selector"]],
                        f"{evidence_name}.record_selector",
                    )
                else:
                    raise WorkflowError(
                        f"{evidence_name} must bind code evidence to one frozen changed record"
                    )
                if (
                    len(selected) != 1
                    or not any(
                        record["record_id"] == selected[0]
                        for record in scope["changed_records"]
                    )
                    or selected[0] not in context["inspected_record_ids"]
                ):
                    raise WorkflowError(
                        f"{evidence_name} must bind code evidence to one inspected frozen record"
                    )
                bound_evidence.append(detail)
                continue
            if kind == "requirement":
                if set(evidence) != {"kind", "source_id", "detail"}:
                    raise WorkflowError(
                        f"{evidence_name} must bind requirement evidence to one trusted source_id"
                    )
                source_id = _string(
                    evidence["source_id"], f"{evidence_name}.source_id"
                )
                if source_id not in trusted_requirement_ids:
                    raise WorkflowError(
                        f"{evidence_name} must bind requirement evidence to a trusted requirement source"
                    )
                bound_evidence.append(detail)
                continue
            if kind != "validation":
                raise WorkflowError(f"{evidence_name}.kind is unsupported")
            if set(evidence) != {
                "kind",
                "proposal_id",
                "expected_status",
                "detail",
            }:
                raise WorkflowError(
                    f"{evidence_name} has unsupported validation evidence fields"
                )
            proposal_id = _string(
                evidence.get("proposal_id"), f"{evidence_name}.proposal_id"
            )
            expected_status = _string(
                evidence.get("expected_status"), f"{evidence_name}.expected_status"
            )
            check = checks_by_id.get(proposal_id)
            if (
                check is None
                or check["screening"] != "approved"
                or check["status"] == "not_run"
            ):
                raise WorkflowError(
                    f"{evidence_name} references a validation without an approved controlled result"
                )
            if check["status"] != expected_status:
                raise WorkflowError(
                    f"{evidence_name} expected {expected_status} but controlled validation was {check['status']}"
                )
            bound_evidence.append(detail)
        decision["verification_evidence"] = bound_evidence
        decision["task_id"] = context["task_id"]
        bound.append(decision)
    return bound


def _materialize_finalization_decisions(
    scope: dict[str, Any], raw_decisions: object
) -> list[dict[str, Any]]:
    decisions = copy.deepcopy(_array(raw_decisions, "candidate_decisions"))
    for decision_index, raw_decision in enumerate(decisions):
        decision = _object(raw_decision, f"candidate_decisions[{decision_index}]")
        evidence_items = _array(
            decision.get("verification_evidence"),
            f"candidate_decisions[{decision_index}].verification_evidence",
        )
        for evidence_index, raw_evidence in enumerate(evidence_items):
            name = (
                f"candidate_decisions[{decision_index}].verification_evidence"
                f"[{evidence_index}]"
            )
            evidence = _object(raw_evidence, name)
            kind = evidence.get("kind")
            if kind == "code" and "record_selector" in evidence:
                selected = _select_record_ids(
                    scope, [evidence["record_selector"]], f"{name}.record_selector"
                )
                if len(selected) != 1:
                    raise WorkflowError(
                        f"{name} must bind code evidence to one frozen changed record"
                    )
                evidence.pop("record_selector")
                evidence["changed_record_id"] = selected[0]
            elif kind == "context_code" and "context_selector" in evidence:
                selector = _object(
                    evidence["context_selector"], f"{name}.context_selector"
                )
                allowed = {"context_record_id", "path", "content_identity"}
                if not selector or set(selector) - allowed:
                    raise WorkflowError(
                        f"{name}.context_selector has unsupported fields"
                    )
                matches = [
                    record
                    for record in scope.get("frozen_context_records", [])
                    if all(
                        record.get(key) == expected
                        for key, expected in selector.items()
                    )
                ]
                if len(matches) != 1:
                    raise WorkflowError(
                        f"{name} must bind context code evidence to one frozen context record"
                    )
                evidence.pop("context_selector")
                evidence["context_record_id"] = matches[0]["context_record_id"]
    return decisions


def _format_anchor(anchor: dict[str, Any]) -> str:
    ordered_fields = ["kind"]
    if anchor["kind"] == "line":
        ordered_fields.extend(("side", "path", "line"))
    else:
        ordered_fields.extend(
            (
                "change_type",
                "path",
                "old_path",
                "new_path",
                "old_mode",
                "new_mode",
                "old_oid",
                "new_oid",
            )
        )
    fields = []
    for field in ordered_fields:
        if field not in anchor:
            continue
        value = "null" if anchor[field] is None else anchor[field]
        fields.append(f"{field}={_markdown_text(value)}")
    path = _markdown_text(anchor.get("path", "unknown"))
    if anchor["kind"] == "line":
        suffix = " (deleted)" if anchor.get("side") == "old" else ""
        location = f"{path}:{anchor['line']}{suffix}"
    else:
        location = f"{path} ({anchor['kind']})"
    return location + "; " + "; ".join(fields)


def _markdown_text(value: object) -> str:
    text = " ".join(str(value).split())
    escaped = (
        text.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    for marker in ("#", "*", "_", "[", "]", "|"):
        escaped = escaped.replace(marker, f"\\{marker}")
    return escaped


def _render_markdown(
    final: dict[str, Any],
    scope: dict[str, Any],
    checks: list[Any],
    phase_boundary: dict[str, Any],
) -> str:
    findings = final["verified_findings"]
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    confidence_order = {"high": 0, "medium": 1}
    findings = sorted(
        findings,
        key=lambda item: (
            severity_order.get(item["severity"], 99),
            confidence_order.get(item["confidence"], 99),
            item["title"],
        ),
    )
    lines: list[str] = []
    if not findings:
        lines.extend(["No findings", ""])
    for finding in findings:
        lines.extend(
            [
                f"## [{finding['severity'].title()}] {_markdown_text(finding['title'])}",
                "",
                f"- Confidence: {finding['confidence']}",
                f"- Changed record ID: {_markdown_text(finding['changed_record_id'])}",
                f"- Anchor: {_format_anchor(finding['anchor'])}",
                f"- Scenario: {_markdown_text(finding['reachable_scenario'])}",
                f"- Impact: {_markdown_text(finding['impact'])}",
                "- Evidence: "
                + "; ".join(_markdown_text(item) for item in finding["verification_evidence"]),
                f"- Direction: {_markdown_text(finding['correction_direction'])}",
                "",
            ]
        )
    if final["questions"]:
        lines.extend(["## Questions", ""])
        for question in final["questions"]:
            assumption = question.get("assumption")
            assumption_text = (
                assumption.get("text") if isinstance(assumption, dict) else None
            )
            text = question.get("title") or assumption_text or str(question)
            lines.append(f"- {_markdown_text(text)}")
        lines.append("")
    lines.extend(["## Checks", ""])
    if checks:
        for check in checks:
            item = _object(check, "checks[]")
            lines.append(
                f"- {_markdown_text(item.get('status', 'not_run'))}: "
                f"{_markdown_text(item.get('name', item.get('command', 'check')))}; "
                f"reason: {_markdown_text(item.get('evidence', 'not supplied'))}"
            )
    else:
        lines.append("- Not run")
    target = scope["target"]
    lines.extend(
        [
            "",
            "## Scope",
            "",
            f"- Scope ID: {_markdown_text(scope['scope_id'])}",
            f"- Target kind: {_markdown_text(target['kind'])}",
            "- Requested comparison: "
            + _markdown_text(
                target.get("requested", "local staged, unstaged, and safe untracked changes")
            ),
            f"- Semantics: {_markdown_text(target.get('semantics', 'working-tree-snapshot'))}",
            f"- Base OID: {_markdown_text(target['base_oid'])}",
            "- Head OID: "
            + _markdown_text(target.get("head_oid", target.get("head", "unknown"))),
        ]
    )
    optional_target_fields = (
        ("requested_base", "Requested base ref"),
        ("requested_head", "Requested head ref"),
        ("requested_base_oid", "Requested base OID"),
        ("operator", "Range operator"),
        ("pull_request_number", "Pull request"),
        ("parent_count", "Parent count"),
    )
    for field, label in optional_target_fields:
        if field in target:
            lines.append(f"- {label}: {_markdown_text(target[field])}")
    if scope["exclusions"]:
        lines.append("- Exclusions:")
        for exclusion in scope["exclusions"]:
            lines.append(
                f"  - {_markdown_text(exclusion.get('path', 'unknown'))}: "
                f"{_markdown_text(exclusion.get('reason', 'excluded'))}"
            )
    else:
        lines.append("- Exclusions: none")
    authorized_followup = phase_boundary.get("authorized_followup")
    lines.extend(
        [
            "",
            "## Phase Boundary",
            "",
            f"- Review: {_markdown_text(phase_boundary['review'])}",
            "- Authorized follow-up: "
            + _markdown_text(
                authorized_followup if authorized_followup is not None else "none"
            ),
            f"- Follow-up state: {_markdown_text(phase_boundary['followup_state'])}",
        ]
    )
    ledger = final["coverage_ledger"]
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- Pair status: {final['reviewer_pair_status']}",
            f"- Covered records: {sum(1 for record in ledger['records'] if record['covered'])}/{len(ledger['records'])}",
        ]
    )
    for unit in ledger.get("units", []):
        states = ", ".join(
            f"{role}={unit['role_status'][role]}" for role in ROLES
        )
        lines.append(f"- {_markdown_text(unit['unit_id'])}: {_markdown_text(states)}")
    if ledger.get("uncovered_record_ids"):
        lines.append(
            "- Uncovered records: "
            + ", ".join(_markdown_text(item) for item in ledger["uncovered_record_ids"])
        )
    if final["verification_gaps"]:
        lines.extend(["", "## Verification Gaps", ""])
        for gap in final["verification_gaps"]:
            title = gap.get("title") or gap.get("kind", "gap")
            reason = gap.get("main_agent_reason") or gap.get("reason") or "unresolved"
            provenance = "/".join(
                str(value)
                for value in (gap.get("unit_id"), gap.get("role"))
                if value
            )
            suffix = f" [{_markdown_text(provenance)}]" if provenance else ""
            lines.append(
                f"- {_markdown_text(title)}: {_markdown_text(reason)}{suffix}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _canonical_artifact_path(path: Path, name: str) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise WorkflowError(f"{name} parent is invalid: {exc}") from exc
    return parent / candidate.name


def _read_stable_regular_artifact(path: Path, name: str) -> tuple[Path, bytes]:
    canonical = _canonical_artifact_path(path, name)
    try:
        before = canonical.lstat()
    except OSError as exc:
        raise WorkflowError(f"{name} is invalid: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise WorkflowError(f"{name} must be a regular file, not a link or special file")
    if before.st_nlink != 1:
        raise WorkflowError(f"{name} must have exactly one filesystem link")
    if before.st_size > CONTROL_ARTIFACT_MAX_BYTES:
        raise WorkflowError(f"{name} exceeds the bounded size limit")
    if hasattr(os, "geteuid") and before.st_uid != os.geteuid():
        raise WorkflowError(f"{name} must be owned by the current user")
    if stat.S_IMODE(before.st_mode) & 0o022:
        raise WorkflowError(f"{name} must not be group- or world-writable")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(canonical, flags)
        opened = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if identity != opened_identity or not stat.S_ISREG(opened.st_mode):
            raise WorkflowError(f"{name} changed before it could be read safely")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(CONTROL_ARTIFACT_MAX_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        if len(payload) > CONTROL_ARTIFACT_MAX_BYTES:
            raise WorkflowError(f"{name} exceeds the bounded size limit")
        after_path = canonical.lstat()
        final_identity = (
            after_open.st_dev,
            after_open.st_ino,
            after_open.st_mode,
            after_open.st_nlink,
            after_open.st_size,
            after_open.st_mtime_ns,
            after_open.st_ctime_ns,
        )
        path_identity = (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_mode,
            after_path.st_nlink,
            after_path.st_size,
            after_path.st_mtime_ns,
            after_path.st_ctime_ns,
        )
        if identity != final_identity or identity != path_identity:
            raise WorkflowError(f"{name} changed while it was being read")
        return canonical, payload
    except WorkflowError:
        raise
    except OSError as exc:
        raise WorkflowError(f"{name} could not be read safely: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_provider_consent(path: Path) -> dict[str, Any]:
    resolved, payload = _read_stable_regular_artifact(
        path, "provider consent artifact"
    )
    try:
        value = _object(
            json.loads(payload.decode("utf-8")), "provider consent artifact"
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"provider consent artifact is invalid: {exc}") from exc
    required = {
        "schema_version",
        "capability",
        "consent_id",
        "repository",
        "provider",
    }
    if set(value) != required:
        raise WorkflowError("provider consent artifact has an invalid schema")
    if value["schema_version"] != SCHEMA_VERSION:
        raise WorkflowError(f"provider consent schema_version must be {SCHEMA_VERSION}")
    if value["capability"] != "external_provider_disclosure":
        raise WorkflowError("provider consent artifact has an unsupported capability")
    for field in ("consent_id", "repository", "provider"):
        _string(value[field], f"provider consent artifact.{field}")
    return {**copy.deepcopy(value), "_artifact_path": str(resolved)}


def _screen_safety_attempts(
    raw_attempts: object,
    environment: dict[str, Any],
    repo: Path,
    provider_consent: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if provider_consent is not None:
        artifact_path = Path(provider_consent["_artifact_path"])
        git_dir = Path(
            _git(repo, "rev-parse", "--absolute-git-dir").decode().strip()
        ).resolve()
        common_dir_value = Path(
            _git(repo, "rev-parse", "--git-common-dir").decode().strip()
        )
        common_dir = (
            common_dir_value.resolve()
            if common_dir_value.is_absolute()
            else (repo / common_dir_value).resolve()
        )
        repository_owned_roots = {repo, git_dir, common_dir}
        if any(
            artifact_path == root or artifact_path.is_relative_to(root)
            for root in repository_owned_roots
        ):
            raise WorkflowError(
                "provider consent artifact must be outside the reviewed repository and its Git administrative directories"
            )
        if Path(provider_consent["repository"]).resolve() != repo:
            raise WorkflowError(
                "provider consent artifact is bound to another repository"
            )

    decisions: list[dict[str, Any]] = []
    for index, raw_attempt in enumerate(_array(raw_attempts, "safety_attempts")):
        attempt = copy.deepcopy(_object(raw_attempt, f"safety_attempts[{index}]"))
        kind = _string(attempt.get("kind"), f"safety_attempts[{index}].kind")
        if kind not in BLOCKED_SAFETY_ATTEMPTS:
            raise WorkflowError(f"safety_attempts[{index}].kind is unsupported")
        if kind == "external_provider" and provider_consent is not None:
            provider = _string(
                attempt.get("provider"), f"safety_attempts[{index}].provider"
            )
            if provider_consent["provider"] != provider:
                raise WorkflowError(
                    "provider consent artifact is bound to another provider"
                )
            decisions.append(
                {
                    "attempt": attempt,
                    "decision": "allowed_disclosure",
                    "reason": "out_of_band_provider_consent",
                    "consent_id": provider_consent["consent_id"],
                    "executed": False,
                }
            )
            continue
        decisions.append(
            {
                "attempt": attempt,
                "decision": "blocked",
                "reason": BLOCKED_SAFETY_ATTEMPTS[kind],
                "executed": False,
            }
        )
    return decisions


def _screen_and_bind_validations(
    proposals: list[dict[str, Any]],
    trusted_commands: list[dict[str, Any]],
    raw_results: object,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    proposal_ids = [
        _string(
            _object(wrapper, f"proposals[{index}]")
            .get("proposal", {})
            .get("proposal_id"),
            f"proposals[{index}].proposal.proposal_id",
        )
        for index, wrapper in enumerate(proposals)
    ]
    if len(proposal_ids) != len(set(proposal_ids)):
        raise WorkflowError("proposal IDs must be globally unique")
    screening = screen_validations(
        {"proposals": proposals, "trusted_commands": trusted_commands}
    )
    approved = {
        proposal["proposal_id"]: proposal for proposal in screening["approved"]
    }
    rejected = {
        item["proposal_id"]: item["reason"] for item in screening["rejected"]
    }
    results: dict[str, dict[str, Any]] = {}
    for index, raw_result in enumerate(
        _array(raw_results, "validation_results")
    ):
        result = copy.deepcopy(
            _object(raw_result, f"validation_results[{index}]")
        )
        if set(result) != {"proposal_id", "status", "evidence"}:
            raise WorkflowError(
                f"validation_results[{index}] must contain proposal_id, status, and evidence"
            )
        proposal_id = _string(
            result["proposal_id"], f"validation_results[{index}].proposal_id"
        )
        if proposal_id not in approved:
            raise WorkflowError(
                f"validation result {proposal_id} is not approved by validation screening"
            )
        if proposal_id in results:
            raise WorkflowError("validation results must name each proposal at most once")
        if result["status"] not in {"passed", "failed", "inconclusive"}:
            raise WorkflowError(
                f"validation_results[{index}].status is unsupported"
            )
        _string(result["evidence"], f"validation_results[{index}].evidence")
        results[proposal_id] = result
    checks: list[dict[str, Any]] = []
    for proposal_id, proposal in approved.items():
        result = results.get(proposal_id)
        checks.append(
            {
                "proposal_id": proposal_id,
                "name": " ".join(proposal["argv"]),
                "status": result["status"] if result is not None else "not_run",
                "evidence": (
                    result["evidence"]
                    if result is not None
                    else "Approved but no controlled execution result was supplied"
                ),
                "screening": "approved",
            }
        )
    proposals_by_id = {
        wrapper["proposal"]["proposal_id"]: wrapper["proposal"]
        for wrapper in proposals
    }
    for proposal_id, reason in rejected.items():
        proposal = proposals_by_id[proposal_id]
        checks.append(
            {
                "proposal_id": proposal_id,
                "name": " ".join(proposal["argv"]),
                "status": "not_run",
                "evidence": reason,
                "screening": "rejected",
            }
        )
    screening = {
        **screening,
        "controlled_results": len(results),
        "trusted_command_evidence": copy.deepcopy(trusted_commands),
    }
    return screening, checks


def _authorized_command_records(
    raw_commands: object, name: str, trust_source: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    required = {
        "argv",
        "requires_network",
        "requires_credentials",
        "writes_repository",
    }
    for index, raw_command in enumerate(_array(raw_commands, name)):
        command = _object(raw_command, f"{name}[{index}]")
        if set(command) != required:
            raise WorkflowError(
                f"{name}[{index}] must contain argv and authoritative network, credential, and repository-write effects"
            )
        argv = _array(command["argv"], f"{name}[{index}].argv")
        if not argv:
            raise WorkflowError(f"{name}[{index}].argv cannot be empty")
        normalized = [
            _string(argument, f"{name}[{index}].argv[{argument_index}]")
            for argument_index, argument in enumerate(argv)
        ]
        effects: dict[str, bool] = {}
        for field in (
            "requires_network",
            "requires_credentials",
            "writes_repository",
        ):
            if not isinstance(command[field], bool):
                raise WorkflowError(f"{name}[{index}].{field} must be a boolean")
            effects[field] = command[field]
        records.append(
            {"argv": normalized, "trust_source": trust_source, **effects}
        )
    return records


def _trusted_validation_commands(
    repo: Path,
    scope: dict[str, Any],
    request: dict[str, Any],
    environment: dict[str, Any],
    raw_self_attested: object,
) -> list[dict[str, Any]]:
    self_attested = _array(raw_self_attested, "trusted_commands")
    if self_attested:
        raise WorkflowError(
            "caller-supplied trusted_commands are not authoritative; use frozen base, explicit user, or host policy authorization"
        )
    for name, raw_commands in (
        (
            "request.authorized_validation_commands",
            request.get("authorized_validation_commands", []),
        ),
        (
            "environment.host_authorized_validation_commands",
            environment.get("host_authorized_validation_commands", []),
        ),
    ):
        if _array(raw_commands, name):
            raise WorkflowError(
                f"{name}: in-band command authority is not trusted; the evaluator accepts only the immutable frozen-base registry"
            )
    records: list[dict[str, Any]] = []
    base_oid = scope["target"].get("base_oid")
    if isinstance(base_oid, str) and base_oid:
        tree_entry = _git(
            repo, "ls-tree", "-z", base_oid, "--", BASE_VALIDATION_POLICY
        )
        if tree_entry:
            try:
                policy = json.loads(
                    _git(
                        repo, "show", f"{base_oid}:{BASE_VALIDATION_POLICY}"
                    ).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WorkflowError(
                    f"frozen base validation policy is invalid: {exc}"
                ) from exc
            policy = _object(policy, "frozen base validation policy")
            if set(policy) != {"schema_version", "commands"}:
                raise WorkflowError(
                    "frozen base validation policy must contain schema_version and commands"
                )
            if policy["schema_version"] != SCHEMA_VERSION:
                raise WorkflowError(
                    f"frozen base validation policy schema_version must be {SCHEMA_VERSION}"
                )
            records.extend(
                _authorized_command_records(
                    policy["commands"],
                    "frozen base validation policy.commands",
                    "base_revision",
                )
            )
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in records:
        key = tuple(record["argv"])
        existing = unique.get(key)
        if existing is not None and any(
            existing[field] != record[field]
            for field in (
                "requires_network",
                "requires_credentials",
                "writes_repository",
            )
        ):
            raise WorkflowError(
                "frozen base validation policy contains conflicting authoritative metadata for one command"
            )
        unique.setdefault(key, record)
    return list(unique.values())


def _apply_hard_limits(
    bundle: dict[str, Any], environment: dict[str, Any]
) -> dict[str, Any]:
    if "hard_limits" not in environment:
        return bundle
    hard_limits = _object(environment["hard_limits"], "environment.hard_limits")
    supported = {
        "max_executable_units": ("graph_executable_units", "hard_executable_unit_limit_unsatisfied"),
        "max_reviewer_passes": ("paired_reviewer_passes", "hard_reviewer_pass_limit_unsatisfied"),
        "max_waves": ("waves", "hard_wave_limit_unsatisfied"),
    }
    unknown = set(hard_limits) - set(supported)
    if unknown:
        raise WorkflowError(
            "environment.hard_limits contains unsupported limits: "
            + ", ".join(sorted(unknown))
        )
    preflight = bundle["review_plan"]["preflight"]
    reasons: list[str] = []
    for name, (observed_field, reason) in supported.items():
        if name not in hard_limits:
            continue
        limit = hard_limits[name]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise WorkflowError(f"environment.hard_limits.{name} must be positive")
        observed = preflight.get(observed_field)
        if isinstance(observed, int) and observed > limit:
            reasons.append(reason)
    if not reasons:
        return bundle
    paused = copy.deepcopy(bundle)
    paused_preflight = paused["review_plan"]["preflight"]
    paused_preflight["status"] = "paused"
    paused_preflight["blocking_reasons"] = [
        *paused_preflight.get("blocking_reasons", []),
        *reasons,
    ]
    paused["reviewer_task_envelopes"] = []
    uncovered_reason = "preflight_blocked:" + ",".join(reasons)
    ledger = paused["coverage_ledger"]
    for collection in (ledger["records"], ledger.get("units", [])):
        for entry in collection:
            if not entry["covered"]:
                entry["uncovered_reason"] = uncovered_reason
    ledger["uncovered_record_ids"] = [
        entry["record_id"] for entry in ledger["records"] if not entry["covered"]
    ]
    ledger["complete"] = False
    return paused


def _complete_degraded_fallback(
    *,
    repo: Path,
    request: dict[str, Any],
    environment: dict[str, Any],
    scope: dict[str, Any],
    bundle: dict[str, Any],
    raw_outcome: object,
    raw_decisions: object,
    raw_validation_results: object,
    raw_trusted_commands: object,
    before: dict[str, Any],
    safety_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    outcome = copy.deepcopy(_object(raw_outcome, "degraded_fallback_outcome"))
    required = {
        "status",
        "inspected",
        "candidate_findings",
        "unresolved_assumptions",
        "proposed_checks",
    }
    if set(outcome) != required:
        raise WorkflowError(
            "degraded_fallback_outcome must contain status, inspected, candidate_findings, unresolved_assumptions, and proposed_checks"
        )
    if outcome["status"] != "completed":
        raise WorkflowError("degraded_fallback_outcome.status must be completed")
    inspected = _select_record_ids(
        scope, outcome["inspected"], "degraded_fallback_outcome.inspected"
    )
    expected_record_ids = {
        record["record_id"] for record in scope["changed_records"]
    }
    if set(inspected) != expected_record_ids:
        raise WorkflowError(
            "degraded fallback must inspect every frozen changed record"
        )
    controlled_candidates = [
        _candidate_from_control(
            raw_candidate,
            scope,
            expected_record_ids,
            f"degraded_fallback_outcome.candidate_findings[{index}]",
        )
        for index, raw_candidate in enumerate(
            _array(
                outcome["candidate_findings"],
                "degraded_fallback_outcome.candidate_findings",
            )
        )
    ]
    candidates = validate_candidate_findings(
        controlled_candidates, "degraded_fallback_outcome.candidate_findings"
    )
    records_by_id = {
        record["record_id"]: record for record in scope["changed_records"]
    }
    if any(
        candidate["changed_record_id"] not in expected_record_ids
        or candidate["changed_record_id"] not in records_by_id
        or candidate["anchor"]
        != records_by_id[candidate["changed_record_id"]]["anchor"]
        for candidate in candidates
    ):
        raise WorkflowError(
            "degraded fallback candidate record and anchor are not the same frozen inspected evidence"
        )
    proposals = [
        {
            "task_id": "degraded-single-agent",
            "unit_id": "degraded-scope",
            "role": "single_agent",
            "proposal": copy.deepcopy(
                _object(
                    raw_proposal,
                    f"degraded_fallback_outcome.proposed_checks[{index}]",
                )
            ),
        }
        for index, raw_proposal in enumerate(
            _array(
                outcome["proposed_checks"],
                "degraded_fallback_outcome.proposed_checks",
            )
        )
    ]
    trusted_commands = _trusted_validation_commands(
        repo, scope, request, environment, raw_trusted_commands
    )
    validation_screening, checks = _screen_and_bind_validations(
        proposals, trusted_commands, raw_validation_results
    )
    synthetic_bundle = copy.deepcopy(bundle)
    synthetic_bundle["review_lifecycle"] = {
        "accepted_results": [
            {
                "task_id": "degraded-single-agent",
                "unit_id": "degraded-scope",
                "role": "single_agent",
                "inspected_record_ids": inspected,
                "candidate_findings": candidates,
            }
        ]
    }
    prepared_decisions: list[dict[str, Any]] = []
    for index, raw_decision in enumerate(
        _array(raw_decisions, "candidate_decisions")
    ):
        decision = copy.deepcopy(
            _object(raw_decision, f"candidate_decisions[{index}]")
        )
        if "unit_id" in decision or "role" in decision:
            raise WorkflowError(
                "degraded candidate decisions do not accept paired unit or role claims"
            )
        decision["unit_id"] = "degraded-scope"
        decision["role"] = "single_agent"
        prepared_decisions.append(decision)
    decisions = _bind_candidate_decisions(
        synthetic_bundle, scope, prepared_decisions, checks
    )
    candidates_with_provenance = [
        {
            **candidate,
            "task_id": "degraded-single-agent",
            "unit_id": "degraded-scope",
            "role": "single_agent",
        }
        for candidate in candidates
    ]
    adjudicated = adjudicate_candidates(
        candidates_with_provenance, decisions, evidence_bound=True
    )
    verified_findings = adjudicated["verified_findings"]
    questions = adjudicated["questions"]
    verification_gaps: list[dict[str, Any]] = [
        {
            "kind": "degraded_reviewer_independence",
            "unit_id": "degraded-scope",
            "role": "single_agent",
            "reason": "isolated paired reviewers were unavailable; approved single-agent fallback completed",
        },
        *adjudicated["verification_gaps"],
    ]
    assumption_ids: set[str] = set()
    for index, raw_assumption in enumerate(
        _array(
            outcome["unresolved_assumptions"],
            "degraded_fallback_outcome.unresolved_assumptions",
        )
    ):
        assumption = copy.deepcopy(
            _object(
                raw_assumption,
                f"degraded_fallback_outcome.unresolved_assumptions[{index}]",
            )
        )
        if set(assumption) != {"assumption_id", "text"}:
            raise WorkflowError(
                f"degraded_fallback_outcome.unresolved_assumptions[{index}] must contain only assumption_id and text"
            )
        assumption_id = _string(
            assumption.get("assumption_id"),
            f"degraded_fallback_outcome.unresolved_assumptions[{index}].assumption_id",
        )
        if assumption_id in assumption_ids:
            raise WorkflowError(
                "degraded_fallback_outcome unresolved assumption IDs must be unique"
            )
        assumption_ids.add(assumption_id)
        _string(
            assumption.get("text"),
            f"degraded_fallback_outcome.unresolved_assumptions[{index}].text",
        )
        questions.append(
            {
                "kind": "unresolved_reviewer_assumption",
                "task_id": "degraded-single-agent",
                "unit_id": "degraded-scope",
                "role": "single_agent",
                "assumption": assumption,
            }
        )
    final = {
        "verified_findings": verified_findings,
        "questions": questions,
        "verification_gaps": verification_gaps,
        "coverage_ledger": bundle["coverage_ledger"],
        "reviewer_pair_status": "incomplete",
        "scope_gate": "passed_before_final_report",
    }
    current_scope = _resolve_and_bind_scope(repo, request)
    if current_scope != scope:
        raise WorkflowError(
            "scope drift detected before degraded final reporting"
        )
    after = _repository_state(
        repo,
        selected_paths=_requested_snapshot_paths(request),
        max_untracked_bytes=_requested_snapshot_max_untracked_bytes(request),
    )
    if before != after:
        raise WorkflowError(
            "repository changed during review; discard stale degraded results and restart from a fresh snapshot"
        )
    phase_boundary = {
        "review": "completed_degraded",
        "authorized_followup": request.get("authorized_followup"),
        "followup_state": (
            "not_started"
            if request.get("authorized_followup") is not None
            else "not_authorized"
        ),
    }
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": "completed_degraded",
        "request": copy.deepcopy(request),
        "repository_root": str(repo),
        "resolved_scope": scope,
        "review_plan": bundle["review_plan"],
        "dispatch_envelope_count": 0,
        **final,
        "reviewer_isolation": [],
        "checks": checks,
        "validation_screening": validation_screening,
        "safety_decisions": safety_decisions,
        "repository_unchanged": True,
        "phase_boundary": phase_boundary,
    }
    output["report_markdown"] = _render_markdown(
        final, scope, checks, phase_boundary
    )
    return output


def _run_workflow(
    raw_value: object, provider_consent: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = _object(raw_value, "workflow input")
    allowed_fields = {
        "schema_version",
        "repository",
        "request",
        "environment",
        "partition",
        "reviewer_outcomes",
        "candidate_decisions",
        "validation_results",
        "trusted_commands",
        "safety_attempts",
        "budget_decision",
        "previous_review_plan",
        "degraded_fallback",
        "degraded_fallback_outcome",
        "trusted_lenses",
    }
    unknown_fields = set(value) - allowed_fields
    if unknown_fields:
        raise WorkflowError(
            "workflow input contains unsupported fields: "
            + ", ".join(sorted(unknown_fields))
        )
    if value.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError(f"workflow input schema_version must be {SCHEMA_VERSION}")
    repository_value = validate_path_text(
        _string(value.get("repository"), "repository"),
        "repository",
        error_type=WorkflowError,
    )
    requested_repo = Path(repository_value).resolve()
    repo = _validated_repo_root(requested_repo)
    request = _object(value.get("request"), "request")
    _string(request.get("text"), "request.text")
    environment = _object(value.get("environment"), "environment")
    safety_decisions = _screen_safety_attempts(
        value.get("safety_attempts", []), environment, repo, provider_consent
    )
    snapshot_paths = _requested_snapshot_paths(request)
    snapshot_max_untracked_bytes = _requested_snapshot_max_untracked_bytes(request)
    before = _repository_state(
        repo,
        selected_paths=snapshot_paths,
        max_untracked_bytes=snapshot_max_untracked_bytes,
    )
    requested_scope = _object(request.get("scope"), "request.scope")
    if requested_scope.get("kind") == "unresolved":
        if requested_scope.get("network_lookup_authorized") is not False:
            raise WorkflowError(
                "unresolved scope must state whether a network lookup is authorized"
            )
        after = _repository_state(
            repo,
            selected_paths=snapshot_paths,
            max_untracked_bytes=snapshot_max_untracked_bytes,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "scope_unresolved",
            "request": copy.deepcopy(request),
            "reason": _string(requested_scope.get("reason"), "request.scope.reason"),
            "required_action": "ask_for_target_or_network_permission",
            "network_attempted": False,
            "repository_unchanged": before == after,
        }
    scope = _resolve_and_bind_scope(repo, request)
    graph = _build_feature_graph(scope, value.get("partition"))
    build_input: dict[str, Any] = {
        "scope_manifest": scope,
        "feature_graph": graph,
        "environment": environment,
    }
    if "budget_decision" in value:
        raw_decision = copy.deepcopy(_object(value["budget_decision"], "budget_decision"))
        if raw_decision.get("graph_revision") == "current":
            raise WorkflowError("budget approval requires an exact prior graph revision")
        build_input["budget_decision"] = raw_decision
    for optional in ("previous_review_plan", "degraded_fallback", "trusted_lenses"):
        if optional not in value:
            continue
        if optional == "trusted_lenses" and _array(
            value[optional], "trusted_lenses"
        ):
            raise WorkflowError(
                "caller-supplied trusted_lenses are not authoritative; use the trusted contract seam or an out-of-band lens catalog"
            )
        build_input[optional] = copy.deepcopy(value[optional])
    bundle = build_graph(build_input)
    bundle = _apply_hard_limits(bundle, environment)
    preflight = bundle["review_plan"]["preflight"]
    if preflight["status"] == "ready_degraded":
        if "degraded_fallback_outcome" in value:
            return _complete_degraded_fallback(
                repo=repo,
                request=request,
                environment=environment,
                scope=scope,
                bundle=bundle,
                raw_outcome=value["degraded_fallback_outcome"],
                raw_decisions=value.get("candidate_decisions", []),
                raw_validation_results=value.get("validation_results", []),
                raw_trusted_commands=value.get("trusted_commands", []),
                before=before,
                safety_decisions=safety_decisions,
            )
        after = _repository_state(
            repo,
            selected_paths=snapshot_paths,
            max_untracked_bytes=snapshot_max_untracked_bytes,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "degraded_fallback_ready",
            "request": copy.deepcopy(request),
            "repository_root": str(repo),
            "resolved_scope": scope,
            "review_plan": bundle["review_plan"],
            "dispatch_envelope_count": 0,
            "coverage_ledger": bundle["coverage_ledger"],
            "repository_unchanged": before == after,
            "safety_decisions": safety_decisions,
            "required_action": "run_approved_single_agent_fallback",
        }
    if preflight["status"] not in {"ready", "ready_partial"}:
        after = _repository_state(
            repo,
            selected_paths=snapshot_paths,
            max_untracked_bytes=snapshot_max_untracked_bytes,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "preflight_paused",
            "request": copy.deepcopy(request),
            "repository_root": str(repo),
            "resolved_scope": scope,
            "review_plan": bundle["review_plan"],
            "dispatch_envelope_count": len(bundle["reviewer_task_envelopes"]),
            "coverage_ledger": bundle["coverage_ledger"],
            "repository_unchanged": before == after,
            "safety_decisions": safety_decisions,
        }
    (
        completed_bundle,
        retries,
        lifecycle_gaps,
        isolation_evidence,
        proposed_checks,
        control_state,
    ) = _run_outcomes(bundle, scope, value.get("reviewer_outcomes"))
    if control_state is not None:
        after = _repository_state(
            repo,
            selected_paths=snapshot_paths,
            max_untracked_bytes=snapshot_max_untracked_bytes,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            **control_state,
            "request": copy.deepcopy(request),
            "repository_root": str(repo),
            "resolved_scope": scope,
            "review_plan": completed_bundle["review_plan"],
            "dispatch_envelope_count": len(completed_bundle["reviewer_task_envelopes"]),
            "coverage_ledger": completed_bundle["coverage_ledger"],
            "verification_gaps": lifecycle_gaps,
            "observed_retries": retries,
            "reviewer_isolation": isolation_evidence,
            "safety_decisions": safety_decisions,
            "repository_unchanged": before == after,
        }
    current_scope = _resolve_and_bind_scope(repo, request)
    trusted_commands = _trusted_validation_commands(
        repo,
        scope,
        request,
        environment,
        value.get("trusted_commands", []),
    )
    validation_screening, checks = _screen_and_bind_validations(
        proposed_checks,
        trusted_commands,
        value.get("validation_results", []),
    )
    final = finalize_review(
        {
            "bundle": completed_bundle,
            "current_scope_manifest": current_scope,
            "candidate_decisions": _materialize_finalization_decisions(
                scope, value.get("candidate_decisions", [])
            ),
            "validation_checks": checks,
        }
    )
    after = _repository_state(
        repo,
        selected_paths=snapshot_paths,
        max_untracked_bytes=snapshot_max_untracked_bytes,
    )
    if before != after:
        raise WorkflowError(
            "repository changed during review; discard stale results and restart from a fresh snapshot"
        )
    phase_boundary = {
        "review": "completed",
        "authorized_followup": request.get("authorized_followup"),
        "followup_state": (
            "not_started"
            if request.get("authorized_followup") is not None
            else "not_authorized"
        ),
    }
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "request": copy.deepcopy(request),
        "repository_root": str(repo),
        "resolved_scope": scope,
        "review_plan": completed_bundle["review_plan"],
        "dispatch_envelope_count": len(completed_bundle["reviewer_task_envelopes"]),
        **final,
        "observed_retries": retries,
        "lifecycle_gaps": lifecycle_gaps,
        "reviewer_isolation": isolation_evidence,
        "checks": checks,
        "validation_screening": validation_screening,
        "safety_decisions": safety_decisions,
        "repository_unchanged": True,
        "phase_boundary": phase_boundary,
    }
    output["report_markdown"] = _render_markdown(
        final, scope, checks, phase_boundary
    )
    return output


def run_workflow(
    raw_value: object, provider_consent: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        return _run_workflow(raw_value, provider_consent)
    except SnapshotBudgetExceeded as exc:
        value = raw_value if isinstance(raw_value, dict) else {}
        raw_request = value.get("request")
        request = copy.deepcopy(raw_request) if isinstance(raw_request, dict) else {}
        repository = value.get("repository")
        required_action = (
            "compact_git_administration_or_approve_larger_limit_and_retry"
            if exc.budget == "admin_entries"
            else "reduce_snapshot_scope_and_retry"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "preflight_paused",
            "request": request,
            "repository_root": repository if isinstance(repository, str) else None,
            "dispatch_envelope_count": 0,
            "reviewer_pair_status": "incomplete",
            "verification_gaps": [exc.coverage_gap()],
            "repository_unchanged": None,
            "required_action": required_action,
        }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _implementation_digest(root: Path) -> str:
    skill_root = root / "skills" / "personal" / "code-review"
    entries: list[tuple[Path, Path, os.stat_result]] = []
    release_input_prefixes = (
        ("skills", "personal", "code-review", "scripts"),
        ("skills", "personal", "code-review", "tests"),
    )

    def is_release_input(relative: Path) -> bool:
        return relative == Path("Makefile") or any(
            relative.parts[: len(prefix)] == prefix
            for prefix in release_input_prefixes
        )

    def visit(path: Path) -> None:
        relative = path.relative_to(root)
        if (
            "__pycache__" in relative.parts
            or path.suffix in {".pyc", ".pyo"}
            or relative == INDEPENDENT_EVALUATION_RELATIVE_PATH
        ):
            return
        try:
            info = path.lstat()
        except OSError as exc:
            raise WorkflowError(
                f"implementation digest could not inspect {relative.as_posix()}: {exc}"
            ) from exc
        if not (
            stat.S_ISREG(info.st_mode)
            or stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
        ):
            raise WorkflowError(
                "implementation digest entries must be regular files, directories, "
                f"or symbolic links: {relative.as_posix()}"
            )
        if stat.S_ISLNK(info.st_mode) and is_release_input(relative):
            raise WorkflowError(
                "release inputs must be nofollow regular files or directories: "
                + relative.as_posix()
            )
        entries.append((relative, path, info))
        if stat.S_ISDIR(info.st_mode):
            try:
                children = sorted(
                    path.iterdir(), key=lambda child: os.fsencode(child.name)
                )
            except OSError as exc:
                raise WorkflowError(
                    f"implementation digest could not list {relative.as_posix()}: {exc}"
                ) from exc
            for child in children:
                visit(child)

    visit(root / "Makefile")
    visit(skill_root)
    digest = hashlib.sha256()
    for relative, path, info in sorted(
        entries, key=lambda entry: os.fsencode(entry[0].as_posix())
    ):
        digest.update(os.fsencode(relative.as_posix()))
        digest.update(b"\0")
        digest.update(f"mode:{stat.S_IMODE(info.st_mode):o}".encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"directory")
        elif stat.S_ISLNK(info.st_mode):
            digest.update(b"symlink\0")
            try:
                digest.update(os.fsencode(os.readlink(path)))
            except OSError as exc:
                raise WorkflowError(
                    f"implementation digest could not read link {relative.as_posix()}: {exc}"
                ) from exc
        else:
            digest.update(b"regular\0")
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise WorkflowError(
                    f"implementation digest could not open regular file {relative.as_posix()}: {exc}"
                ) from exc
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                ) != (info.st_dev, info.st_ino):
                    raise WorkflowError(
                        "implementation digest regular file changed during inspection: "
                        + relative.as_posix()
                    )
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _v1_semantic_case_ids() -> list[str]:
    path = Path(__file__).resolve().parents[1] / "tests" / "eval_cases.json"
    try:
        catalog = _object(json.loads(path.read_text(encoding="utf-8")), "v1 catalog")
        cases = _array(catalog.get("cases"), "v1 catalog.cases")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"v1 semantic catalog is invalid: {exc}") from exc
    case_ids = [
        _string(_object(case, f"v1 catalog.cases[{index}]").get("id"), f"v1 catalog.cases[{index}].id")
        for index, case in enumerate(cases)
    ]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise WorkflowError("v1 semantic catalog case IDs must be non-empty and unique")
    return case_ids


def _validate_probe_manifest() -> None:
    probe_ids: set[str] = set()
    tests: set[str] = set()
    covered_categories: set[str] = set()
    covered_invariants: set[str] = set()
    for index, probe in enumerate(PROBE_MANIFEST):
        if set(probe) != {"id", "test", "categories", "safety_invariants"}:
            raise WorkflowError(f"probe manifest entry {index} is malformed")
        probe_id = _string(probe["id"], f"probe manifest entry {index}.id")
        test = _string(probe["test"], f"probe manifest entry {index}.test")
        if probe_id in probe_ids or test in tests:
            raise WorkflowError("probe manifest IDs and tests must be unique")
        probe_ids.add(probe_id)
        tests.add(test)
        categories = probe["categories"]
        invariants = probe["safety_invariants"]
        if (
            not isinstance(categories, list)
            or not categories
            or any(category not in RUBRIC for category in categories)
        ):
            raise WorkflowError(f"probe manifest entry {probe_id} has invalid categories")
        if (
            not isinstance(invariants, list)
            or any(invariant not in SAFETY_INVARIANTS for invariant in invariants)
        ):
            raise WorkflowError(
                f"probe manifest entry {probe_id} has invalid safety invariants"
            )
        covered_categories.update(categories)
        covered_invariants.update(invariants)
    if covered_categories != set(RUBRIC):
        raise WorkflowError("probe manifest does not cover every rubric category")
    if covered_invariants != SAFETY_INVARIANTS:
        raise WorkflowError("probe manifest does not cover every safety invariant")


def _load_independent_evaluation(
    root: Path, implementation_digest: str
) -> dict[str, Any]:
    path = root / INDEPENDENT_EVALUATION_RELATIVE_PATH
    try:
        canonical, payload = _read_stable_regular_artifact(
            path, "independent standard-agent evaluation artifact"
        )
    except WorkflowError as exc:
        if not path.exists() and not path.is_symlink():
            raise WorkflowError(
                "independent standard-agent evaluation artifact is missing; make check never generates it"
            ) from exc
        raise
    expected_path = root.resolve() / INDEPENDENT_EVALUATION_RELATIVE_PATH
    if canonical != expected_path:
        raise WorkflowError(
            "independent standard-agent evaluation artifact must use its canonical repository path"
        )
    relative = INDEPENDENT_EVALUATION_RELATIVE_PATH.as_posix()
    staged = _git(root, "ls-files", "--stage", "-z", "--", relative)
    entries = [entry for entry in staged.split(b"\0") if entry]
    if len(entries) != 1:
        raise WorkflowError(
            "independent standard-agent evaluation artifact must be tracked in the Git index"
        )
    header, separator, staged_path = entries[0].partition(b"\t")
    fields = header.split()
    if (
        separator != b"\t"
        or staged_path.decode("utf-8", "surrogateescape") != relative
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[2] != b"0"
    ):
        raise WorkflowError(
            "independent standard-agent evaluation artifact must have one regular stage-0 index entry"
        )
    staged_payload = _git(root, "cat-file", "blob", fields[1].decode("ascii"))
    if staged_payload != payload:
        raise WorkflowError(
            "independent standard-agent evaluation artifact must match its staged Git blob"
        )
    try:
        raw_evaluation = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(
            f"independent standard-agent evaluation artifact is invalid: {exc}"
        ) from exc
    evaluation = copy.deepcopy(
        _object(raw_evaluation, "independent standard-agent evaluation")
    )
    required = {
        "schema_version",
        "evaluation_id",
        "producer",
        "reviewed_implementation_digest",
        "category_scores",
        "safety_invariants",
        "v1_semantic_observations",
        "unresolved_findings",
        "attestation",
    }
    if set(evaluation) != required:
        raise WorkflowError(
            "independent standard-agent evaluation artifact has an invalid schema"
        )
    if evaluation["schema_version"] != SCHEMA_VERSION:
        raise WorkflowError(
            f"independent evaluation schema_version must be {SCHEMA_VERSION}"
        )
    _string(evaluation["evaluation_id"], "independent evaluation.evaluation_id")
    producer = _object(evaluation["producer"], "independent evaluation.producer")
    if set(producer) != {"kind", "context_id", "independent"}:
        raise WorkflowError("independent evaluation producer is malformed")
    if producer["kind"] != "standard_agent" or producer["independent"] is not True:
        raise WorkflowError(
            "independent evaluation must attest a separate standard-agent context"
        )
    _string(producer["context_id"], "independent evaluation.producer.context_id")
    _string(evaluation["attestation"], "independent evaluation.attestation")
    if evaluation["reviewed_implementation_digest"] != implementation_digest:
        raise WorkflowError(
            "independent standard-agent evaluation is stale for this implementation digest"
        )
    semantic_observations = _array(
        evaluation["v1_semantic_observations"],
        "independent evaluation.v1_semantic_observations",
    )
    expected_case_ids = _v1_semantic_case_ids()
    observed_case_ids: list[str] = []
    for index, raw_observation in enumerate(semantic_observations):
        observation = _object(
            raw_observation,
            f"independent evaluation.v1_semantic_observations[{index}]",
        )
        if set(observation) != {"case_id", "status", "evidence"}:
            raise WorkflowError(
                "independent evaluation v1 semantic observations must contain exactly case_id, status, and evidence"
            )
        case_id = _string(
            observation["case_id"],
            f"independent evaluation.v1_semantic_observations[{index}].case_id",
        )
        if observation["status"] not in {"passed", "failed"}:
            raise WorkflowError(
                "independent evaluation v1 semantic observation status must be passed or failed"
            )
        _string(
            observation["evidence"],
            f"independent evaluation.v1_semantic_observations[{index}].evidence",
        )
        observed_case_ids.append(case_id)
    if (
        len(observed_case_ids) != len(set(observed_case_ids))
        or set(observed_case_ids) != set(expected_case_ids)
    ):
        raise WorkflowError(
            "independent evaluation v1 semantic coverage must contain every catalog case exactly once"
        )
    evaluate_release_gate(
        {
            "schema_version": SCHEMA_VERSION,
            "category_scores": evaluation["category_scores"],
            "safety_invariants": evaluation["safety_invariants"],
            "unresolved_findings": evaluation["unresolved_findings"],
        }
    )
    return evaluation


def _evidence_binding(root: Path) -> dict[str, str]:
    head_oid = _git_optional(root, "rev-parse", "--verify", "HEAD").decode().strip()
    if not head_oid:
        raise WorkflowError("independent grade requires a repository HEAD revision")
    implementation_digest = _implementation_digest(root)
    independent_evaluation = _load_independent_evaluation(
        root, implementation_digest
    )
    return {
        "grader_id": GRADER["id"],
        "grader_version": GRADER["version"],
        "head_oid": head_oid,
        "implementation_digest": implementation_digest,
        "probe_manifest_digest": _sha256_json(PROBE_MANIFEST),
        "independent_evaluation_digest": _sha256_json(independent_evaluation),
    }


def _run_probe(root: Path, probe: dict[str, Any]) -> dict[str, Any]:
    test_root = root / "skills" / "personal" / "code-review" / "tests"
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve().with_name("run_unittest.py")),
                "run",
                str(test_root),
                probe["test"],
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            timeout=120,
            check=False,
        )
        combined_output = f"{result.stdout}\n{result.stderr}"
        skipped = result.returncode == 0 and "skipped=" in combined_output
        status = "skipped" if skipped else "passed" if result.returncode == 0 else "failed"
        detail = (
            "unittest process exited 0"
            if status == "passed"
            else "unittest reported one or more skipped tests"
            if status == "skipped"
            else f"unittest process exited {result.returncode}"
        )
        exit_code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        status = "error"
        detail = f"{type(exc).__name__}: {exc}"
        exit_code = None
    return {
        "id": probe["id"],
        "test": probe["test"],
        "categories": list(probe["categories"]),
        "safety_invariants": list(probe["safety_invariants"]),
        "status": status,
        "exit_code": exit_code,
        "evidence": detail,
    }


def _score_probe_observations(
    observations: list[dict[str, Any]],
    binding: dict[str, str],
    independent_evaluation: dict[str, Any],
) -> dict[str, Any]:
    expected_by_id = {probe["id"]: probe for probe in PROBE_MANIFEST}
    if len(observations) != len(expected_by_id):
        raise WorkflowError("independent grade evidence is missing probe observations")
    observed_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_observation in enumerate(observations):
        observation = _object(raw_observation, f"probe_observations[{index}]")
        required = {
            "id",
            "test",
            "categories",
            "safety_invariants",
            "status",
            "exit_code",
            "evidence",
        }
        if set(observation) != required:
            raise WorkflowError(f"probe_observations[{index}] is malformed")
        probe_id = _string(observation["id"], f"probe_observations[{index}].id")
        expected = expected_by_id.get(probe_id)
        if expected is None or probe_id in observed_by_id:
            raise WorkflowError("independent grade evidence contains unknown or duplicate probes")
        if (
            observation["test"] != expected["test"]
            or observation["categories"] != expected["categories"]
            or observation["safety_invariants"] != expected["safety_invariants"]
        ):
            raise WorkflowError(f"probe observation {probe_id} does not match its manifest")
        if observation["status"] not in {"passed", "failed", "error", "skipped"}:
            raise WorkflowError(f"probe observation {probe_id} has an invalid status")
        if observation["status"] == "passed" and observation["exit_code"] != 0:
            raise WorkflowError(f"probe observation {probe_id} has contradictory evidence")
        if not isinstance(observation["evidence"], str) or not observation["evidence"]:
            raise WorkflowError(f"probe observation {probe_id} has no evidence")
        observed_by_id[probe_id] = observation
    probe_scores = {
        category: maximum
        if all(
            observed_by_id[probe["id"]]["status"] == "passed"
            for probe in PROBE_MANIFEST
            if category in probe["categories"]
        )
        else 0
        for category, maximum in RUBRIC.items()
    }
    probe_invariants = {
        invariant: all(
            observed_by_id[probe["id"]]["status"] == "passed"
            for probe in PROBE_MANIFEST
            if invariant in probe["safety_invariants"]
        )
        for invariant in sorted(SAFETY_INVARIANTS)
    }
    probe_findings = [
        {
            "severity": "High",
            "title": f"Behavioral release probe failed: {observation['id']}",
        }
        for observation in observations
        if observation["status"] != "passed"
    ]
    semantic_failures = [
        observation
        for observation in independent_evaluation["v1_semantic_observations"]
        if observation["status"] != "passed"
    ]
    semantic_findings = [
        {
            "severity": "High",
            "title": f"Independent v1 semantic case failed: {observation['case_id']}",
        }
        for observation in semantic_failures
    ]
    scores = {
        category: (
            0
            if category == "v1_compatibility" and semantic_failures
            else min(
                probe_scores[category],
                independent_evaluation["category_scores"][category],
            )
        )
        for category in RUBRIC
    }
    invariants = {
        invariant: probe_invariants[invariant]
        and independent_evaluation["safety_invariants"][invariant]
        for invariant in sorted(SAFETY_INVARIANTS)
    }
    findings_by_identity: dict[str, dict[str, Any]] = {}
    for finding in [
        *probe_findings,
        *semantic_findings,
        *independent_evaluation["unresolved_findings"],
    ]:
        findings_by_identity.setdefault(_sha256_json(finding), copy.deepcopy(finding))
    findings = list(findings_by_identity.values())
    gate = evaluate_release_gate(
        {
            "schema_version": SCHEMA_VERSION,
            "category_scores": scores,
            "safety_invariants": invariants,
            "unresolved_findings": findings,
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "release_ready" if gate["release_ready"] else "blocked",
        "grader": copy.deepcopy(GRADER),
        "evidence_binding": binding,
        "probe_observations": observations,
        "probe_category_scores": probe_scores,
        "probe_safety_invariants": probe_invariants,
        "independent_evaluation": copy.deepcopy(independent_evaluation),
        "aggregation": {
            "category_scores": "minimum_of_probe_and_independent_evaluation",
            "v1_semantics": "all_independent_catalog_observations_must_pass",
            "safety_invariants": "logical_and",
            "unresolved_findings": "union",
        },
        "safety_invariants": invariants,
        "unresolved_findings": findings,
        **gate,
    }


def run_independent_grade() -> dict[str, Any]:
    _validate_probe_manifest()
    root = _repository_root()
    initial_repository_state = _repository_state(root)
    binding = _evidence_binding(root)
    independent_evaluation = _load_independent_evaluation(
        root, binding["implementation_digest"]
    )
    observations = [_run_probe(root, probe) for probe in PROBE_MANIFEST]
    if _repository_state(root) != initial_repository_state:
        raise WorkflowError(
            "repository changed during probe execution; discard the mixed snapshot and rerun"
        )
    post_probe_binding = _evidence_binding(root)
    if post_probe_binding != binding:
        raise WorkflowError(
            "release evidence changed during probe execution; discard the mixed snapshot and rerun"
        )
    return _score_probe_observations(
        observations, binding, independent_evaluation
    )


def verify_independent_grade(raw_value: object) -> dict[str, Any]:
    value = _object(raw_value, "independent grade")
    if "grader" not in value or "evidence_binding" not in value:
        raise WorkflowError(
            "gate does not accept caller-supplied scorecards; submit no-input grade output"
        )
    _validate_probe_manifest()
    root = _repository_root()
    initial_repository_state = _repository_state(root)
    expected_binding = _evidence_binding(root)
    if value.get("grader") != GRADER or value.get("evidence_binding") != expected_binding:
        raise WorkflowError("independent grade evidence is not bound to this implementation revision")
    observations = _array(value.get("probe_observations"), "probe_observations")
    independent_evaluation = _load_independent_evaluation(
        root, expected_binding["implementation_digest"]
    )
    submitted = _score_probe_observations(
        observations, expected_binding, independent_evaluation
    )
    if value != submitted:
        raise WorkflowError("independent grade verdict does not match its bound probe evidence")
    fresh = run_independent_grade()
    if submitted["probe_observations"] != fresh["probe_observations"]:
        raise WorkflowError("independent grade evidence is stale or cannot be reproduced")
    final_binding = _evidence_binding(root)
    if (
        fresh.get("evidence_binding") != expected_binding
        or final_binding != expected_binding
        or _repository_state(root) != initial_repository_state
    ):
        raise WorkflowError(
            "release evidence changed during gate reproduction; no verdict can be emitted"
        )
    return fresh


def evaluate_release_gate(raw_value: object) -> dict[str, Any]:
    value = _object(raw_value, "release scorecard")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise WorkflowError(f"release scorecard schema_version must be {SCHEMA_VERSION}")
    scores = _object(value.get("category_scores"), "category_scores")
    if set(scores) != set(RUBRIC):
        raise WorkflowError("category_scores must contain exactly the approved rubric categories")
    normalized_scores: dict[str, int] = {}
    for category, maximum in RUBRIC.items():
        score = scores[category]
        if (
            not isinstance(score, int)
            or isinstance(score, bool)
            or score < 0
            or score > maximum
        ):
            raise WorkflowError(
                f"category_scores.{category} must be an integer from 0 to {maximum}"
            )
        normalized_scores[category] = score
    invariants = _object(value.get("safety_invariants"), "safety_invariants")
    if set(invariants) != SAFETY_INVARIANTS:
        raise WorkflowError(
            "safety_invariants must contain exactly the approved critical invariants"
        )
    if any(not isinstance(result, bool) for result in invariants.values()):
        raise WorkflowError("every safety invariant result must be boolean")
    findings = _array(value.get("unresolved_findings"), "unresolved_findings")
    unresolved_blocker = False
    for index, raw_finding in enumerate(findings):
        finding = _object(raw_finding, f"unresolved_findings[{index}]")
        if set(finding) != {"severity", "title"}:
            raise WorkflowError(
                f"unresolved_findings[{index}] must contain exactly severity and title"
            )
        severity = _string(
            finding.get("severity"), f"unresolved_findings[{index}].severity"
        ).lower()
        _string(finding.get("title"), f"unresolved_findings[{index}].title")
        if severity not in {"critical", "high", "medium", "low"}:
            raise WorkflowError(
                f"unresolved_findings[{index}].severity is unsupported"
            )
        unresolved_blocker = unresolved_blocker or severity in {"critical", "high"}
    overall = sum(normalized_scores.values())
    blocking_reasons: list[str] = []
    if overall < 94:
        blocking_reasons.append("overall_score_below_94")
    blocking_reasons.extend(
        f"safety_invariant_failed:{name}"
        for name in sorted(SAFETY_INVARIANTS)
        if invariants[name] is not True
    )
    if unresolved_blocker:
        blocking_reasons.append("unresolved_critical_or_high_finding")
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric": RUBRIC,
        "category_scores": normalized_scores,
        "overall_score": overall,
        "minimum_score": 94,
        "safety_pass_rate": sum(invariants.values()) / len(SAFETY_INVARIANTS),
        "safety_required_pass_rate": 1.0,
        "unresolved_critical_or_high": unresolved_blocker,
        "blocking_reasons": blocking_reasons,
        "release_ready": not blocking_reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one controlled workflow from JSON stdin")
    run.add_argument(
        "--provider-consent",
        type=Path,
        help="read a control-plane provider consent artifact outside the reviewed repository",
    )
    subparsers.add_parser("grade", help="run the versioned independent behavioral probes")
    subparsers.add_parser("gate", help="verify and reproduce no-input grade evidence")
    return parser


def main() -> int:
    if os.name != "posix":
        print("ERROR: code-review workflow requires a POSIX runtime", file=sys.stderr)
        return 2
    args = _parser().parse_args()
    try:
        if args.command == "run":
            value = json.load(sys.stdin)
            provider_consent = (
                _load_provider_consent(args.provider_consent)
                if args.provider_consent is not None
                else None
            )
            output = run_workflow(value, provider_consent)
        elif args.command == "grade":
            supplied = "" if sys.stdin.isatty() else sys.stdin.read()
            if supplied.strip():
                raise WorkflowError("grade does not accept caller-supplied evidence")
            output = run_independent_grade()
        elif args.command == "gate":
            output = verify_independent_grade(json.load(sys.stdin))
        else:
            raise WorkflowError(f"unsupported command: {args.command}")
    except (
        json.JSONDecodeError,
        OSError,
        ValueError,
        ScopeError,
        ContractError,
        WorkflowError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.command in {"grade", "gate"}:
            json.dump(
                {"schema_version": SCHEMA_VERSION, "status": "invalid", "error": str(exc)},
                sys.stdout,
                indent=2,
                sort_keys=True,
            )
            sys.stdout.write("\n")
        return 2
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return (
        1
        if args.command in {"grade", "gate"} and output["release_ready"] is False
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
