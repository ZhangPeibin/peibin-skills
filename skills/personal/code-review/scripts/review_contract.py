#!/usr/bin/env python3
"""Build and validate ephemeral Code Review v2 workflow contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from typing import Any


SCHEMA_VERSION = "2.0"
RESILIENT_SCHEMA_VERSION = "2.1"
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
LENS_VERSION_MAJOR = 1
LENS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*$")


def _risk_lens(lens_id: str, evidence_tag: str, question: str) -> dict[str, Any]:
    return {
        "lens_id": lens_id,
        "version": "1.0",
        "kind": "risk",
        "trust_source": "installed",
        "applicability": {"evidence_tags": [evidence_tag]},
        "supported_roles": list(ROLES),
        "targeted_questions": [question],
        "evidence_needs": ["Changed boundary, reachable callers, guards, and contracts"],
        "context_lookups": ["Inspect only the relevant boundary and consumers read-only"],
        "stop_conditions": ["Stop when the suspected path is unreachable"],
        "estimated_context_tokens": 400,
    }


BUILTIN_LENSES = (
    _risk_lens(
        "risk/security-trust",
        "risk:security",
        "Can attacker-controlled input or identity context bypass a trust boundary?",
    ),
    _risk_lens(
        "risk/data-migration",
        "risk:data",
        "Can rollout, rollback, existing rows, or partial execution violate data integrity?",
    ),
    _risk_lens(
        "risk/concurrency-reliability",
        "risk:concurrency",
        "Can an interleaving, retry, timeout, or duplicate delivery violate an invariant?",
    ),
    _risk_lens(
        "risk/contract-compatibility",
        "risk:compatibility",
        "Can a caller, stored representation, or deployment order observe an incompatible contract?",
    ),
)


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


def _validate_proposed_check(raw_value: object, name: str) -> dict[str, Any]:
    proposal = _require_object(raw_value, name)
    _require_fields(
        proposal,
        name,
        {
            "proposal_id",
            "argv",
            "purpose",
            "scope",
            "requires_network",
            "requires_credentials",
            "writes_repository",
        },
    )
    _require_string(proposal["proposal_id"], f"{name}.proposal_id")
    argv = _require_string_array(proposal["argv"], f"{name}.argv", unique=False)
    if not argv:
        raise ContractError(f"{name}.argv cannot be empty")
    _require_string(proposal["purpose"], f"{name}.purpose")
    if proposal["scope"] not in {"focused", "unit", "broad"}:
        raise ContractError(f"{name}.scope is unsupported")
    for field in ("requires_network", "requires_credentials", "writes_repository"):
        if not isinstance(proposal[field], bool):
            raise ContractError(f"{name}.{field} must be a boolean")
    return proposal


def _role_mandate(role: str) -> str:
    return (
        "Attack business logic, state transitions, boundaries, and error paths."
        if role == "internal_behavior"
        else "Attack callers, authorization, data, concurrency, compatibility, and deployment contracts."
    )


def _validate_task_budget(raw_value: object, name: str) -> dict[str, Any]:
    budget = _require_object(raw_value, name)
    _require_fields(budget, name, {"estimated_context_tokens", "resource_limits"})
    _require_positive_integer(
        budget["estimated_context_tokens"], f"{name}.estimated_context_tokens"
    )
    _require_object(budget["resource_limits"], f"{name}.resource_limits")
    return budget


def _validate_lens(raw_value: object, name: str) -> dict[str, Any]:
    lens = _require_object(raw_value, name)
    allowed_fields = {
        "lens_id",
        "version",
        "kind",
        "trust_source",
        "applicability",
        "supported_roles",
        "targeted_questions",
        "evidence_needs",
        "context_lookups",
        "stop_conditions",
        "estimated_context_tokens",
    }
    _require_fields(lens, name, allowed_fields)
    unexpected = sorted(set(lens) - allowed_fields)
    if unexpected:
        raise ContractError(
            f"{name} cannot extend or override immutable core policy fields: {', '.join(unexpected)}"
        )
    lens_id = _require_string(lens["lens_id"], f"{name}.lens_id")
    if LENS_ID_RE.fullmatch(lens_id) is None:
        raise ContractError(f"{name}.lens_id must be namespaced as namespace/name")
    version = _require_string(lens["version"], f"{name}.version")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise ContractError(f"{name}.version is invalid") from exc
    if major != LENS_VERSION_MAJOR:
        raise ContractError(
            f"{name}.version major {major} is unsupported; expected {LENS_VERSION_MAJOR}"
        )
    if lens["kind"] not in {"language", "framework", "risk"}:
        raise ContractError(f"{name}.kind is unsupported")
    if lens["trust_source"] not in {"installed", "system", "explicit_user"}:
        raise ContractError(
            f"{name}.trust_source cannot trust repository-provided content"
        )
    supported_roles = _require_string_array(
        lens["supported_roles"], f"{name}.supported_roles"
    )
    if not supported_roles or not set(supported_roles) <= set(ROLES):
        raise ContractError(f"{name}.supported_roles must name one or both core roles")
    for field in (
        "targeted_questions",
        "evidence_needs",
        "context_lookups",
        "stop_conditions",
    ):
        values = _require_string_array(lens[field], f"{name}.{field}")
        if not values:
            raise ContractError(f"{name}.{field} cannot be empty")
    _require_positive_integer(
        lens["estimated_context_tokens"], f"{name}.estimated_context_tokens"
    )
    applicability = _require_object(lens["applicability"], f"{name}.applicability")
    allowed_evidence = {"path_suffixes", "change_types", "evidence_tags"}
    unexpected_evidence = sorted(set(applicability) - allowed_evidence)
    if unexpected_evidence:
        raise ContractError(
            f"{name}.applicability contains unsupported evidence: {', '.join(unexpected_evidence)}"
        )
    evidence_count = 0
    for field in allowed_evidence:
        if field in applicability:
            evidence_count += len(
                _require_string_array(
                    applicability[field], f"{name}.applicability.{field}"
                )
            )
    if evidence_count == 0:
        raise ContractError(f"{name}.applicability requires resolved change evidence")
    return lens


def _lens_applies(
    lens: dict[str, Any], unit: dict[str, Any], records_by_id: dict[str, dict[str, Any]]
) -> bool:
    applicability = lens["applicability"]
    unit_records = [
        records_by_id[record_id]
        for record_id in (
            list(unit["changed_record_ids"]) + list(unit.get("context_record_ids", []))
        )
    ]
    paths = {record["anchor"]["path"] for record in unit_records}
    change_types = {record["change_type"] for record in unit_records}
    evidence_tags = set(unit.get("applicability_evidence", []))
    return (
        any(
            path.endswith(suffix)
            for suffix in applicability.get("path_suffixes", [])
            for path in paths
        )
        or bool(change_types & set(applicability.get("change_types", [])))
        or bool(evidence_tags & set(applicability.get("evidence_tags", [])))
    )


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


def _schema_minor(value: dict[str, Any]) -> int:
    version = value["schema_version"]
    parts = version.split(".", 1)
    if len(parts) == 1:
        return 0
    try:
        return int(parts[1])
    except ValueError as exc:
        raise ContractError(f"schema_version is invalid: {version!r}") from exc


def validate_contract(kind: str, raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, kind)
    if kind == "lens":
        return _validate_lens(value, "lens")
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
            if "applicability_evidence" in unit:
                _require_string_array(
                    unit["applicability_evidence"],
                    f"feature_graph.review_units[{index}].applicability_evidence",
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
        preflight = _require_object(value["preflight"], "review_plan.preflight")
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
            current_plan = _schema_minor(value) >= 1 or "lens_revision" in preflight
            if current_plan:
                _require_fields(
                    assignment,
                    f"review_plan.assignments[{index}]",
                    {"role_mandate", "budget"},
                )
            if "role_mandate" in assignment:
                _require_string(
                    assignment["role_mandate"],
                    f"review_plan.assignments[{index}].role_mandate",
                )
            if "budget" in assignment:
                _validate_task_budget(
                    assignment["budget"],
                    f"review_plan.assignments[{index}].budget",
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
        if preflight.get("status") == "ready_degraded":
            if assignment_pairs:
                raise ContractError(
                    "a degraded single-agent plan cannot claim paired assignments"
                )
        elif set(assignment_pairs) != expected_pairs:
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
            allowed_fields = {
                "schema_version",
                "task_id",
                "scope_id",
                "graph_revision",
                "unit_id",
                "role",
                "role_mandate",
                "changed_record_ids",
                "context_record_ids",
                "active_lenses",
                "core_policy",
                "output_contract",
                "budget",
                "attempt",
                "fresh_context_required",
                "excluded_context",
            }
            unsupported_fields = sorted(set(value) - allowed_fields)
            if unsupported_fields:
                raise ContractError(
                    "reviewer_task_envelope has unsupported fields: "
                    + ", ".join(unsupported_fields)
                )
            if _schema_minor(value) >= 1:
                _require_fields(
                    value,
                    "reviewer_task_envelope",
                    {
                        "graph_revision",
                        "role_mandate",
                        "context_record_ids",
                        "active_lenses",
                        "budget",
                        "attempt",
                    },
                )
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
            if "role_mandate" in value and value["role_mandate"] != _role_mandate(value["role"]):
                raise ContractError(
                    "reviewer_task_envelope role mandate must match its required role"
                )
            if "budget" in value:
                _validate_task_budget(value["budget"], "reviewer_task_envelope.budget")
            if "attempt" in value and value["attempt"] not in {1, 2}:
                raise ContractError("reviewer_task_envelope.attempt must be 1 or 2")
            if "fresh_context_required" in value and not isinstance(
                value["fresh_context_required"], bool
            ):
                raise ContractError(
                    "reviewer_task_envelope.fresh_context_required must be a boolean"
                )
            if "excluded_context" in value:
                _require_string_array(
                    value["excluded_context"],
                    "reviewer_task_envelope.excluded_context",
                )
            if value["output_contract"] != "reviewer_result@2":
                raise ContractError(
                    "reviewer_task_envelope.output_contract must be reviewer_result@2"
                )
            active_lenses = _require_array(
                value.get("active_lenses", []),
                "reviewer_task_envelope.active_lenses",
            )
            active_lens_ids: list[str] = []
            for index, raw_lens in enumerate(active_lenses):
                lens = _validate_lens(
                    raw_lens, f"reviewer_task_envelope.active_lenses[{index}]"
                )
                if value["role"] not in lens["supported_roles"]:
                    raise ContractError(
                        "reviewer task envelope includes a lens that does not support its role"
                    )
                active_lens_ids.append(lens["lens_id"])
            if len(active_lens_ids) != len(set(active_lens_ids)):
                raise ContractError(
                    "reviewer task envelope active lens IDs must be unique"
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
            proposed_checks = _require_array(
                value["proposed_checks"], "reviewer_result.proposed_checks"
            )
            proposal_ids: list[str] = []
            for index, raw_proposal in enumerate(proposed_checks):
                proposal = _validate_proposed_check(
                    raw_proposal, f"reviewer_result.proposed_checks[{index}]"
                )
                proposal_ids.append(proposal["proposal_id"])
            if len(proposal_ids) != len(set(proposal_ids)):
                raise ContractError(
                    "reviewer_result proposed check IDs must be unique"
                )
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
            finding_ids: list[str] = []
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
                finding_ids.append(finding["finding_id"])
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
            if len(finding_ids) != len(set(finding_ids)):
                raise ContractError(
                    "reviewer_result candidate finding IDs must be unique"
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
                    "retry_pending",
                    "completed",
                    "failed",
                    "incomplete",
                    "subdivision_required",
                    "degraded",
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
        unit_entries = value.get("units")
        all_units_covered = True
        if unit_entries is not None:
            units = _require_array(unit_entries, "coverage_ledger.units")
            unit_ids: list[str] = []
            for index, raw_entry in enumerate(units):
                entry = _require_object(
                    raw_entry, f"coverage_ledger.units[{index}]"
                )
                _require_fields(
                    entry,
                    f"coverage_ledger.units[{index}]",
                    {"unit_id", "role_status", "attempts", "covered"},
                )
                unit_ids.append(
                    _require_string(
                        entry["unit_id"],
                        f"coverage_ledger.units[{index}].unit_id",
                    )
                )
                role_status = _require_object(
                    entry["role_status"],
                    f"coverage_ledger.units[{index}].role_status",
                )
                attempts = _require_object(
                    entry["attempts"],
                    f"coverage_ledger.units[{index}].attempts",
                )
                if set(role_status) != set(ROLES) or set(attempts) != set(ROLES):
                    raise ContractError(
                        f"coverage_ledger.units[{index}] must track both roles"
                    )
                for role in ROLES:
                    if role_status[role] not in {
                        "pending",
                        "retry_pending",
                        "completed",
                        "failed",
                        "incomplete",
                        "subdivision_required",
                        "degraded",
                        "not_selected",
                    }:
                        raise ContractError(
                            f"coverage_ledger.units[{index}].role_status.{role} is unsupported"
                        )
                    if (
                        not isinstance(attempts[role], int)
                        or isinstance(attempts[role], bool)
                        or attempts[role] < 0
                    ):
                        raise ContractError(
                            f"coverage_ledger.units[{index}].attempts.{role} must be a non-negative integer"
                        )
                if not isinstance(entry["covered"], bool):
                    raise ContractError(
                        f"coverage_ledger.units[{index}].covered must be a boolean"
                    )
                pair_completed = all(
                    role_status[role] == "completed" for role in ROLES
                )
                if entry["covered"] != pair_completed:
                    raise ContractError(
                        f"coverage_ledger.units[{index}] cannot claim coverage until both roles complete"
                    )
            if len(unit_ids) != len(set(unit_ids)):
                raise ContractError("coverage_ledger unit_id values must be unique")
            all_units_covered = bool(units) and all(
                entry["covered"] for entry in units
            )
        if not isinstance(value["complete"], bool):
            raise ContractError("coverage_ledger.complete must be a boolean")
        all_covered = all_units_covered and bool(value["records"]) and all(
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


def _lens_revision(active_lenses_by_unit: dict[str, list[dict[str, Any]]]) -> str:
    payload = json.dumps(
        active_lenses_by_unit,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _budget_revision(
    task_budgets_by_unit: dict[str, dict[str, dict[str, Any]]],
) -> str:
    payload = json.dumps(
        task_budgets_by_unit,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _expected_task_budgets(
    units: dict[str, dict[str, Any]],
    selected_unit_ids: set[str],
    active_lenses_by_unit: dict[str, list[dict[str, Any]]],
    preflight: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    context_budget = _require_object(
        preflight.get("context_budget"), "review_plan.preflight.context_budget"
    )
    conservative_estimate = context_budget.get("max_supplied_tokens_per_unit")
    if conservative_estimate is not None:
        conservative_estimate = _require_positive_integer(
            conservative_estimate,
            "review_plan.preflight.context_budget.max_supplied_tokens_per_unit",
        )
    resource_limits = _require_object(
        preflight.get("resource_limits"), "review_plan.preflight.resource_limits"
    )
    effective_limits = _require_object(
        resource_limits.get("effective"),
        "review_plan.preflight.resource_limits.effective",
    )
    budgets: dict[str, dict[str, dict[str, Any]]] = {}
    for unit_id in selected_unit_ids:
        unit = units[unit_id]
        raw_base_estimate = unit.get("estimated_context_tokens")
        base_estimate = _require_positive_integer(
            raw_base_estimate
            if raw_base_estimate is not None
            else conservative_estimate
            if conservative_estimate is not None
            else 1,
            f"Review Unit {unit_id}.estimated_context_tokens",
        )
        budgets[unit_id] = {
            role: {
                "estimated_context_tokens": base_estimate
                + sum(
                    lens["estimated_context_tokens"]
                    for lens in active_lenses_by_unit[unit_id]
                    if role in lens["supported_roles"]
                ),
                "resource_limits": effective_limits,
            }
            for role in ROLES
        }
    return budgets


def _validate_partial_review_provenance(
    preflight: dict[str, Any],
    graph_revision: str,
    selected_unit_ids: set[str],
    unselected_unit_ids: set[str],
) -> None:
    if preflight.get("status") != "ready_partial":
        return
    if not unselected_unit_ids:
        raise ContractError("a paired partial review must leave an executable unit unselected")
    decision = _require_object(
        preflight.get("budget_decision"),
        "review_plan.preflight.budget_decision",
    )
    _require_fields(
        decision,
        "review_plan.preflight.budget_decision",
        {
            "kind",
            "approved",
            "graph_revision",
            "selection_basis",
            "selected_unit_ids",
        },
    )
    decision_unit_ids = _require_string_array(
        decision["selected_unit_ids"],
        "review_plan.preflight.budget_decision.selected_unit_ids",
    )
    if (
        decision["kind"] != "paired_partial_review"
        or decision["approved"] is not True
        or decision["graph_revision"] != graph_revision
        or decision["selection_basis"] != "risk_prioritized"
        or set(decision_unit_ids) != selected_unit_ids
    ):
        raise ContractError(
            "paired partial review requires an approved graph-bound budget decision"
        )


def _build_bundle(
    scope: dict[str, Any],
    feature_graph: dict[str, Any],
    environment: dict[str, Any],
    budget_decision: dict[str, Any] | None = None,
    previous_plan: dict[str, Any] | None = None,
    degraded_fallback: dict[str, Any] | None = None,
    trusted_lenses: list[Any] | None = None,
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
    records_by_id = {
        record["record_id"]: record for record in scope["changed_records"]
    }
    provided_lens_count = len(trusted_lenses or [])
    lens_catalog: list[dict[str, Any]] = []
    for index, raw_lens in enumerate((trusted_lenses or []) + list(BUILTIN_LENSES)):
        lens_catalog.append(_validate_lens(raw_lens, f"trusted_lenses[{index}]"))
    lens_ids = [lens["lens_id"] for lens in lens_catalog]
    if len(lens_ids) != len(set(lens_ids)):
        raise ContractError("trusted lens IDs must be unique and cannot override built-in lenses")
    active_lenses_by_unit = {
        unit["unit_id"]: [
            lens
            for lens in lens_catalog
            if _lens_applies(lens, unit, records_by_id)
        ]
        for unit in graph_units
    }
    active_lens_ids = {
        lens["lens_id"]
        for lenses in active_lenses_by_unit.values()
        for lens in lenses
    }
    inapplicable_lens_ids = [
        lens_id
        for lens_id in lens_ids[:provided_lens_count]
        if lens_id not in active_lens_ids
    ]
    lens_revision = _lens_revision(active_lenses_by_unit)
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
    unit_role_context_estimates: dict[str, dict[str, int]] = {}
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
        base_estimate = _require_positive_integer(
            estimate, f"Review Unit {unit['unit_id']}.estimated_context_tokens"
        )
        role_estimates = {
            role: base_estimate
            + sum(
                lens["estimated_context_tokens"]
                for lens in active_lenses_by_unit[unit["unit_id"]]
                if role in lens["supported_roles"]
            )
            for role in ROLES
        }
        unit_role_context_estimates[unit["unit_id"]] = role_estimates
        unit_context_estimates[unit["unit_id"]] = max(role_estimates.values())

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
    task_budgets_by_unit = {
        unit["unit_id"]: {
            role: {
                "estimated_context_tokens": unit_role_context_estimates[
                    unit["unit_id"]
                ][role],
                "resource_limits": effective_limits,
            }
            for role in ROLES
        }
        for unit in units
    }
    budget_revision = _budget_revision(task_budgets_by_unit)
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
            role_mandate = _role_mandate(role)
            task_budget = task_budgets_by_unit[unit["unit_id"]][role]
            task_id = _stable_id(
                "task",
                scope_id,
                graph_revision,
                lens_revision,
                budget_revision,
                unit["unit_id"],
                role,
            )
            assignments.append(
                {
                    "task_id": task_id,
                    "unit_id": unit["unit_id"],
                    "role": role,
                    "role_mandate": role_mandate,
                    "budget": task_budget,
                    "attempt": 1,
                }
            )
            envelopes.append(
                {
                    "schema_version": RESILIENT_SCHEMA_VERSION,
                    "task_id": task_id,
                    "scope_id": scope_id,
                    "graph_revision": graph_revision,
                    "unit_id": unit["unit_id"],
                    "role": role,
                    "role_mandate": role_mandate,
                    "attempt": 1,
                    "changed_record_ids": list(unit["changed_record_ids"]),
                    "context_record_ids": context_record_ids,
                    "active_lenses": [
                        lens
                        for lens in active_lenses_by_unit[unit["unit_id"]]
                        if role in lens["supported_roles"]
                    ],
                    "core_policy": CORE_POLICY,
                    "output_contract": "reviewer_result@2",
                    "budget": task_budget,
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
    degraded_ready = False
    if degraded_fallback is not None:
        if degraded_fallback.get("kind") != "single_agent":
            raise ContractError("degraded_fallback.kind must be single_agent")
        if degraded_fallback.get("approved") is not True:
            raise ContractError("degraded single-agent fallback must be explicitly approved")
        if degraded_fallback.get("scope_id") != scope_id:
            raise ContractError(
                "degraded single-agent fallback must be bound to the frozen scope_id"
            )
        if degraded_fallback.get("graph_revision") != graph_revision:
            raise ContractError(
                "degraded single-agent fallback must be bound to the current graph revision"
            )
        if blocking_reasons != [
            "no_reviewer_capacity_after_main_agent_reservation"
        ]:
            raise ContractError(
                "degraded single-agent fallback is allowed only when isolated subagents are unavailable"
            )
        degraded_ready = True
        blocking_reasons = []
    dispatch_ready = not blocking_reasons and not degraded_ready
    planned_assignments = [] if degraded_ready else assignments
    review_plan = {
        "schema_version": RESILIENT_SCHEMA_VERSION,
        "scope_id": scope_id,
        "policy_version": CORE_POLICY["version"],
        "unit_ids": unit_ids,
        "assignments": planned_assignments,
        "preflight": {
            "status": (
                "ready_degraded"
                if degraded_ready
                else
                "ready_partial"
                if dispatch_ready and partial_review
                else "ready" if dispatch_ready else "paused"
            ),
            "executable_units": len(graph_units),
            "graph_executable_units": len(graph_units),
            "selected_executable_units": len(units),
            "required_reviewer_passes": 0 if degraded_ready else reviewer_passes,
            "paired_reviewer_passes": reviewer_passes,
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
            "lens_revision": lens_revision,
            "budget_revision": budget_revision,
            "task_budgets_by_unit": task_budgets_by_unit,
            "active_lens_ids_by_unit": {
                unit_id: [lens["lens_id"] for lens in lenses]
                for unit_id, lenses in active_lenses_by_unit.items()
            },
            "active_lenses_by_unit": active_lenses_by_unit,
            "inapplicable_lens_ids": inapplicable_lens_ids,
            "graph_changed": graph_changed,
            "graph_unit_ids": graph_unit_ids,
            "added_unit_ids": added_unit_ids,
        },
    }
    if degraded_ready:
        review_plan["preflight"].update(
            {
                "review_mode": "single_agent_v1_compatible",
                "reviewer_independence": "degraded",
                "coverage_claim": "degraded",
                "degraded_fallback": degraded_fallback,
            }
        )
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
    if "no_reviewer_capacity_after_main_agent_reservation" in blocking_reasons:
        review_plan["preflight"]["degraded_fallback_choice"] = "single_agent"
        review_plan["preflight"]["degraded_fallback_requires_explicit_approval"] = True
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
                        "degraded"
                        if degraded_ready
                        else "pending"
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
                    "degraded_single_agent_fallback"
                    if degraded_ready
                    else None
                    if feature_graph["ownership"][record["record_id"]]
                    in selected_unit_id_set
                    else "not_selected_by_partial_review"
                ),
            }
            for record in scope["changed_records"]
        ],
        "units": [
            {
                "unit_id": unit["unit_id"],
                "role_status": {
                    role: (
                    "degraded"
                    if degraded_ready
                    else "pending"
                        if unit["unit_id"] in selected_unit_id_set
                        else "not_selected"
                    )
                    for role in ROLES
                },
                "attempts": {role: 0 for role in ROLES},
                "covered": False,
                "selected_for_review": unit["unit_id"] in selected_unit_id_set,
                "uncovered_reason": (
                    "degraded_single_agent_fallback"
                    if degraded_ready
                    else None
                    if unit["unit_id"] in selected_unit_id_set
                    else "not_selected_by_partial_review"
                ),
            }
            for unit in graph_units
        ],
        "uncovered_record_ids": (
            [record["record_id"] for record in scope["changed_records"]]
            if degraded_ready
            else unselected_record_ids
        ),
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
    degraded_fallback = (
        _require_object(value["degraded_fallback"], "degraded_fallback")
        if "degraded_fallback" in value
        else None
    )
    trusted_lenses = (
        _require_array(value["trusted_lenses"], "trusted_lenses")
        if "trusted_lenses" in value
        else []
    )
    return _build_bundle(
        scope,
        feature_graph,
        environment,
        budget_decision,
        previous_plan,
        degraded_fallback,
        trusted_lenses,
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


def complete_graph(
    raw_value: object, *, allow_legacy: bool = False
) -> dict[str, Any]:
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
    plan_minor = _schema_minor(plan)
    if allow_legacy:
        if plan_minor != 0:
            raise ContractError(
                "complete-legacy-graph accepts only explicit legacy 2.0 Review Plans"
            )
    elif plan_minor < 1:
        raise ContractError(
            "legacy 2.0 Review Plans require the explicit complete-legacy-graph command"
        )
    if any(item["scope_id"] != scope["scope_id"] for item in (graph, plan, ledger)):
        raise ContractError("bundle contracts do not share one frozen scope_id")

    units = {
        unit["unit_id"]: unit
        for unit in graph["review_units"]
        if unit.get("executable") is True
    }
    scope_record_ids = {record["record_id"] for record in scope["changed_records"]}
    if set(graph["ownership"]) != scope_record_ids:
        raise ContractError(
            "Feature Graph ownership must cover every Scope Manifest changed record"
        )

    preflight = _require_object(plan["preflight"], "review_plan.preflight")
    if preflight.get("status") not in {"ready", "ready_partial"}:
        raise ContractError("cannot complete a Review Plan that was not ready for dispatch")
    graph_revision = _graph_revision(graph)
    if preflight.get("graph_revision") != graph_revision:
        raise ContractError("Review Plan graph revision does not match the Feature Graph")
    if plan["policy_version"] != CORE_POLICY["version"]:
        raise ContractError("Review Plan policy version does not match the core policy")

    expected_active_lenses: dict[str, list[dict[str, Any]]] = {}
    if "lens_revision" in preflight:
        raw_lenses_by_unit = _require_object(
            preflight.get("active_lenses_by_unit"),
            "review_plan.preflight.active_lenses_by_unit",
        )
        if set(raw_lenses_by_unit) != set(units):
            raise ContractError(
                "Review Plan active lenses must describe every executable unit"
            )
        for lens_unit_id, raw_lenses in raw_lenses_by_unit.items():
            lenses: list[dict[str, Any]] = []
            lens_ids: list[str] = []
            for index, raw_lens in enumerate(
                _require_array(
                    raw_lenses,
                    f"review_plan.preflight.active_lenses_by_unit.{lens_unit_id}",
                )
            ):
                lens = _validate_lens(
                    raw_lens,
                    f"review_plan.preflight.active_lenses_by_unit.{lens_unit_id}[{index}]",
                )
                lenses.append(lens)
                lens_ids.append(lens["lens_id"])
            if len(lens_ids) != len(set(lens_ids)):
                raise ContractError(
                    f"Review Plan active lens IDs must be unique for {lens_unit_id}"
                )
            expected_active_lenses[lens_unit_id] = lenses
        if _lens_revision(expected_active_lenses) != preflight["lens_revision"]:
            raise ContractError(
                "Review Plan active lens content does not match its lens revision"
            )
        expected_lens_ids = {
            unit_id: [lens["lens_id"] for lens in lenses]
            for unit_id, lenses in expected_active_lenses.items()
        }
        if preflight.get("active_lens_ids_by_unit") != expected_lens_ids:
            raise ContractError(
                "Review Plan active lens IDs do not match frozen lens content"
            )

    selected_unit_ids = set(plan["unit_ids"])
    if not selected_unit_ids or not selected_unit_ids <= set(units):
        raise ContractError("Review Plan units do not match the Feature Graph")
    unselected_unit_ids = set(units) - selected_unit_ids
    if preflight.get("status") == "ready" and unselected_unit_ids:
        raise ContractError("a complete Review Plan must select every executable unit")
    if set(preflight.get("unselected_unit_ids", [])) != unselected_unit_ids:
        raise ContractError("Review Plan unselected units do not match the Feature Graph")
    unselected_record_ids = {
        record_id
        for record_id, owner in graph["ownership"].items()
        if owner in unselected_unit_ids
    }
    if set(preflight.get("unselected_record_ids", [])) != unselected_record_ids:
        raise ContractError("Review Plan unselected records do not match ownership")
    _validate_partial_review_provenance(
        preflight,
        graph_revision,
        selected_unit_ids,
        unselected_unit_ids,
    )

    expected_task_budgets: dict[str, dict[str, dict[str, Any]]] = {}
    if "budget_revision" in preflight:
        if "lens_revision" not in preflight:
            raise ContractError("budget revision requires a lens-aware Review Plan")
        expected_task_budgets = _expected_task_budgets(
            units,
            selected_unit_ids,
            expected_active_lenses,
            preflight,
        )
        raw_task_budgets = _require_object(
            preflight.get("task_budgets_by_unit"),
            "review_plan.preflight.task_budgets_by_unit",
        )
        if (
            raw_task_budgets != expected_task_budgets
            or _budget_revision(expected_task_budgets)
            != preflight["budget_revision"]
        ):
            raise ContractError(
                "Review Plan task budgets do not match the canonical budget"
            )
    elif "lens_revision" in preflight:
        raise ContractError("current Review Plan is missing canonical budget metadata")

    expected_assignments: dict[tuple[str, str], dict[str, Any]] = {}
    for assignment in plan["assignments"]:
        key = (assignment["unit_id"], assignment["role"])
        task_identity_parts = [scope["scope_id"], graph_revision]
        if "lens_revision" in preflight:
            task_identity_parts.append(preflight["lens_revision"])
        if "budget_revision" in preflight:
            task_identity_parts.append(preflight["budget_revision"])
        if assignment["task_id"] != _stable_id(
            "task", *task_identity_parts, *key
        ):
            raise ContractError(
                f"Review Plan task_id does not match graph revision for {key[0]} {key[1]}"
            )
        if assignment["attempt"] != 1:
            raise ContractError("Review Plan completion requires the first planned attempt")
        if expected_task_budgets and (
            assignment["role_mandate"] != _role_mandate(assignment["role"])
            or assignment["budget"]
            != expected_task_budgets[assignment["unit_id"]][assignment["role"]]
        ):
            raise ContractError(
                f"Review Plan assignment does not match the canonical budget or mandate for {key[0]} {key[1]}"
            )
        expected_assignments[key] = assignment

    raw_envelopes = _require_array(
        bundle["reviewer_task_envelopes"], "bundle.reviewer_task_envelopes"
    )
    if len(raw_envelopes) != len(expected_assignments):
        raise ContractError("reviewer task envelopes must match every planned assignment")
    envelopes: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_envelope in raw_envelopes:
        envelope = validate_contract("reviewer_task_envelope", raw_envelope)
        if allow_legacy and _schema_minor(envelope) != 0:
            raise ContractError(
                "complete-legacy-graph requires legacy 2.0 Reviewer Task Envelopes"
            )
        if not allow_legacy and _schema_minor(envelope) < 1:
            raise ContractError(
                "current completion requires 2.1 Reviewer Task Envelopes"
            )
        key = (envelope["unit_id"], envelope["role"])
        if key in envelopes:
            raise ContractError(
                f"duplicate reviewer task envelope for {key[0]} {key[1]}"
            )
        assignment = expected_assignments.get(key)
        if assignment is None or envelope["task_id"] != assignment["task_id"]:
            raise ContractError(
                f"reviewer task envelope does not match plan for {key[0]} {key[1]}"
            )
        if expected_task_budgets and (
            envelope.get("role_mandate") != assignment["role_mandate"]
            or envelope.get("budget") != assignment["budget"]
        ):
            raise ContractError(
                f"reviewer task envelope role mandate or budget does not match the plan for {key[0]} {key[1]}"
            )
        if envelope["scope_id"] != scope["scope_id"]:
            raise ContractError(
                f"reviewer task envelope scope does not match for {key[0]} {key[1]}"
            )
        if envelope.get("graph_revision") != graph_revision:
            raise ContractError(
                f"reviewer task envelope graph revision does not match for {key[0]} {key[1]}"
            )
        record_ids = set(units[key[0]]["changed_record_ids"])
        if set(envelope["changed_record_ids"]) != record_ids:
            raise ContractError(
                f"reviewer task envelope evidence does not match owned scope for {key[0]} {key[1]}"
            )
        if set(envelope.get("context_record_ids", [])) != set(
            units[key[0]].get("context_record_ids", [])
        ):
            raise ContractError(
                f"reviewer task envelope context does not match the Feature Graph for {key[0]} {key[1]}"
            )
        if expected_active_lenses:
            role_lenses = [
                lens
                for lens in expected_active_lenses[key[0]]
                if key[1] in lens["supported_roles"]
            ]
            if envelope.get("active_lenses", []) != role_lenses:
                raise ContractError(
                    f"reviewer task envelope active lenses do not match the lens revision for {key[0]} {key[1]}"
                )
        envelopes[key] = envelope
    if set(envelopes) != set(expected_assignments):
        raise ContractError("reviewer task envelopes must cover every planned role")

    ledger_by_record = {
        entry["record_id"]: entry for entry in ledger["records"]
    }
    if set(ledger_by_record) != scope_record_ids:
        raise ContractError("Coverage Ledger records do not match the Scope Manifest")
    for record_id, entry in ledger_by_record.items():
        owner = graph["ownership"][record_id]
        if entry["primary_owner"] != owner:
            raise ContractError("Coverage Ledger primary owner does not match Feature Graph")
        selected = owner in selected_unit_ids
        expected_status = "pending" if selected else "not_selected"
        if set(entry["role_status"].values()) != {expected_status}:
            raise ContractError("Coverage Ledger role state does not match Review Plan selection")
        if set(entry["attempts"].values()) != {0} or entry["covered"]:
            raise ContractError("Coverage Ledger must be pending before result completion")
        if entry.get("selected_for_review", selected) is not selected:
            raise ContractError("Coverage Ledger record selection does not match Review Plan")
    if set(ledger.get("uncovered_record_ids", [])) != unselected_record_ids:
        raise ContractError("Coverage Ledger uncovered records do not match Review Plan")
    ledger_units = _require_array(ledger.get("units"), "coverage_ledger.units")
    ledger_by_unit = {entry["unit_id"]: entry for entry in ledger_units}
    if set(ledger_by_unit) != set(units) or len(ledger_by_unit) != len(ledger_units):
        raise ContractError("Coverage Ledger units do not match the Feature Graph")
    for unit_id, entry in ledger_by_unit.items():
        expected_status = "pending" if unit_id in selected_unit_ids else "not_selected"
        if set(entry["role_status"].values()) != {expected_status}:
            raise ContractError("Coverage Ledger unit state does not match Review Plan selection")
        if set(entry["attempts"].values()) != {0} or entry["covered"]:
            raise ContractError("Coverage Ledger units must be pending before result completion")
        selected = unit_id in selected_unit_ids
        if entry.get("selected_for_review", selected) is not selected:
            raise ContractError("Coverage Ledger unit selection does not match Review Plan")

    raw_results = value["reviewer_results"]
    if not isinstance(raw_results, list):
        raise ContractError("reviewer_results must be an array")
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_result in raw_results:
        result = validate_contract("reviewer_result", raw_result)
        key = (result["unit_id"], result["role"])
        if key in results:
            raise ContractError(f"duplicate reviewer result for {key[0]} {key[1]}")
        assignment = expected_assignments.get(key)
        if assignment is None:
            raise ContractError(f"unplanned reviewer result: {key[0]} {key[1]}")
        if result["task_id"] != assignment["task_id"]:
            raise ContractError(
                f"reviewer result task_id does not match plan for {key[0]} {key[1]}"
            )
        if result["scope_id"] != scope["scope_id"]:
            raise ContractError(
                f"reviewer result scope_id does not match for {key[0]} {key[1]}"
            )
        if result["status"] != "completed":
            raise ContractError(
                f"reviewer role {key[0]} {key[1]} did not complete successfully"
            )
        record_ids = set(units[key[0]]["changed_record_ids"])
        if set(result["inspected_record_ids"]) != record_ids:
            raise ContractError(
                f"reviewer role {key[0]} {key[1]} did not inspect the complete owned scope"
            )
        frozen_anchors = {
            json.dumps(record["anchor"], sort_keys=True, separators=(",", ":"))
            for record in scope["changed_records"]
            if record["record_id"] in record_ids
        }
        for candidate in result["candidate_findings"]:
            candidate_anchor = json.dumps(
                candidate["anchor"], sort_keys=True, separators=(",", ":")
            )
            if candidate_anchor not in frozen_anchors:
                raise ContractError(
                    f"reviewer role {key[0]} {key[1]} candidate anchor is not in the frozen Scope Manifest owned evidence"
                )
        results[key] = result
    if set(results) != set(expected_assignments):
        missing = sorted(set(expected_assignments) - set(results))
        missing_names = ", ".join(f"{unit_id}/{role}" for unit_id, role in missing)
        raise ContractError(f"missing successful reviewer roles: {missing_names}")

    completed_ledger = copy.deepcopy(ledger)
    for entry in completed_ledger["records"]:
        if entry["primary_owner"] not in selected_unit_ids:
            continue
        entry["role_status"] = {role: "completed" for role in ROLES}
        entry["attempts"] = {role: 1 for role in ROLES}
        entry["covered"] = True
    for entry in completed_ledger["units"]:
        if entry["unit_id"] not in selected_unit_ids:
            continue
        entry["role_status"] = {role: "completed" for role in ROLES}
        entry["attempts"] = {role: 1 for role in ROLES}
        entry["covered"] = True
    completed_ledger["complete"] = all(
        entry["covered"] for entry in completed_ledger["records"]
    ) and all(entry["covered"] for entry in completed_ledger["units"])
    validate_contract("coverage_ledger", completed_ledger)

    candidates = [
        {
            "task_id": result["task_id"],
            "unit_id": unit_id,
            "role": role,
            "candidate": candidate,
        }
        for (unit_id, role), result in results.items()
        for candidate in result["candidate_findings"]
    ]
    return {
        "coverage_ledger": completed_ledger,
        "candidate_findings": candidates,
        "review_mode": (
            "resilient_lenses_v2"
            if not allow_legacy
            else "legacy_v2_all_success"
        ),
        "main_agent_action": "verify, deduplicate, calibrate, and report through the evidence gate",
    }


def complete_one_unit(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "completion input")
    bundle = _require_object(value.get("bundle"), "bundle")
    graph = _require_object(bundle.get("feature_graph"), "feature_graph")
    units = graph.get("review_units")
    if not isinstance(units, list) or len(units) != 1:
        raise ContractError("complete-one-unit requires exactly one Review Unit")
    return complete_graph(raw_value)


def _retry_task_id(initial_task_id: str) -> str:
    return _stable_id("retry", initial_task_id, "attempt-2")


def _lifecycle_state(
    bundle: dict[str, Any], graph_revision: str
) -> dict[str, Any]:
    existing = bundle.get("review_lifecycle")
    if existing is not None:
        state = _require_object(existing, "bundle.review_lifecycle")
        _require_fields(
            state,
            "bundle.review_lifecycle",
            {
                "schema_version",
                "scope_id",
                "graph_revision",
                "roles",
                "accepted_results",
            },
        )
        _validate_version(state, "bundle.review_lifecycle")
        if state["scope_id"] != bundle["scope_manifest"]["scope_id"]:
            raise ContractError("review lifecycle does not share the frozen scope_id")
        if state["graph_revision"] != graph_revision:
            raise ContractError("review lifecycle graph revision is stale")
        _require_array(state["roles"], "bundle.review_lifecycle.roles")
        _require_array(
            state["accepted_results"], "bundle.review_lifecycle.accepted_results"
        )
        return state

    return {
        "schema_version": RESILIENT_SCHEMA_VERSION,
        "scope_id": bundle["scope_manifest"]["scope_id"],
        "graph_revision": graph_revision,
        "roles": [
            {
                "unit_id": assignment["unit_id"],
                "role": assignment["role"],
                "state": "pending",
                "attempts": 0,
                "last_failure": None,
            }
            for assignment in bundle["review_plan"]["assignments"]
        ],
        "accepted_results": [],
    }


def _lifecycle_bundle(raw_bundle: object) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    bundle = copy.deepcopy(_require_object(raw_bundle, "bundle"))
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
    preflight = _require_object(plan["preflight"], "review_plan.preflight")
    if preflight.get("status") not in {"ready", "ready_partial"}:
        raise ContractError("review lifecycle requires a dispatch-ready paired plan")
    graph_revision = _graph_revision(graph)
    if preflight.get("graph_revision") != graph_revision:
        raise ContractError("Review Plan graph revision does not match the Feature Graph")
    if plan["policy_version"] != CORE_POLICY["version"]:
        raise ContractError("Review Plan policy version does not match the core policy")
    units = {
        unit["unit_id"]: unit
        for unit in graph["review_units"]
        if unit.get("executable") is True
    }
    selected_unit_ids = set(plan["unit_ids"])
    if not selected_unit_ids or not selected_unit_ids <= set(units):
        raise ContractError("Review Plan units do not match the Feature Graph")
    unselected_unit_ids = set(units) - selected_unit_ids
    if preflight.get("status") == "ready" and unselected_unit_ids:
        raise ContractError("a complete Review Plan must select every executable unit")
    if set(preflight.get("unselected_unit_ids", [])) != unselected_unit_ids:
        raise ContractError("Review Plan unselected units do not match the Feature Graph")
    unselected_record_ids = {
        record_id
        for record_id, owner in graph["ownership"].items()
        if owner in unselected_unit_ids
    }
    if set(preflight.get("unselected_record_ids", [])) != unselected_record_ids:
        raise ContractError("Review Plan unselected records do not match ownership")

    _validate_partial_review_provenance(
        preflight,
        graph_revision,
        selected_unit_ids,
        unselected_unit_ids,
    )

    active_lenses_by_unit: dict[str, list[dict[str, Any]]] = {
        unit_id: [] for unit_id in units
    }
    if "lens_revision" in preflight:
        raw_lenses_by_unit = _require_object(
            preflight.get("active_lenses_by_unit"),
            "review_plan.preflight.active_lenses_by_unit",
        )
        if set(raw_lenses_by_unit) != set(units):
            raise ContractError(
                "Review Plan active lenses must describe every executable unit"
            )
        for lens_unit_id, raw_lenses in raw_lenses_by_unit.items():
            lenses = [
                _validate_lens(
                    raw_lens,
                    f"review_plan.preflight.active_lenses_by_unit.{lens_unit_id}[{index}]",
                )
                for index, raw_lens in enumerate(
                    _require_array(
                        raw_lenses,
                        f"review_plan.preflight.active_lenses_by_unit.{lens_unit_id}",
                    )
                )
            ]
            if len({lens["lens_id"] for lens in lenses}) != len(lenses):
                raise ContractError(
                    f"Review Plan active lens IDs must be unique for {lens_unit_id}"
                )
            active_lenses_by_unit[lens_unit_id] = lenses
        if _lens_revision(active_lenses_by_unit) != preflight["lens_revision"]:
            raise ContractError(
                "Review Plan active lens content does not match its lens revision"
            )

    if "lens_revision" not in preflight or "budget_revision" not in preflight:
        raise ContractError(
            "review lifecycle requires current lens and canonical budget metadata"
        )
    expected_task_budgets = _expected_task_budgets(
        units,
        selected_unit_ids,
        active_lenses_by_unit,
        preflight,
    )
    raw_task_budgets = _require_object(
        preflight.get("task_budgets_by_unit"),
        "review_plan.preflight.task_budgets_by_unit",
    )
    if (
        raw_task_budgets != expected_task_budgets
        or _budget_revision(expected_task_budgets) != preflight["budget_revision"]
    ):
        raise ContractError("Review Plan task budgets do not match the canonical budget")

    task_identity_parts = [
        scope["scope_id"],
        graph_revision,
        preflight["lens_revision"],
        preflight["budget_revision"],
    ]
    assignments: dict[tuple[str, str], dict[str, Any]] = {}
    for assignment in plan["assignments"]:
        key = (assignment["unit_id"], assignment["role"])
        if assignment["task_id"] != _stable_id(
            "task", *task_identity_parts, *key
        ):
            raise ContractError(
                f"Review Plan task_id does not match frozen graph and lenses for {key[0]} {key[1]}"
            )
        if (
            assignment["role_mandate"] != _role_mandate(assignment["role"])
            or assignment["budget"]
            != expected_task_budgets[assignment["unit_id"]][assignment["role"]]
        ):
            raise ContractError(
                f"Review Plan assignment does not match the canonical budget or mandate for {key[0]} {key[1]}"
            )
        assignments[key] = assignment
    envelopes: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_envelope in _require_array(
        bundle["reviewer_task_envelopes"], "bundle.reviewer_task_envelopes"
    ):
        envelope = validate_contract("reviewer_task_envelope", raw_envelope)
        key = (envelope["unit_id"], envelope["role"])
        assignment = assignments.get(key)
        if (
            key in envelopes
            or assignment is None
            or envelope["task_id"] != assignment["task_id"]
            or envelope["scope_id"] != scope["scope_id"]
            or envelope.get("graph_revision") != graph_revision
        ):
            raise ContractError(
                f"reviewer task envelope does not match frozen plan for {key[0]} {key[1]}"
            )
        if envelope["role_mandate"] != assignment["role_mandate"]:
            raise ContractError(
                f"reviewer task envelope role mandate does not match frozen plan for {key[0]} {key[1]}"
            )
        if envelope["budget"] != assignment["budget"]:
            raise ContractError(
                f"reviewer task envelope budget does not match frozen plan for {key[0]} {key[1]}"
            )
        unit = units[key[0]]
        expected_role_lenses = [
            lens
            for lens in active_lenses_by_unit[key[0]]
            if key[1] in lens["supported_roles"]
        ]
        if (
            set(envelope["changed_record_ids"])
            != set(unit["changed_record_ids"])
            or set(envelope.get("context_record_ids", []))
            != set(unit.get("context_record_ids", []))
            or envelope.get("active_lenses", []) != expected_role_lenses
        ):
            raise ContractError(
                f"reviewer task envelope evidence or lenses do not match frozen plan for {key[0]} {key[1]}"
            )
        envelopes[key] = envelope
    if set(envelopes) != set(assignments):
        raise ContractError("reviewer task envelopes must cover every planned role")

    lifecycle = _lifecycle_state(bundle, graph_revision)
    lifecycle_roles: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw_entry in enumerate(lifecycle["roles"]):
        entry = _require_object(raw_entry, f"review_lifecycle.roles[{index}]")
        _require_fields(
            entry,
            f"review_lifecycle.roles[{index}]",
            {"unit_id", "role", "state", "attempts", "last_failure"},
        )
        key = (entry["unit_id"], entry["role"])
        if key in lifecycle_roles or key not in assignments:
            raise ContractError("review lifecycle roles must match planned assignments")
        if entry["state"] not in {
            "pending",
            "retry_pending",
            "completed",
            "incomplete",
            "subdivision_required",
        }:
            raise ContractError("review lifecycle role state is unsupported")
        if (
            not isinstance(entry["attempts"], int)
            or isinstance(entry["attempts"], bool)
            or entry["attempts"] not in {0, 1, 2}
        ):
            raise ContractError("review lifecycle attempts must be 0, 1, or 2")
        valid_attempts_by_state = {
            "pending": {0},
            "retry_pending": {1},
            "completed": {1, 2},
            "incomplete": {2},
            "subdivision_required": {1, 2},
        }
        if entry["attempts"] not in valid_attempts_by_state[entry["state"]]:
            raise ContractError(
                "review lifecycle state and attempts are inconsistent"
            )
        if entry["state"] in {"pending", "completed"}:
            if entry["last_failure"] is not None:
                raise ContractError(
                    "pending or completed lifecycle roles cannot retain a failure"
                )
        else:
            _require_string(
                entry["last_failure"],
                f"review_lifecycle.roles[{index}].last_failure",
            )
            retryable_failures = {
                "timeout",
                "tool_failure",
                "malformed_result",
                "incomplete_role_coverage",
            }
            expected_failures = (
                {"context_overflow"}
                if entry["state"] == "subdivision_required"
                else retryable_failures
            )
            if entry["last_failure"] not in expected_failures:
                raise ContractError(
                    "review lifecycle state and failure reason are inconsistent"
                )
        lifecycle_roles[key] = entry
    if set(lifecycle_roles) != set(assignments):
        raise ContractError("review lifecycle must track every planned role")

    ledger_records = {entry["record_id"]: entry for entry in ledger["records"]}
    ledger_units = {entry["unit_id"]: entry for entry in ledger.get("units", [])}
    if set(ledger_records) != set(graph["ownership"]) or set(ledger_units) != set(units):
        raise ContractError("Coverage Ledger does not match the frozen Feature Graph")
    for record_id, record_entry in ledger_records.items():
        owner = graph["ownership"][record_id]
        selected = owner in selected_unit_ids
        if (
            record_entry["primary_owner"] != owner
            or record_entry.get("selected_for_review") is not selected
        ):
            raise ContractError(
                "Coverage Ledger record ownership or selection does not match the plan"
            )
        if not selected and (
            set(record_entry["role_status"].values()) != {"not_selected"}
            or set(record_entry["attempts"].values()) != {0}
            or record_entry["covered"]
            or record_entry.get("uncovered_reason")
            != "not_selected_by_partial_review"
        ):
            raise ContractError(
                "Coverage Ledger unselected record must remain not_selected and uncovered"
            )
    for ledger_unit_id, unit_entry in ledger_units.items():
        selected = ledger_unit_id in selected_unit_ids
        if unit_entry.get("selected_for_review") is not selected:
            raise ContractError(
                "Coverage Ledger unit selection does not match the plan"
            )
        if not selected and (
            set(unit_entry["role_status"].values()) != {"not_selected"}
            or set(unit_entry["attempts"].values()) != {0}
            or unit_entry["covered"]
            or unit_entry.get("uncovered_reason")
            != "not_selected_by_partial_review"
        ):
            raise ContractError(
                "Coverage Ledger unselected unit must remain not_selected and uncovered"
            )
    if not unselected_record_ids <= set(ledger.get("uncovered_record_ids", [])):
        raise ContractError(
            "Coverage Ledger must retain every not_selected record in uncovered_record_ids"
        )
    if unselected_unit_ids and ledger["complete"]:
        raise ContractError("a paired partial review can never claim complete coverage")
    for key, role_entry in lifecycle_roles.items():
        unit_id, role = key
        if ledger_units[unit_id]["role_status"][role] != role_entry["state"]:
            raise ContractError("Coverage Ledger unit role state does not match lifecycle")
        if ledger_units[unit_id]["attempts"][role] != role_entry["attempts"]:
            raise ContractError("Coverage Ledger unit attempts do not match lifecycle")
        for record_id in units[unit_id]["changed_record_ids"]:
            record_entry = ledger_records[record_id]
            if (
                record_entry["role_status"][role] != role_entry["state"]
                or record_entry["attempts"][role] != role_entry["attempts"]
            ):
                raise ContractError(
                    "Coverage Ledger record role state does not match lifecycle"
                )
    bundle["review_lifecycle"] = lifecycle
    return bundle, scope, graph, plan, ledger


def advance_review(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "review lifecycle input")
    _require_fields(value, "review lifecycle input", {"bundle", "event"})
    bundle, scope, graph, _plan, ledger = _lifecycle_bundle(value["bundle"])
    event = _require_object(value["event"], "event")
    _require_fields(event, "event", {"unit_id", "role", "task_id", "outcome"})
    unit_id = _require_string(event["unit_id"], "event.unit_id")
    role = _require_string(event["role"], "event.role")
    task_id = _require_string(event["task_id"], "event.task_id")
    if role not in ROLES:
        raise ContractError("event.role is unsupported")
    if event["outcome"] not in {
        "result",
        "timeout",
        "tool_failure",
        "context_overflow",
    }:
        raise ContractError("event.outcome is unsupported")

    lifecycle = bundle["review_lifecycle"]
    role_entries = [
        entry
        for entry in lifecycle["roles"]
        if entry.get("unit_id") == unit_id and entry.get("role") == role
    ]
    if len(role_entries) != 1:
        raise ContractError("event does not identify exactly one planned reviewer role")
    role_entry = role_entries[0]
    attempt = role_entry.get("attempts", 0) + 1
    if (
        role_entry.get("state") not in {"pending", "retry_pending"}
        or attempt not in {1, 2}
    ):
        raise ContractError("reviewer role is not awaiting an allowed attempt")
    envelopes = [
        validate_contract("reviewer_task_envelope", envelope)
        for envelope in bundle["reviewer_task_envelopes"]
        if envelope.get("unit_id") == unit_id and envelope.get("role") == role
    ]
    if len(envelopes) != 1:
        raise ContractError("event does not match one planned reviewer envelope")
    expected_task_id = (
        envelopes[0]["task_id"]
        if attempt == 1
        else _retry_task_id(envelopes[0]["task_id"])
    )
    if expected_task_id != task_id:
        raise ContractError("event task_id does not match the planned reviewer task")

    accepted_result: dict[str, Any] | None = None
    failure_reason = event["outcome"]
    if event["outcome"] == "result":
        try:
            if "reviewer_result" not in event:
                raise ContractError("result event requires reviewer_result")
            candidate_result = validate_contract(
                "reviewer_result", event["reviewer_result"]
            )
            if (
                candidate_result["task_id"] != task_id
                or candidate_result["scope_id"] != scope["scope_id"]
                or candidate_result["unit_id"] != unit_id
                or candidate_result["role"] != role
            ):
                raise ContractError(
                    "reviewer result identity does not match the frozen task"
                )
            owned_record_ids = set(
                next(
                    unit["changed_record_ids"]
                    for unit in graph["review_units"]
                    if unit["unit_id"] == unit_id
                )
            )
            if (
                candidate_result["status"] != "completed"
                or set(candidate_result["inspected_record_ids"])
                != owned_record_ids
            ):
                failure_reason = "incomplete_role_coverage"
            else:
                frozen_anchors = {
                    json.dumps(
                        record["anchor"], sort_keys=True, separators=(",", ":")
                    )
                    for record in scope["changed_records"]
                    if record["record_id"] in owned_record_ids
                }
                if any(
                    json.dumps(
                        candidate["anchor"],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    not in frozen_anchors
                    for candidate in candidate_result["candidate_findings"]
                ):
                    raise ContractError(
                        "reviewer candidate anchor is outside frozen owned evidence"
                    )
                accepted_result = candidate_result
        except (ContractError, KeyError, StopIteration, TypeError):
            failure_reason = "malformed_result"

    if accepted_result is not None:
        role_entry["state"] = "completed"
        role_entry["attempts"] = attempt
        role_entry["last_failure"] = None
        lifecycle["accepted_results"].append(accepted_result)
        for collection in (ledger["records"], ledger.get("units", [])):
            for entry in collection:
                owns_event = (
                    entry.get("primary_owner") == unit_id
                    if "record_id" in entry
                    else entry.get("unit_id") == unit_id
                )
                if not owns_event:
                    continue
                entry["role_status"][role] = "completed"
                entry["attempts"][role] = attempt
                entry["covered"] = all(
                    entry["role_status"][required_role] == "completed"
                    for required_role in ROLES
                )
                if entry["covered"]:
                    entry["uncovered_reason"] = None
        ledger["complete"] = bool(ledger["records"]) and all(
            entry["covered"] for entry in ledger["records"]
        ) and all(entry["covered"] for entry in ledger.get("units", []))
        ledger["uncovered_record_ids"] = [
            entry["record_id"] for entry in ledger["records"] if not entry["covered"]
        ]
        validate_contract("coverage_ledger", ledger)
        candidates = [
            {
                "task_id": task_id,
                "unit_id": unit_id,
                "role": role,
                "candidate": candidate,
                "verification_status": "pending_main_agent",
            }
            for candidate in accepted_result["candidate_findings"]
        ]
        return {
            "bundle": bundle,
            "retry_task_envelope": None,
            "candidate_findings": candidates,
            "proposed_checks": [
                {
                    "task_id": task_id,
                    "unit_id": unit_id,
                    "role": role,
                    "proposal": proposal,
                    "screening_status": "pending_main_agent",
                }
                for proposal in accepted_result["proposed_checks"]
            ],
            "verification_gaps": [],
        }

    if event["outcome"] == "context_overflow":
        role_entry["state"] = "subdivision_required"
        role_entry["attempts"] = attempt
        role_entry["last_failure"] = "context_overflow"
        affected_record_ids = [
            record_id
            for record_id, owner in graph["ownership"].items()
            if owner == unit_id
        ]
        for collection in (ledger["records"], ledger.get("units", [])):
            for entry in collection:
                owns_event = (
                    entry.get("primary_owner") == unit_id
                    if "record_id" in entry
                    else entry.get("unit_id") == unit_id
                )
                if owns_event:
                    entry["role_status"][role] = "subdivision_required"
                    entry["attempts"][role] = attempt
                    entry["covered"] = False
                    entry["uncovered_reason"] = (
                        "context_overflow_requires_subdivision"
                    )
        ledger["uncovered_record_ids"] = sorted(
            set(ledger.get("uncovered_record_ids", [])) | set(affected_record_ids)
        )
        ledger["complete"] = False
        validate_contract("coverage_ledger", ledger)
        return {
            "bundle": bundle,
            "status": "repreflight_required",
            "required_action": (
                "subdivide_by_behavior_state_transition_or_contract"
            ),
            "retry_task_envelope": None,
            "candidate_findings": [],
            "proposed_checks": [],
            "verification_gaps": [
                {
                    "kind": "context_overflow",
                    "unit_id": unit_id,
                    "role": role,
                    "affected_record_ids": affected_record_ids,
                }
            ],
        }

    terminal = attempt == 2
    role_entry["state"] = "incomplete" if terminal else "retry_pending"
    role_entry["attempts"] = attempt
    role_entry["last_failure"] = failure_reason
    for collection in (ledger["records"], ledger.get("units", [])):
        for entry in collection:
            owns_event = (
                entry.get("primary_owner") == unit_id
                if "record_id" in entry
                else entry.get("unit_id") == unit_id
            )
            if owns_event:
                entry["role_status"][role] = role_entry["state"]
                entry["attempts"][role] = attempt
                entry["covered"] = False
                if terminal:
                    entry["uncovered_reason"] = "reviewer_role_failed"
    ledger["complete"] = False
    affected_record_ids = [
        record_id
        for record_id, owner in graph["ownership"].items()
        if owner == unit_id
    ]
    if terminal:
        ledger["uncovered_record_ids"] = sorted(
            set(ledger.get("uncovered_record_ids", [])) | set(affected_record_ids)
        )
    validate_contract("coverage_ledger", ledger)

    retry = None
    if not terminal:
        retry = {
            field: copy.deepcopy(envelopes[0][field])
            for field in (
                "schema_version",
                "task_id",
                "scope_id",
                "graph_revision",
                "unit_id",
                "role",
                "role_mandate",
                "changed_record_ids",
                "context_record_ids",
                "active_lenses",
                "core_policy",
                "output_contract",
                "budget",
            )
            if field in envelopes[0]
        }
        retry["task_id"] = _retry_task_id(envelopes[0]["task_id"])
        retry["attempt"] = 2
        retry["fresh_context_required"] = True
        retry["excluded_context"] = [
            "paired_role_conclusions",
            "previous_attempt_conclusions",
        ]
        validate_contract("reviewer_task_envelope", retry)
    return {
        "bundle": bundle,
        "retry_task_envelope": retry,
        "candidate_findings": [],
        "proposed_checks": [],
        "verification_gaps": (
            [
                {
                    "kind": "reviewer_role_failed",
                    "unit_id": unit_id,
                    "role": role,
                    "attempts": attempt,
                    "affected_record_ids": affected_record_ids,
                }
            ]
            if terminal
            else []
        ),
    }


def _scope_identity_payload(scope: dict[str, Any]) -> str:
    return json.dumps(
        {
            "scope_id": scope["scope_id"],
            "target": scope["target"],
            "changed_records": scope["changed_records"],
            "exclusions": scope["exclusions"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def scope_gate(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "scope gate input")
    _require_fields(
        value,
        "scope gate input",
        {"frozen_scope_manifest", "current_scope_manifest", "phase"},
    )
    frozen = validate_contract(
        "scope_manifest", value["frozen_scope_manifest"]
    )
    current = validate_contract(
        "scope_manifest", value["current_scope_manifest"]
    )
    if value["phase"] not in {"before_dispatch", "before_final_report"}:
        raise ContractError("scope gate phase is unsupported")
    if _scope_identity_payload(frozen) != _scope_identity_payload(current):
        raise ContractError(
            f"scope drift detected {value['phase']}; invalidate affected results and re-review the current snapshot"
        )
    return {
        "status": "passed",
        "phase": value["phase"],
        "scope_id": frozen["scope_id"],
    }


def screen_validations(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "validation screening input")
    _require_fields(
        value, "validation screening input", {"proposals", "trusted_commands"}
    )
    raw_trusted = _require_array(value["trusted_commands"], "trusted_commands")
    trusted_argv: set[tuple[str, ...]] = set()
    for index, raw_command in enumerate(raw_trusted):
        command = _require_object(raw_command, f"trusted_commands[{index}]")
        _require_fields(
            command, f"trusted_commands[{index}]", {"argv", "trust_source"}
        )
        argv = _require_string_array(
            command["argv"], f"trusted_commands[{index}].argv", unique=False
        )
        if not argv:
            raise ContractError(f"trusted_commands[{index}].argv cannot be empty")
        if command["trust_source"] not in {
            "base_revision",
            "user_authorized",
            "host_policy",
        }:
            raise ContractError(
                f"trusted_commands[{index}].trust_source is not trusted"
            )
        trusted_argv.add(tuple(argv))

    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_argv: set[tuple[str, ...]] = set()
    for index, raw_proposal in enumerate(
        _require_array(value["proposals"], "proposals")
    ):
        wrapper = _require_object(raw_proposal, f"proposals[{index}]")
        proposal = (
            _validate_proposed_check(
                wrapper["proposal"], f"proposals[{index}].proposal"
            )
            if "proposal" in wrapper
            else _validate_proposed_check(wrapper, f"proposals[{index}]")
        )
        argv_key = tuple(proposal["argv"])
        reason: str | None = None
        if argv_key in seen_argv:
            reason = "duplicate"
        else:
            seen_argv.add(argv_key)
            if proposal["requires_network"]:
                reason = "network_not_authorized"
            elif proposal["requires_credentials"]:
                reason = "credentials_not_authorized"
            elif proposal["writes_repository"]:
                reason = "repository_write_not_authorized"
            elif proposal["scope"] != "focused":
                reason = "not_proportionate_or_focused"
            elif argv_key not in trusted_argv:
                reason = "command_not_from_trusted_source"
        if reason is None:
            approved.append(proposal)
        else:
            rejected.append(
                {"proposal_id": proposal["proposal_id"], "reason": reason}
            )
    return {
        "approved": approved,
        "rejected": rejected,
        "execution_authority": "main_agent_only",
        "commands_executed": 0,
    }


def finalize_review(raw_value: object) -> dict[str, Any]:
    value = _require_object(raw_value, "review finalization input")
    _require_fields(
        value,
        "review finalization input",
        {"bundle", "current_scope_manifest", "candidate_decisions"},
    )
    bundle, scope, graph, _plan, ledger = _lifecycle_bundle(value["bundle"])
    current_scope = validate_contract(
        "scope_manifest", value["current_scope_manifest"]
    )
    if _scope_identity_payload(current_scope) != _scope_identity_payload(scope):
        raise ContractError(
            "scope drift detected before final reporting; current evidence cannot be merged with frozen results"
        )
    lifecycle = bundle["review_lifecycle"]
    if any(
        entry.get("state") == "subdivision_required"
        for entry in lifecycle["roles"]
    ):
        raise ContractError(
            "context overflow requires behavioral subdivision and graph re-preflight before final reporting"
        )
    nonterminal = [
        f"{entry.get('unit_id')}/{entry.get('role')}"
        for entry in lifecycle["roles"]
        if entry.get("state") in {"pending", "retry_pending"}
    ]
    if nonterminal:
        raise ContractError(
            "cannot finalize while reviewer roles remain pending: "
            + ", ".join(sorted(nonterminal))
        )

    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved_questions: list[dict[str, Any]] = []
    completed_role_entries = {
        (entry.get("unit_id"), entry.get("role")): entry
        for entry in lifecycle["roles"]
        if entry.get("state") == "completed"
    }
    accepted_role_keys: set[tuple[str, str]] = set()
    original_envelopes = {
        (envelope.get("unit_id"), envelope.get("role")): envelope
        for envelope in bundle["reviewer_task_envelopes"]
    }
    units_by_id = {unit["unit_id"]: unit for unit in graph["review_units"]}
    for raw_result in lifecycle["accepted_results"]:
        result = validate_contract("reviewer_result", raw_result)
        role_key = (result["unit_id"], result["role"])
        role_entry = completed_role_entries.get(role_key)
        original_envelope = original_envelopes.get(role_key)
        if role_key in accepted_role_keys or role_entry is None or original_envelope is None:
            raise ContractError(
                "accepted reviewer result does not match exactly one completed role"
            )
        expected_task_id = (
            original_envelope["task_id"]
            if role_entry.get("attempts") == 1
            else _retry_task_id(original_envelope["task_id"])
            if role_entry.get("attempts") == 2
            else None
        )
        expected_records = set(units_by_id[result["unit_id"]]["changed_record_ids"])
        frozen_owned_anchors = {
            json.dumps(
                record["anchor"],
                sort_keys=True,
                separators=(",", ":"),
            )
            for record in scope["changed_records"]
            if record["record_id"] in expected_records
        }
        if (
            result["status"] != "completed"
            or result["scope_id"] != scope["scope_id"]
            or result["task_id"] != expected_task_id
            or set(result["inspected_record_ids"]) != expected_records
        ):
            raise ContractError(
                "accepted reviewer result identity or owned coverage is inconsistent with the lifecycle"
            )
        if any(
            json.dumps(
                candidate["anchor"],
                sort_keys=True,
                separators=(",", ":"),
            )
            not in frozen_owned_anchors
            for candidate in result["candidate_findings"]
        ):
            raise ContractError(
                "accepted reviewer result candidate anchor is outside frozen owned evidence"
            )
        accepted_role_keys.add(role_key)
        for assumption in result["unresolved_assumptions"]:
            unresolved_questions.append(
                {
                    "kind": "unresolved_reviewer_assumption",
                    "task_id": result["task_id"],
                    "unit_id": result["unit_id"],
                    "role": result["role"],
                    "assumption": copy.deepcopy(assumption),
                }
            )
        for candidate in result["candidate_findings"]:
            key = (result["task_id"], candidate["finding_id"])
            if key in candidates:
                raise ContractError(
                    "candidate finding IDs must be unique within a reviewer task"
                )
            candidates[key] = candidate
    if accepted_role_keys != set(completed_role_entries):
        raise ContractError(
            "accepted reviewer results must match every completed lifecycle role"
        )

    raw_decisions = _require_array(
        value["candidate_decisions"], "candidate_decisions"
    )
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw_decision in enumerate(raw_decisions):
        decision = _require_object(raw_decision, f"candidate_decisions[{index}]")
        _require_fields(
            decision,
            f"candidate_decisions[{index}]",
            {
                "task_id",
                "finding_id",
                "decision",
                "reason",
                "verification_evidence",
                "factual_conflict",
            },
        )
        key = (
            _require_string(
                decision["task_id"], f"candidate_decisions[{index}].task_id"
            ),
            _require_string(
                decision["finding_id"],
                f"candidate_decisions[{index}].finding_id",
            ),
        )
        if key in decisions:
            raise ContractError("candidate decisions must be unique")
        if key not in candidates:
            raise ContractError("candidate decision does not name an accepted candidate")
        if decision["decision"] not in {
            "verified_finding",
            "rejected",
            "question",
            "verification_gap",
        }:
            raise ContractError(
                f"candidate_decisions[{index}].decision is unsupported"
            )
        _require_string(
            decision["reason"], f"candidate_decisions[{index}].reason"
        )
        evidence = _require_string_array(
            decision["verification_evidence"],
            f"candidate_decisions[{index}].verification_evidence",
            unique=False,
        )
        if not isinstance(decision["factual_conflict"], bool):
            raise ContractError(
                f"candidate_decisions[{index}].factual_conflict must be a boolean"
            )
        if decision["factual_conflict"] and decision["decision"] not in {
            "question",
            "verification_gap",
        }:
            raise ContractError(
                "an unresolved factual conflict must become a question or verification gap"
            )
        if decision["decision"] == "verified_finding" and not evidence:
            raise ContractError(
                "a verified finding requires independent main-agent verification evidence"
            )
        decisions[key] = decision
    if set(decisions) != set(candidates):
        raise ContractError("every accepted candidate requires one main-agent decision")

    verified_findings: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = unresolved_questions
    verification_gaps: list[dict[str, Any]] = []
    for key, candidate in candidates.items():
        decision = decisions[key]
        decided = {
            **candidate,
            "main_agent_reason": decision["reason"],
            "verification_evidence": decision["verification_evidence"],
        }
        if decision["decision"] == "verified_finding":
            verified_findings.append(decided)
        elif decision["decision"] == "question":
            questions.append(decided)
        elif decision["decision"] == "verification_gap":
            verification_gaps.append(decided)

    for role_entry in lifecycle["roles"]:
        if role_entry.get("state") in {"incomplete", "subdivision_required"}:
            verification_gaps.append(
                {
                    "kind": "reviewer_role_incomplete",
                    "unit_id": role_entry["unit_id"],
                    "role": role_entry["role"],
                    "attempts": role_entry["attempts"],
                    "reason": role_entry["last_failure"],
                }
            )
    return {
        "verified_findings": verified_findings,
        "questions": questions,
        "verification_gaps": verification_gaps,
        "coverage_ledger": ledger,
        "reviewer_pair_status": "complete" if ledger["complete"] else "incomplete",
        "scope_gate": "passed_before_final_report",
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
        "complete-graph",
        help="accept current paired results for every selected Review Unit",
    )
    subparsers.add_parser(
        "complete-legacy-graph",
        help="explicitly accept all-success results for a legacy 2.0 Review Plan",
    )
    subparsers.add_parser(
        "complete-one-unit",
        help="accept exactly one successful isolated result for each planned role",
    )
    subparsers.add_parser(
        "advance-review",
        help="advance one reviewer role through its bounded failure lifecycle",
    )
    subparsers.add_parser(
        "finalize-review",
        help="adjudicate accepted candidates after the final scope-drift gate",
    )
    subparsers.add_parser(
        "scope-gate",
        help="reject scope drift before reviewer dispatch or final reporting",
    )
    subparsers.add_parser(
        "screen-validations",
        help="screen and deduplicate reviewer-proposed checks without executing them",
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
        elif args.command == "complete-graph":
            output = complete_graph(value)
        elif args.command == "complete-legacy-graph":
            output = complete_graph(value, allow_legacy=True)
        elif args.command == "advance-review":
            output = advance_review(value)
        elif args.command == "finalize-review":
            output = finalize_review(value)
        elif args.command == "scope-gate":
            output = scope_gate(value)
        elif args.command == "screen-validations":
            output = screen_validations(value)
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
