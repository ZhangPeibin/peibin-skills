#!/usr/bin/env python3
"""Build and validate ephemeral Code Review v2 workflow contracts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
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
        if not isinstance(value["review_units"], list):
            raise ContractError("feature_graph.review_units must be an array")
        unit_records: dict[str, set[str]] = {}
        claimed_records: set[str] = set()
        for index, raw_unit in enumerate(value["review_units"]):
            unit = _require_object(raw_unit, f"feature_graph.review_units[{index}]")
            _require_fields(
                unit,
                f"feature_graph.review_units[{index}]",
                {"unit_id", "changed_record_ids", "executable"},
            )
            if unit["unit_id"] in unit_records:
                raise ContractError("feature_graph unit_id values must be unique")
            changed_record_ids = _require_string_array(
                unit["changed_record_ids"],
                f"feature_graph.review_units[{index}].changed_record_ids",
            )
            unit_id = _require_string(
                unit["unit_id"], f"feature_graph.review_units[{index}].unit_id"
            )
            records = set(changed_record_ids)
            duplicate_claims = claimed_records & records
            if duplicate_claims:
                raise ContractError(
                    "feature_graph changed records cannot be claimed by multiple units"
                )
            claimed_records.update(records)
            unit_records[unit_id] = records
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
                if role_status[role] not in {"pending", "completed", "failed", "incomplete"}:
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
                "changed_record_ids": record_ids,
                "context_record_ids": [],
                "executable": True,
            }
        ],
        "ownership": {record_id: unit_id for record_id in record_ids},
        "edges": [],
    }

    assignments = []
    envelopes = []
    for role in ROLES:
        task_id = _stable_id("task", scope_id, unit_id, role)
        assignments.append(
            {"task_id": task_id, "unit_id": unit_id, "role": role, "attempt": 1}
        )
        envelopes.append(
            {
                "schema_version": SCHEMA_VERSION,
                "task_id": task_id,
                "scope_id": scope_id,
                "unit_id": unit_id,
                "role": role,
                "role_mandate": (
                    "Attack business logic, state transitions, boundaries, and error paths."
                    if role == "internal_behavior"
                    else "Attack callers, authorization, data, concurrency, compatibility, and deployment contracts."
                ),
                "changed_record_ids": record_ids,
                "context_record_ids": [],
                "core_policy": CORE_POLICY,
                "output_contract": "reviewer_result@2",
                "budget": {},
            }
        )

    review_plan = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "policy_version": CORE_POLICY["version"],
        "unit_ids": [unit_id],
        "assignments": assignments,
        "preflight": {
            "status": "ready",
            "executable_units": 1,
            "required_reviewer_passes": 2,
        },
    }
    coverage_ledger = {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "records": [
            {
                "record_id": record_id,
                "primary_owner": unit_id,
                "role_status": {role: "pending" for role in ROLES},
                "attempts": {role: 0 for role in ROLES},
                "covered": False,
            }
            for record_id in record_ids
        ],
        "complete": False,
    }

    validate_contract("feature_graph", feature_graph)
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
