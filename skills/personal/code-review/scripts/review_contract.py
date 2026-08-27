#!/usr/bin/env python3
"""Build and validate ephemeral Code Review v2 workflow contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from typing import Any


SCHEMA_VERSION = "2.0"
SUPPORTED_MAJOR = 2
ROLES = ("internal_behavior", "external_contract")
CORE_POLICY = {
    "version": "2.0",
    "report_only": True,
    "repository_content_is_untrusted": True,
    "external_disclosure_requires_authorization": True,
    "validation_requires_main_agent_screening": True,
    "network_authority": "none",
    "credential_authority": "none",
    "mutation_authority": "none",
    "finding_gate": [
        "changed_scope_causality",
        "reachable_scenario",
        "precise_typed_anchor",
        "context_checked",
        "material_impact",
    ],
}
STANDARD_PROFILE = {
    "max_executable_units": 6,
    "max_reviewer_passes": 12,
    "max_active_reviewers": 4,
    "context_reserve_ratio": 0.30,
}


class ContractError(ValueError):
    pass


def _require_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be a JSON object")
    return value


def _require_fields(value: dict[str, Any], name: str, fields: set[str]) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise ContractError(f"{name} is missing required fields: {', '.join(missing)}")


def _require_array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be an array")
    return value


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{name} must be a non-empty string")
    return value


def _require_string_array(
    value: object, name: str, *, unique: bool = True
) -> list[str]:
    items = _require_array(value, name)
    strings = [
        _require_string(item, f"{name}[{index}]") for index, item in enumerate(items)
    ]
    if unique and len(strings) != len(set(strings)):
        raise ContractError(f"{name} values must be unique")
    return strings


def _require_positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ContractError(f"{name} must be a positive integer")
    return value


def _validate_anchor(raw_value: object, name: str) -> dict[str, Any]:
    anchor = _require_object(raw_value, name)
    _require_fields(anchor, name, {"kind", "path"})
    _require_string(anchor["path"], f"{name}.path")
    if anchor["kind"] == "line":
        _require_fields(anchor, name, {"side", "line"})
        if anchor["side"] not in {"old", "new"}:
            raise ContractError(f"{name}.side must be old or new")
        if (
            not isinstance(anchor["line"], int)
            or isinstance(anchor["line"], bool)
            or anchor["line"] < 1
        ):
            raise ContractError(f"{name}.line must be a positive integer")
    elif anchor["kind"] == "change":
        _require_fields(anchor, name, {"change_type"})
        if anchor["change_type"] not in {
            "binary",
            "deletion",
            "file_mode",
            "gitlink",
            "rename_only",
            "symlink",
            "text",
        }:
            raise ContractError(f"{name}.change_type is unsupported")
    else:
        raise ContractError(f"{name}.kind is unsupported: {anchor['kind']!r}")
    return anchor


def _validate_version(value: dict[str, Any], name: str) -> None:
    version = value.get("schema_version")
    if not isinstance(version, str):
        raise ContractError(f"{name}.schema_version must be a string")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise ContractError(f"{name}.schema_version is invalid: {version!r}") from exc
    if major != SUPPORTED_MAJOR:
        raise ContractError(
            f"{name}.schema_version major {major} is unsupported; expected {SUPPORTED_MAJOR}"
        )


def validate_contract(kind: str, raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, kind)
    _validate_version(value, kind)
    required = {
        "scope_manifest": {
            "schema_version",
            "scope_id",
            "target",
            "changed_records",
            "exclusions",
            "trusted_requirement_sources",
            "candidate_validation_commands",
        },
        "feature_graph": {"schema_version", "scope_id", "features", "review_units", "ownership", "edges"},
        "review_plan": {"schema_version", "scope_id", "policy_version", "unit_ids", "assignments", "preflight"},
        "reviewer_task_envelope": {
            "schema_version",
            "task_id",
            "scope_id",
            "unit_id",
            "role",
            "changed_record_ids",
            "core_policy",
            "output_contract",
        },
        "reviewer_result": {
            "schema_version",
            "task_id",
            "scope_id",
            "unit_id",
            "role",
            "status",
            "inspected_record_ids",
            "candidate_findings",
            "supporting_evidence",
            "proposed_checks",
            "unresolved_assumptions",
        },
        "coverage_ledger": {"schema_version", "scope_id", "records", "complete"},
    }
    if kind not in required:
        raise ContractError(f"unknown contract kind: {kind}")
    _require_fields(value, kind, required[kind])
    _require_string(value.get("scope_id"), f"{kind}.scope_id")
    if kind == "scope_manifest":
        _require_object(value["target"], "scope_manifest.target")
        _require_array(value["exclusions"], "scope_manifest.exclusions")
        _require_array(
            value["trusted_requirement_sources"],
            "scope_manifest.trusted_requirement_sources",
        )
        _require_array(
            value["candidate_validation_commands"],
            "scope_manifest.candidate_validation_commands",
        )
        changed_records = _require_array(
            value["changed_records"], "scope_manifest.changed_records"
        )
        record_ids: list[str] = []
        for index, record_value in enumerate(changed_records):
            record = _require_object(record_value, f"changed_records[{index}]")
            _require_fields(
                record,
                f"changed_records[{index}]",
                {"record_id", "source", "change_type", "status", "anchor"},
            )
            record_ids.append(
                _require_string(record["record_id"], f"changed_records[{index}].record_id")
            )
            _validate_anchor(record["anchor"], f"changed_records[{index}].anchor")
        if len(record_ids) != len(set(record_ids)):
            raise ContractError("scope_manifest record_id values must be unique")
    elif kind == "feature_graph":
        features = _require_array(value["features"], "feature_graph.features")
        feature_ids: set[str] = set()
        features_by_id: dict[str, dict[str, Any]] = {}
        feature_unit_links: list[str] = []
        for index, raw_feature in enumerate(features):
            feature = _require_object(raw_feature, f"feature_graph.features[{index}]")
            _require_fields(
                feature,
                f"feature_graph.features[{index}]",
                {"feature_id", "name", "purpose", "review_unit_ids"},
            )
            feature_id = _require_string(
                feature["feature_id"], f"feature_graph.features[{index}].feature_id"
            )
            if feature_id in feature_ids:
                raise ContractError("feature_graph feature_id values must be unique")
            feature_ids.add(feature_id)
            features_by_id[feature_id] = feature
            _require_string(feature["name"], f"feature_graph.features[{index}].name")
            _require_string(
                feature["purpose"], f"feature_graph.features[{index}].purpose"
            )
            feature_unit_links.extend(
                _require_string_array(
                    feature["review_unit_ids"],
                    f"feature_graph.features[{index}].review_unit_ids",
                )
            )
            if "child_feature_ids" in feature:
                _require_string_array(
                    feature["child_feature_ids"],
                    f"feature_graph.features[{index}].child_feature_ids",
                )

        for feature_id, feature in features_by_id.items():
            child_ids = feature.get("child_feature_ids", [])
            if child_ids:
                if feature["review_unit_ids"]:
                    raise ContractError(
                        f"Feature {feature_id} is a logical parent and cannot receive a redundant reviewer pair"
                    )
                subdivision = _require_object(
                    feature.get("subdivision"), f"Feature {feature_id}.subdivision"
                )
                _require_fields(
                    subdivision,
                    f"Feature {feature_id}.subdivision",
                    {"reason", "boundary"},
                )
                _require_string(
                    subdivision["reason"], f"Feature {feature_id}.subdivision.reason"
                )
                if subdivision["boundary"] not in {
                    "behavior",
                    "state_transition",
                    "contract",
                }:
                    raise ContractError(
                        f"Feature {feature_id}.subdivision.boundary is unsupported"
                    )
                for child_id in child_ids:
                    child = features_by_id.get(child_id)
                    if child is None or child.get("parent_feature_id") != feature_id:
                        raise ContractError(
                            f"Feature {feature_id} child relationships must be reciprocal"
                        )
            parent_id = feature.get("parent_feature_id")
            if parent_id is not None:
                _require_string(parent_id, f"Feature {feature_id}.parent_feature_id")
                parent = features_by_id.get(parent_id)
                if parent is None or feature_id not in parent.get("child_feature_ids", []):
                    raise ContractError(
                        f"Feature {feature_id} parent relationship must be reciprocal"
                    )

        for feature_id in feature_ids:
            seen = {feature_id}
            current = features_by_id[feature_id]
            while current.get("parent_feature_id") is not None:
                parent_id = current["parent_feature_id"]
                if parent_id in seen:
                    raise ContractError("Feature Graph parent-child relationships must be acyclic")
                seen.add(parent_id)
                current = features_by_id[parent_id]

        review_units = _require_array(
            value["review_units"], "feature_graph.review_units"
        )
        unit_records: dict[str, set[str]] = {}
        units_by_id: dict[str, dict[str, Any]] = {}
        integration_participants: dict[str, set[str]] = {}
        claimed_records: set[str] = set()
        for index, raw_unit in enumerate(review_units):
            unit = _require_object(raw_unit, f"feature_graph.review_units[{index}]")
            _require_fields(
                unit,
                f"feature_graph.review_units[{index}]",
                {
                    "unit_id",
                    "kind",
                    "name",
                    "changed_record_ids",
                    "executable",
                },
            )
            unit_id = _require_string(
                unit["unit_id"], f"feature_graph.review_units[{index}].unit_id"
            )
            if unit_id in unit_records:
                raise ContractError("feature_graph unit_id values must be unique")
            if unit["kind"] not in {"feature", "integration"}:
                raise ContractError(
                    f"feature_graph.review_units[{index}].kind is unsupported"
                )
            if unit["executable"] is not True:
                raise ContractError(
                    "Feature Graph Review Units must be executable; logical parents are Feature nodes"
                )
            _require_string(unit["name"], f"feature_graph.review_units[{index}].name")
            if "purpose" in unit:
                _require_string(
                    unit["purpose"], f"feature_graph.review_units[{index}].purpose"
                )
            changed_record_ids = _require_string_array(
                unit["changed_record_ids"],
                f"feature_graph.review_units[{index}].changed_record_ids",
            )
            records = set(changed_record_ids)
            duplicate_claims = claimed_records & records
            if duplicate_claims:
                raise ContractError(
                    "feature_graph changed records cannot be claimed by multiple units"
                )
            claimed_records.update(records)
            unit_records[unit_id] = records
            units_by_id[unit_id] = unit
        for unit_id, unit in units_by_id.items():
            if unit["kind"] == "feature":
                feature_id = _require_string(
                    unit.get("feature_id"), f"Review Unit {unit_id}.feature_id"
                )
                if feature_id not in feature_ids:
                    raise ContractError(
                        f"Review Unit {unit_id} refers to an unknown Feature"
                    )
                if unit_id not in features_by_id[feature_id]["review_unit_ids"]:
                    raise ContractError(
                        f"Feature and Review Unit links must be bidirectional for {unit_id}"
                    )
                if "integration_criteria" in unit:
                    raise ContractError(
                        f"Feature Review Unit {unit_id} cannot declare integration criteria"
                    )
                continue
            criteria = _require_object(
                unit.get("integration_criteria"),
                f"Integration Review Unit {unit_id}.integration_criteria",
            )
            _require_fields(
                criteria,
                f"Integration Review Unit {unit_id}.integration_criteria",
                {
                    "participating_unit_ids",
                    "material_contract",
                    "cannot_be_validated_end_to_end_by_participant",
                    "material_impact",
                },
            )
            participants = _require_string_array(
                criteria["participating_unit_ids"],
                f"Integration Review Unit {unit_id}.participating_unit_ids",
            )
            if len(participants) < 2:
                raise ContractError(
                    f"Integration Review Unit {unit_id} must cross at least two feature units"
                )
            integration_participants[unit_id] = set(participants)
            if any(
                participant not in units_by_id
                or units_by_id[participant]["kind"] != "feature"
                for participant in participants
            ):
                raise ContractError(
                    f"Integration Review Unit {unit_id} participants must be feature units"
                )
            _require_string(
                criteria["material_contract"],
                f"Integration Review Unit {unit_id}.material_contract",
            )
            if criteria["cannot_be_validated_end_to_end_by_participant"] is not True:
                raise ContractError(
                    f"Integration Review Unit {unit_id} must attest that no participant can validate the material contract end to end"
                )
            if criteria["material_impact"] not in {
                "behavior",
                "security",
                "data_integrity",
                "concurrency",
                "compatibility",
                "migration",
                "deployment",
            }:
                raise ContractError(
                    f"Integration Review Unit {unit_id}.material_impact is unsupported"
                )
        feature_unit_ids = {
            unit_id for unit_id, unit in units_by_id.items() if unit["kind"] == "feature"
        }
        if len(feature_unit_links) != len(set(feature_unit_links)) or set(
            feature_unit_links
        ) != feature_unit_ids:
            raise ContractError(
                "Feature nodes must reference every Feature Review Unit exactly once"
            )
        for feature_id, feature in features_by_id.items():
            for unit_id in feature["review_unit_ids"]:
                unit = units_by_id.get(unit_id)
                if unit is None or unit.get("feature_id") != feature_id:
                    raise ContractError(
                        f"Feature and Review Unit links must be bidirectional for {unit_id}"
                    )
        ownership = value["ownership"]
        if not isinstance(ownership, dict):
            raise ContractError("feature_graph.ownership must be an object")
        expected_records = set().union(*unit_records.values()) if unit_records else set()
        if set(ownership) != expected_records:
            raise ContractError(
                "feature_graph.ownership must cover every changed record exactly once"
            )
        for record_id, owner in ownership.items():
            if owner not in unit_records or record_id not in unit_records[owner]:
                raise ContractError(
                    f"feature_graph.ownership has an invalid owner for {record_id}"
                )
        edges = _require_array(value["edges"], "feature_graph.edges")
        edge_keys: set[tuple[str, str, str]] = set()
        incoming_material_edges: dict[str, set[str]] = {
            unit_id: set() for unit_id in integration_participants
        }
        for index, raw_edge in enumerate(edges):
            edge = _require_object(raw_edge, f"feature_graph.edges[{index}]")
            _require_fields(
                edge,
                f"feature_graph.edges[{index}]",
                {"kind", "from_unit_id", "to_unit_id"},
            )
            if edge["kind"] != "material_contract":
                raise ContractError(
                    f"feature_graph.edges[{index}].kind is unsupported"
                )
            from_unit_id = _require_string(
                edge["from_unit_id"], f"feature_graph.edges[{index}].from_unit_id"
            )
            to_unit_id = _require_string(
                edge["to_unit_id"], f"feature_graph.edges[{index}].to_unit_id"
            )
            if from_unit_id not in units_by_id or to_unit_id not in units_by_id:
                raise ContractError("Feature Graph edges must connect known Review Units")
            if from_unit_id == to_unit_id:
                raise ContractError("Feature Graph edges cannot be self-referential")
            edge_key = (edge["kind"], from_unit_id, to_unit_id)
            if edge_key in edge_keys:
                raise ContractError("Feature Graph edges must be unique")
            edge_keys.add(edge_key)
            if to_unit_id in incoming_material_edges:
                incoming_material_edges[to_unit_id].add(from_unit_id)
        for unit_id, participants in integration_participants.items():
            if incoming_material_edges[unit_id] != participants:
                raise ContractError(
                    f"Integration Review Unit {unit_id} requires a material edge from every participant"
                )
    elif kind == "review_plan":
        unit_ids = _require_string_array(value["unit_ids"], "review_plan.unit_ids")
        if not isinstance(value["assignments"], list):
            raise ContractError("review_plan.assignments must be an array")
        assignment_pairs: list[tuple[str, str]] = []
        task_ids: list[str] = []
        for index, raw_assignment in enumerate(value["assignments"]):
            assignment = _require_object(
                raw_assignment, f"review_plan.assignments[{index}]"
            )
            _require_fields(
                assignment,
                f"review_plan.assignments[{index}]",
                {"task_id", "unit_id", "role", "attempt"},
            )
            if assignment["role"] not in ROLES:
                raise ContractError(
                    f"review_plan.assignments[{index}].role is unsupported"
                )
            task_ids.append(assignment["task_id"])
            assignment_pairs.append((assignment["unit_id"], assignment["role"]))
        if len(task_ids) != len(set(task_ids)):
            raise ContractError("review_plan task_id values must be unique")
        if len(assignment_pairs) != len(set(assignment_pairs)):
            raise ContractError("review_plan has duplicate unit-role assignments")
        expected_pairs = {
            (unit_id, role) for unit_id in unit_ids for role in ROLES
        }
        if set(assignment_pairs) != expected_pairs:
            raise ContractError("review_plan must assign both required roles to every unit")
    elif kind in {"reviewer_task_envelope", "reviewer_result"}:
        if value["role"] not in ROLES:
            raise ContractError(f"{kind}.role is unsupported: {value['role']!r}")
        record_field = (
            "changed_record_ids"
            if kind == "reviewer_task_envelope"
            else "inspected_record_ids"
        )
        _require_string_array(value[record_field], f"{kind}.{record_field}")
        if kind == "reviewer_task_envelope":
            if any(
                field in value
                for field in ("peer_result", "peer_conclusions", "reviewer_results")
            ):
                raise ContractError(
                    "reviewer_task_envelope cannot contain peer reviewer conclusions"
                )
            if value["core_policy"] != CORE_POLICY:
                raise ContractError(
                    "reviewer_task_envelope core policy must match the immutable policy"
                )
            if value["output_contract"] != "reviewer_result@2":
                raise ContractError(
                    "reviewer_task_envelope.output_contract must be reviewer_result@2"
                )
        if kind == "reviewer_result":
            if value["status"] not in {"completed", "failed", "incomplete"}:
                raise ContractError(
                    f"reviewer_result.status is unsupported: {value['status']!r}"
                )
            findings = _require_array(
                value["candidate_findings"], "reviewer_result.candidate_findings"
            )
            _require_array(
                value["supporting_evidence"], "reviewer_result.supporting_evidence"
            )
            _require_array(value["proposed_checks"], "reviewer_result.proposed_checks")
            _require_array(
                value["unresolved_assumptions"],
                "reviewer_result.unresolved_assumptions",
            )
            finding_fields = {
                "finding_id",
                "title",
                "anchor",
                "changed_scope_causality",
                "reachable_scenario",
                "impact",
                "checked_context",
                "severity",
                "confidence",
                "correction_direction",
            }
            for index, raw_finding in enumerate(findings):
                finding = _require_object(
                    raw_finding, f"reviewer_result.candidate_findings[{index}]"
                )
                _require_fields(
                    finding,
                    f"reviewer_result.candidate_findings[{index}]",
                    finding_fields,
                )
                for field in finding_fields - {"anchor"}:
                    _require_string(
                        finding[field],
                        f"reviewer_result.candidate_findings[{index}].{field}",
                    )
                _validate_anchor(
                    finding["anchor"],
                    f"reviewer_result.candidate_findings[{index}].anchor",
                )
                if finding["severity"] not in {"critical", "high", "medium", "low"}:
                    raise ContractError(
                        f"reviewer_result.candidate_findings[{index}].severity is unsupported"
                    )
                if finding["confidence"] not in {"high", "medium"}:
                    raise ContractError(
                        f"reviewer_result.candidate_findings[{index}].confidence is unsupported"
                    )
    elif kind == "coverage_ledger":
        if not isinstance(value["records"], list):
            raise ContractError("coverage_ledger.records must be an array")
        record_ids: list[str] = []
        for index, raw_entry in enumerate(value["records"]):
            entry = _require_object(raw_entry, f"coverage_ledger.records[{index}]")
            _require_fields(
                entry,
                f"coverage_ledger.records[{index}]",
                {"record_id", "primary_owner", "role_status", "attempts", "covered"},
            )
            role_status = _require_object(
                entry["role_status"], f"coverage_ledger.records[{index}].role_status"
            )
            if set(role_status) != set(ROLES):
                raise ContractError(
                    f"coverage_ledger.records[{index}] must track both roles"
                )
            _require_string(
                entry["record_id"], f"coverage_ledger.records[{index}].record_id"
            )
            _require_string(
                entry["primary_owner"],
                f"coverage_ledger.records[{index}].primary_owner",
            )
            for role in ROLES:
                if role_status[role] not in {
                    "pending",
                    "completed",
                    "failed",
                    "incomplete",
                    "not_selected",
                }:
                    raise ContractError(
                        f"coverage_ledger.records[{index}].role_status.{role} is unsupported"
                    )
            attempts = _require_object(
                entry["attempts"], f"coverage_ledger.records[{index}].attempts"
            )
            if set(attempts) != set(ROLES):
                raise ContractError(
                    f"coverage_ledger.records[{index}].attempts must track both roles"
                )
            for role in ROLES:
                if (
                    not isinstance(attempts[role], int)
                    or isinstance(attempts[role], bool)
                    or attempts[role] < 0
                ):
                    raise ContractError(
                        f"coverage_ledger.records[{index}].attempts.{role} must be a non-negative integer"
                    )
            if not isinstance(entry["covered"], bool):
                raise ContractError(
                    f"coverage_ledger.records[{index}].covered must be a boolean"
                )
            pair_completed = all(role_status[role] == "completed" for role in ROLES)
            if entry["covered"] != pair_completed:
                raise ContractError(
                    f"coverage_ledger.records[{index}] cannot claim coverage until both roles complete"
                )
            record_ids.append(entry["record_id"])
        if len(record_ids) != len(set(record_ids)):
            raise ContractError("coverage_ledger record_id values must be unique")
        if not isinstance(value["complete"], bool):
            raise ContractError("coverage_ledger.complete must be a boolean")
        all_covered = bool(value["records"]) and all(
            entry["covered"] for entry in value["records"]
        )
        if value["complete"] != all_covered:
            raise ContractError(
                "coverage_ledger.complete must match complete paired coverage"
            )
    return value


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:16]}"


def _graph_revision(feature_graph: dict[str, Any]) -> str:
    payload = json.dumps(
        feature_graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _build_bundle(
    scope: dict[str, Any],
    feature_graph: dict[str, Any],
    environment: dict[str, Any],
    budget_decision: dict[str, Any] | None = None,
    previous_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_contract("feature_graph", feature_graph)
    scope_id = scope["scope_id"]
    if feature_graph["scope_id"] != scope_id:
        raise ContractError("Feature Graph does not share the frozen Scope Manifest scope_id")
    scope_record_ids = {
        record["record_id"] for record in scope["changed_records"]
    }
    if not scope_record_ids:
        raise ContractError("cannot build a review plan for an empty scope")
    if set(feature_graph["ownership"]) != scope_record_ids:
        raise ContractError(
            "Feature Graph ownership must cover every Scope Manifest changed record"
        )

    graph_units = [
        unit for unit in feature_graph["review_units"] if unit.get("executable") is True
    ]
    if not graph_units:
        raise ContractError("cannot build a review plan without an executable Review Unit")
    graph_revision = _graph_revision(feature_graph)
    graph_unit_ids = [unit["unit_id"] for unit in graph_units]
    preflight_sequence = 1
    preflight_trigger = "initial"
    graph_changed = False
    added_unit_ids: list[str] = []
    previous_graph_revision: str | None = None
    if previous_plan is not None:
        validate_contract("review_plan", previous_plan)
        if previous_plan["scope_id"] != scope_id:
            raise ContractError(
                "previous_review_plan does not share the frozen Scope Manifest scope_id"
            )
        previous_preflight = _require_object(
            previous_plan["preflight"], "previous_review_plan.preflight"
        )
        previous_graph_revision = _require_string(
            previous_preflight.get("graph_revision"),
            "previous_review_plan.preflight.graph_revision",
        )
        preflight_sequence = _require_positive_integer(
            previous_preflight.get("sequence"),
            "previous_review_plan.preflight.sequence",
        ) + 1
        previous_graph_unit_ids = set(
            _require_string_array(
                previous_preflight.get("graph_unit_ids"),
                "previous_review_plan.preflight.graph_unit_ids",
            )
        )
        added_unit_ids = [
            unit_id for unit_id in graph_unit_ids if unit_id not in previous_graph_unit_ids
        ]
        graph_changed = previous_graph_revision != graph_revision
        added_unit_kinds = {
            unit["unit_id"]: unit["kind"] for unit in graph_units
        }
        preflight_trigger = (
            "late_integration_unit"
            if any(added_unit_kinds[unit_id] == "integration" for unit_id in added_unit_ids)
            else "graph_changed" if graph_changed else "manual_repreflight"
        )
    effective_unit_limit = STANDARD_PROFILE["max_executable_units"]
    effective_pass_limit = STANDARD_PROFILE["max_reviewer_passes"]
    if budget_decision is not None:
        decision_kind = budget_decision.get("kind")
        if decision_kind not in {
            "scope_reduction",
            "budget_expansion",
            "paired_partial_review",
        }:
            raise ContractError("budget_decision.kind is unsupported")
        if budget_decision.get("approved") is not True:
            raise ContractError("budget_decision must be explicitly approved")
        if budget_decision.get("graph_revision") != graph_revision:
            raise ContractError(
                "budget_decision must be approved for the current graph revision"
            )
        if decision_kind == "budget_expansion":
            effective_unit_limit = _require_positive_integer(
                budget_decision.get("max_executable_units"),
                "budget_decision.max_executable_units",
            )
            effective_pass_limit = _require_positive_integer(
                budget_decision.get("max_reviewer_passes"),
                "budget_decision.max_reviewer_passes",
            )
            if (
                effective_unit_limit < STANDARD_PROFILE["max_executable_units"]
                or effective_pass_limit < STANDARD_PROFILE["max_reviewer_passes"]
            ):
                raise ContractError(
                    "budget expansion cannot lower the standard profile limits"
                )
    units = graph_units
    partial_review = (
        budget_decision is not None
        and budget_decision["kind"] == "paired_partial_review"
    )
    if partial_review:
        if budget_decision.get("selection_basis") != "risk_prioritized":
            raise ContractError(
                "paired partial review must declare a risk_prioritized selection_basis"
            )
        selected_unit_ids = _require_string_array(
            budget_decision.get("selected_unit_ids"),
            "budget_decision.selected_unit_ids",
        )
        if not selected_unit_ids:
            raise ContractError("paired partial review must select at least one whole unit")
        units_by_id = {unit["unit_id"]: unit for unit in graph_units}
        if not set(selected_unit_ids) <= set(units_by_id):
            raise ContractError(
                "paired partial review selected_unit_ids must name executable Review Units"
            )
        units = [units_by_id[unit_id] for unit_id in selected_unit_ids]
    selected_unit_id_set = {unit["unit_id"] for unit in units}
    unselected_unit_ids = [
        unit["unit_id"]
        for unit in graph_units
        if unit["unit_id"] not in selected_unit_id_set
    ]
    unselected_record_ids = [
        record["record_id"]
        for record in scope["changed_records"]
        if feature_graph["ownership"][record["record_id"]]
        not in selected_unit_id_set
    ]
    unit_ids = [unit["unit_id"] for unit in units]
    usable_context_tokens: int | None = None
    if "usable_context_tokens" in environment:
        usable_context_tokens = _require_positive_integer(
            environment["usable_context_tokens"], "environment.usable_context_tokens"
        )
    unit_context_estimates: dict[str, int] = {}
    estimates_are_declared = True
    for unit in units:
        estimate = unit.get("estimated_context_tokens")
        if estimate is None:
            estimates_are_declared = False
            estimate = (
                math.floor(
                    usable_context_tokens
                    * (1 - STANDARD_PROFILE["context_reserve_ratio"])
                )
                if usable_context_tokens is not None
                else 1
            )
        unit_context_estimates[unit["unit_id"]] = _require_positive_integer(
            estimate, f"Review Unit {unit['unit_id']}.estimated_context_tokens"
        )

    raw_resource_limits = environment.get("resource_limits", {})
    resource_limits = _require_object(
        raw_resource_limits, "environment.resource_limits"
    )
    user_limits = _require_object(
        resource_limits.get("user", {}), "environment.resource_limits.user"
    )
    host_limits = _require_object(
        resource_limits.get("host", {}), "environment.resource_limits.host"
    )
    effective_limits = dict(user_limits)
    for limit_name, host_value in host_limits.items():
        user_value = effective_limits.get(limit_name)
        effective_limits[limit_name] = (
            min(user_value, host_value)
            if isinstance(user_value, (int, float))
            and not isinstance(user_value, bool)
            and isinstance(host_value, (int, float))
            and not isinstance(host_value, bool)
            else host_value
        )
    assignments: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for unit in units:
        context_record_ids = _require_string_array(
            unit.get("context_record_ids", []),
            f"Review Unit {unit['unit_id']}.context_record_ids",
        )
        if not set(context_record_ids) <= scope_record_ids:
            raise ContractError(
                f"Review Unit {unit['unit_id']} context must come from the Scope Manifest"
            )
        for role in ROLES:
            task_id = _stable_id(
                "task", scope_id, graph_revision, unit["unit_id"], role
            )
            assignments.append(
                {
                    "task_id": task_id,
                    "unit_id": unit["unit_id"],
                    "role": role,
                    "attempt": 1,
                }
            )
            envelopes.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "task_id": task_id,
                    "scope_id": scope_id,
                    "graph_revision": graph_revision,
                    "unit_id": unit["unit_id"],
                    "role": role,
                    "role_mandate": (
                        "Attack business logic, state transitions, boundaries, and error paths."
                        if role == "internal_behavior"
                        else "Attack callers, authorization, data, concurrency, compatibility, and deployment contracts."
                    ),
                    "changed_record_ids": list(unit["changed_record_ids"]),
                    "context_record_ids": context_record_ids,
                    "core_policy": CORE_POLICY,
                    "output_contract": "reviewer_result@2",
                    "budget": {
                        "estimated_context_tokens": unit_context_estimates[
                            unit["unit_id"]
                        ],
                        "resource_limits": effective_limits,
                    },
                }
            )

    available_slots = environment.get(
        "available_agent_slots", STANDARD_PROFILE["max_active_reviewers"] + 1
    )
    available_slots = _require_positive_integer(
        available_slots, "environment.available_agent_slots"
    )
    reviewer_passes = len(assignments)
    effective_concurrency = min(
        STANDARD_PROFILE["max_active_reviewers"],
        max(available_slots - 1, 0),
        reviewer_passes,
    )
    waves = (
        math.ceil(reviewer_passes / effective_concurrency)
        if effective_concurrency
        else None
    )
    context_budget = {
        "usable_tokens": usable_context_tokens,
        "reserved_tokens": (
            math.ceil(
                usable_context_tokens * STANDARD_PROFILE["context_reserve_ratio"]
            )
            if usable_context_tokens is not None
            else None
        ),
        "max_supplied_tokens_per_unit": (
            math.floor(
                usable_context_tokens
                * (1 - STANDARD_PROFILE["context_reserve_ratio"])
            )
            if usable_context_tokens is not None
            else None
        ),
        "largest_unit_estimate_tokens": max(unit_context_estimates.values()),
        "estimate_kind": "declared" if estimates_are_declared else "conservative",
    }
    blocking_reasons: list[str] = []
    if len(units) > effective_unit_limit:
        blocking_reasons.append("standard_unit_limit_exceeded")
    if reviewer_passes > effective_pass_limit:
        blocking_reasons.append("standard_reviewer_pass_limit_exceeded")
    max_supplied_tokens = context_budget["max_supplied_tokens_per_unit"]
    if isinstance(max_supplied_tokens, int):
        blocking_reasons.extend(
            f"unit_context_overflow:{unit_id}"
            for unit_id, estimate in unit_context_estimates.items()
            if estimate > max_supplied_tokens
        )
    if not effective_concurrency:
        blocking_reasons.append("no_reviewer_capacity_after_main_agent_reservation")
    dispatch_ready = not blocking_reasons
    review_plan = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "policy_version": CORE_POLICY["version"],
        "unit_ids": unit_ids,
        "assignments": assignments,
        "preflight": {
            "status": (
                "ready_partial"
                if dispatch_ready and partial_review
                else "ready" if dispatch_ready else "paused"
            ),
            "executable_units": len(graph_units),
            "graph_executable_units": len(graph_units),
            "selected_executable_units": len(units),
            "required_reviewer_passes": reviewer_passes,
            "effective_concurrency": effective_concurrency,
            "waves": waves,
            "context_budget": context_budget,
            "resource_limits": {
                "user": user_limits,
                "host": host_limits,
                "effective": effective_limits,
            },
            "blocking_reasons": blocking_reasons,
            "standard_profile": STANDARD_PROFILE,
            "effective_unit_limit": effective_unit_limit,
            "effective_reviewer_pass_limit": effective_pass_limit,
            "unselected_unit_ids": unselected_unit_ids,
            "unselected_record_ids": unselected_record_ids,
            "sequence": preflight_sequence,
            "trigger": preflight_trigger,
            "graph_revision": graph_revision,
            "graph_changed": graph_changed,
            "graph_unit_ids": graph_unit_ids,
            "added_unit_ids": added_unit_ids,
        },
    }
    if previous_graph_revision is not None:
        review_plan["preflight"]["previous_graph_revision"] = previous_graph_revision
    if budget_decision is not None:
        review_plan["preflight"]["budget_decision"] = budget_decision
    if any(reason.startswith("standard_") for reason in blocking_reasons):
        review_plan["preflight"]["overflow_choices"] = [
            "scope_reduction",
            "budget_expansion",
            "paired_partial_review",
        ]
    if any(reason.startswith("unit_context_overflow:") for reason in blocking_reasons):
        review_plan["preflight"]["required_action"] = (
            "subdivide_by_behavior_state_transition_or_contract"
        )
    if not dispatch_ready:
        envelopes = []
    coverage_ledger = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "records": [
            {
                "record_id": record["record_id"],
                "primary_owner": feature_graph["ownership"][record["record_id"]],
                "role_status": {
                    role: (
                        "pending"
                        if feature_graph["ownership"][record["record_id"]]
                        in selected_unit_id_set
                        else "not_selected"
                    )
                    for role in ROLES
                },
                "attempts": {role: 0 for role in ROLES},
                "covered": False,
                "selected_for_review": (
                    feature_graph["ownership"][record["record_id"]]
                    in selected_unit_id_set
                ),
                "uncovered_reason": (
                    None
                    if feature_graph["ownership"][record["record_id"]]
                    in selected_unit_id_set
                    else "not_selected_by_partial_review"
                ),
            }
            for record in scope["changed_records"]
        ],
        "uncovered_record_ids": unselected_record_ids,
        "complete": False,
    }

    validate_contract("review_plan", review_plan)
    for envelope in envelopes:
        validate_contract("reviewer_task_envelope", envelope)
    validate_contract("coverage_ledger", coverage_ledger)
    return {
        "scope_manifest": scope,
        "feature_graph": feature_graph,
        "review_plan": review_plan,
        "reviewer_task_envelopes": envelopes,
        "coverage_ledger": coverage_ledger,
    }


def build_graph(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "graph build input")
    _require_fields(
        value,
        "graph build input",
        {"scope_manifest", "feature_graph", "environment"},
    )
    scope = validate_contract("scope_manifest", value["scope_manifest"])
    environment = _require_object(value["environment"], "environment")
    feature_graph = _require_object(value["feature_graph"], "feature_graph")
    budget_decision = (
        _require_object(value["budget_decision"], "budget_decision")
        if "budget_decision" in value
        else None
    )
    previous_plan = (
        _require_object(value["previous_review_plan"], "previous_review_plan")
        if "previous_review_plan" in value
        else None
    )
    return _build_bundle(
        scope, feature_graph, environment, budget_decision, previous_plan
    )


def build_one_unit(
    scope_value: object, *, unit_id: str, feature_name: str
) -> dict[str, Any]:
    scope = validate_contract("scope_manifest", scope_value)
    scope_id = scope["scope_id"]
    record_ids = [record["record_id"] for record in scope["changed_records"]]
    if not record_ids:
        raise ContractError("cannot build a Review Unit for an empty scope")

    feature_id = _stable_id("feature", scope_id, feature_name)
    feature_graph = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "features": [
            {
                "feature_id": feature_id,
                "name": feature_name,
                "purpose": "Localized behavior represented by the resolved change",
                "review_unit_ids": [unit_id],
            }
        ],
        "review_units": [
            {
                "unit_id": unit_id,
                "feature_id": feature_id,
                "kind": "feature",
                "name": feature_name,
                "purpose": "Review localized behavior represented by the resolved change",
                "changed_record_ids": record_ids,
                "context_record_ids": [],
                "executable": True,
            }
        ],
        "ownership": {record_id: unit_id for record_id in record_ids},
        "edges": [],
    }

    return _build_bundle(scope, feature_graph, {})


def complete_one_unit(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "completion input")
    _require_fields(value, "completion input", {"bundle", "reviewer_results"})
    bundle = _require_object(value["bundle"], "bundle")
    _require_fields(
        bundle,
        "bundle",
        {
            "scope_manifest",
            "feature_graph",
            "review_plan",
            "reviewer_task_envelopes",
            "coverage_ledger",
        },
    )
    scope = validate_contract("scope_manifest", bundle["scope_manifest"])
    graph = validate_contract("feature_graph", bundle["feature_graph"])
    plan = validate_contract("review_plan", bundle["review_plan"])
    ledger = validate_contract("coverage_ledger", bundle["coverage_ledger"])
    if any(item["scope_id"] != scope["scope_id"] for item in (graph, plan, ledger)):
        raise ContractError("bundle contracts do not share one frozen scope_id")
    units = graph["review_units"]
    if not isinstance(units, list) or len(units) != 1:
        raise ContractError("complete-one-unit requires exactly one Review Unit")
    unit = _require_object(units[0], "feature_graph.review_units[0]")
    _require_fields(unit, "feature_graph.review_units[0]", {"unit_id", "changed_record_ids"})
    record_ids = set(unit["changed_record_ids"])
    scope_record_ids = {record["record_id"] for record in scope["changed_records"]}
    if record_ids != scope_record_ids or set(graph["ownership"]) != scope_record_ids:
        raise ContractError(
            "Feature Graph ownership must cover every Scope Manifest changed record"
        )
    if graph["ownership"] != {
        record_id: unit["unit_id"] for record_id in scope_record_ids
    }:
        raise ContractError("Feature Graph ownership does not match the localized unit")
    if plan["unit_ids"] != [unit["unit_id"]]:
        raise ContractError("Review Plan units do not match the Feature Graph")
    if plan["policy_version"] != CORE_POLICY["version"]:
        raise ContractError("Review Plan policy version does not match the core policy")
    expected_assignments = {
        assignment["role"]: assignment
        for assignment in plan["assignments"]
        if assignment.get("unit_id") == unit["unit_id"]
    }
    if set(expected_assignments) != set(ROLES):
        raise ContractError("the Review Unit must have exactly the two required roles")

    raw_envelopes = _require_array(
        bundle["reviewer_task_envelopes"], "bundle.reviewer_task_envelopes"
    )
    if len(raw_envelopes) != len(ROLES):
        raise ContractError("the Review Unit must have exactly two task envelopes")
    envelopes: dict[str, dict[str, Any]] = {}
    for raw_envelope in raw_envelopes:
        envelope = validate_contract("reviewer_task_envelope", raw_envelope)
        role = envelope["role"]
        if role in envelopes:
            raise ContractError(f"duplicate reviewer task envelope for role {role}")
        assignment = expected_assignments.get(role)
        if assignment is None or envelope["task_id"] != assignment["task_id"]:
            raise ContractError(f"reviewer task envelope does not match plan for {role}")
        if envelope["scope_id"] != scope["scope_id"]:
            raise ContractError(f"reviewer task envelope scope does not match for {role}")
        if envelope["unit_id"] != unit["unit_id"]:
            raise ContractError(f"reviewer task envelope unit does not match for {role}")
        if set(envelope["changed_record_ids"]) != record_ids:
            raise ContractError(
                f"reviewer task envelope evidence does not match owned scope for {role}"
            )
        envelopes[role] = envelope
    if set(envelopes) != set(ROLES):
        raise ContractError("reviewer task envelopes must cover both required roles")

    raw_results = value["reviewer_results"]
    if not isinstance(raw_results, list):
        raise ContractError("reviewer_results must be an array")
    results: dict[str, dict[str, Any]] = {}
    for raw_result in raw_results:
        result = validate_contract("reviewer_result", raw_result)
        role = result["role"]
        if role in results:
            raise ContractError(f"duplicate reviewer result for role {role}")
        assignment = expected_assignments.get(role)
        if assignment is None:
            raise ContractError(f"unplanned reviewer role: {role}")
        if result["task_id"] != assignment["task_id"]:
            raise ContractError(f"reviewer result task_id does not match plan for {role}")
        if result["scope_id"] != scope["scope_id"]:
            raise ContractError(f"reviewer result scope_id does not match for {role}")
        if result["unit_id"] != unit["unit_id"]:
            raise ContractError(f"reviewer result unit_id does not match for {role}")
        if result["status"] != "completed":
            raise ContractError(f"reviewer role {role} did not complete successfully")
        if set(result["inspected_record_ids"]) != record_ids:
            raise ContractError(f"reviewer role {role} did not inspect the complete owned scope")
        frozen_anchors = {
            json.dumps(record["anchor"], sort_keys=True, separators=(",", ":"))
            for record in scope["changed_records"]
        }
        for candidate in result["candidate_findings"]:
            candidate_anchor = json.dumps(
                candidate["anchor"], sort_keys=True, separators=(",", ":")
            )
            if candidate_anchor not in frozen_anchors:
                raise ContractError(
                    f"reviewer role {role} candidate anchor is not in the frozen Scope Manifest"
                )
        results[role] = result
    if set(results) != set(ROLES):
        missing = sorted(set(ROLES) - results.keys())
        raise ContractError(f"missing successful reviewer roles: {', '.join(missing)}")

    completed_ledger = copy.deepcopy(ledger)
    ledger_ids = {entry["record_id"] for entry in completed_ledger["records"]}
    if ledger_ids != record_ids:
        raise ContractError("Coverage Ledger records do not match Review Unit ownership")
    for entry in completed_ledger["records"]:
        if entry["primary_owner"] != graph["ownership"][entry["record_id"]]:
            raise ContractError("Coverage Ledger primary owner does not match Feature Graph")
        entry["role_status"] = {role: "completed" for role in ROLES}
        entry["attempts"] = {role: 1 for role in ROLES}
        entry["covered"] = True
    completed_ledger["complete"] = True

    candidates = [
        {"task_id": result["task_id"], "role": role, "candidate": candidate}
        for role, result in results.items()
        for candidate in result["candidate_findings"]
    ]
    return {
        "coverage_ledger": completed_ledger,
        "candidate_findings": candidates,
        "main_agent_action": "verify, deduplicate, calibrate, and report through the evidence gate",
    }


def _read_stdin() -> object:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ContractError(f"stdin is not valid JSON: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate one JSON contract from stdin")
    validate.add_argument("--kind", required=True)
    build = subparsers.add_parser(
        "build-one-unit", help="build the localized one-Review-Unit v2 contract bundle"
    )
    build.add_argument("--unit-id", required=True)
    build.add_argument("--feature-name", required=True)
    subparsers.add_parser(
        "build-graph",
        help="validate a semantic Feature Graph and build its paired review plan",
    )
    subparsers.add_parser(
        "complete-one-unit",
        help="accept exactly one successful isolated result for each planned role",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        value = _read_stdin()
        if args.command == "validate":
            output = validate_contract(args.kind, value)
        elif args.command == "build-one-unit":
            output = build_one_unit(
                value, unit_id=args.unit_id, feature_name=args.feature_name
            )
        elif args.command == "build-graph":
            output = build_graph(value)
        else:
            output = complete_one_unit(value)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (KeyError, TypeError) as exc:
        print(f"ERROR: malformed contract value: {exc}", file=sys.stderr)
        return 2
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
