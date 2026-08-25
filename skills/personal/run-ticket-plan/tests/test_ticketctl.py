from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "ticketctl.py"
ENVELOPE_KEYS = {
    "schema_version",
    "command",
    "outcome",
    "ticket",
    "changed",
    "data",
    "next",
    "error",
}
DATA_KEYS_BY_COMMAND = {
    "status": {
        "active",
        "frontier",
        "resumable",
        "roadmap",
        "by_state",
        "blocked_by",
        "waits_for",
        "handoffs",
        "completion_methods",
    },
    "next": {"roadmap", "work_packet"},
    "pause": {"reason", "waits_for", "handoff", "roadmap", "frontier"},
    "pass": {"completion", "roadmap", "frontier", "resumable"},
    "prepare-confirm": {
        "repository_root",
        "ticket_path",
        "title",
        "objective",
        "acceptance_criteria",
    },
    "confirm": {"summary", "criteria", "roadmap", "frontier", "resumable"},
}
SPEC = importlib.util.spec_from_file_location("ticketctl", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ticketctl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ticketctl
SPEC.loader.exec_module(ticketctl)


class TicketFixture:
    def __init__(self, root: Path):
        self.root = root
        self.feature = root / ".scratch" / "demo"
        self.issues = self.feature / "issues"
        self.issues.mkdir(parents=True)

    def add_ticket(
        self,
        ticket_id: str,
        *,
        status: str = "ready-for-agent",
        blocked_by: str = "None — can start immediately",
        what: str | None = None,
        criteria: tuple[str, ...] = ("The focused check passes.",),
        suffix: str = "ticket",
    ) -> Path:
        what = what or f"Deliver ticket {ticket_id}."
        checklist = "\n".join(f"- [ ] {criterion}" for criterion in criteria)
        path = self.issues / f"{ticket_id}-{suffix}.md"
        path.write_text(
            f"""# {ticket_id} — Ticket {ticket_id}

**What to build:** {what}

**Blocked by:** {blocked_by}

**Status:** {status}

{checklist}
""",
            encoding="utf-8",
        )
        return path

    def add_formal_ticket(self, ticket_id: str) -> Path:
        path = self.issues / f"{ticket_id}-formal.md"
        path.write_text(
            f"""# {ticket_id} Formal ticket

**Status:** Open
**Depends On:** None

## Delivery

Deliver the formal variant.

## Core Acceptance

- The formal check passes.
""",
            encoding="utf-8",
        )
        return path


class TicketCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = TicketFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self, command: str, *arguments: str, expected: int = 0
    ) -> tuple[dict[str, object], str]:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                command,
                "--feature-root",
                str(self.fixture.feature),
                *arguments,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            completed.stderr + completed.stdout,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload), ENVELOPE_KEYS)
        if payload["outcome"] == "ERROR":
            self.assertEqual(payload["data"], {})
        else:
            self.assertEqual(set(payload["data"]), DATA_KEYS_BY_COMMAND[command])
        return payload, completed.stdout

    def result_file(
        self,
        ticket_id: str,
        criteria: tuple[str, ...],
        *,
        failing: set[str] | None = None,
    ) -> Path:
        failing = failing or set()
        path = self.root / f"confirm-{ticket_id}.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "ticket": ticket_id,
                    "summary": "Independent verification completed.",
                    "criteria": [
                        {
                            "criterion": criterion,
                            "result": "FAIL" if criterion in failing else "PASS",
                            "evidence": f"Evidence for {criterion}",
                        }
                        for criterion in criteria
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def submit_result(
        self,
        ticket_id: str,
        criteria: tuple[str, ...],
        *,
        failing: set[str] | None = None,
    ) -> dict[str, object]:
        path = self.result_file(ticket_id, criteria, failing=failing)
        return self.run_cli(
            "confirm", "--ticket", ticket_id, "--result-file", str(path)
        )[0]

    def handoff_file(self, ticket_id: str) -> Path:
        path = self.fixture.feature / "handoffs" / f"{ticket_id}.md"
        path.parent.mkdir()
        path.write_text(
            f"""---
handoff_schema: "2.0"
---

# Handoff

Complete ticket {ticket_id}.
""",
            encoding="utf-8",
        )
        return path

    def test_status_reads_to_tickets_dag_without_writing(self) -> None:
        first = self.fixture.add_ticket("01")
        second = self.fixture.add_ticket("02", blocked_by="01 — Build the foundation")
        before = {first: first.read_bytes(), second: second.read_bytes()}

        payload, stdout = self.run_cli("status")

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "status")
        self.assertEqual(payload["outcome"], "READY")
        self.assertIsNone(payload["ticket"])
        self.assertFalse(payload["changed"])
        self.assertIsNone(payload["error"])
        self.assertEqual(
            payload["next"],
            {
                "command": "run-ticket-plan next 01",
                "reason": "oldest executable ticket",
            },
        )
        self.assertEqual(payload["data"]["frontier"], ["01"])
        self.assertEqual(payload["data"]["by_state"]["blocked"], ["02"])
        self.assertEqual(payload["data"]["blocked_by"]["02"], ["01"])
        self.assertEqual(
            payload["data"]["roadmap"],
            {"state": "MISSING", "changed": False, "created": False},
        )
        self.assertFalse(
            self.fixture.feature.joinpath("implementation-roadmap.md").exists()
        )
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(stdout.count("\n"), 1, "controller output should stay compact")

    def test_status_distinguishes_ready_active_and_complete_feature_states(
        self,
    ) -> None:
        self.fixture.add_ticket("01")

        ready, _ = self.run_cli("status")
        self.assertEqual(ready["outcome"], "READY")

        self.run_cli("next")
        active, _ = self.run_cli("status")
        self.assertEqual(active["outcome"], "ACTIVE")

        self.run_cli("pass", "--note", "Explicitly accepted.")
        complete, _ = self.run_cli("status")
        self.assertEqual(complete["outcome"], "COMPLETE")
        self.assertIsNone(complete["next"])

        exhausted, _ = self.run_cli("next", expected=2)
        self.assertEqual(exhausted["error"]["code"], "FEATURE_COMPLETE")

    def test_next_activates_frontier_and_creates_derived_roadmap(self) -> None:
        ticket_path = self.fixture.add_ticket(
            "01",
            what="Deliver a user-visible vertical slice.",
            criteria=("The focused check passes.", "The result is observable."),
        )

        activated, _ = self.run_cli("next")

        self.assertEqual(activated["schema_version"], 1)
        self.assertEqual(activated["command"], "next")
        self.assertEqual(activated["outcome"], "WORK_PACKET")
        self.assertEqual(activated["ticket"], "01")
        self.assertTrue(activated["changed"])
        self.assertIsNone(activated["error"])
        self.assertEqual(
            activated["next"],
            {
                "command": "run-ticket-plan confirm 01",
                "reason": "confirm after implementation",
            },
        )
        packet = activated["data"]["work_packet"]
        self.assertEqual(
            set(packet),
            {
                "source",
                "ticket",
                "objective",
                "acceptance_criteria",
                "boundaries",
                "history",
                "confirm",
            },
        )
        self.assertEqual(
            packet["acceptance_criteria"],
            ["The focused check passes.", "The result is observable."],
        )
        self.assertEqual(
            packet["source"],
            {
                "feature_root": str(self.fixture.feature.resolve()),
                "repository_root": str(self.root.resolve()),
                "ticket_path": str(ticket_path.resolve()),
            },
        )
        self.assertEqual(
            packet["ticket"],
            {
                "id": "01",
                "title": "Ticket 01",
                "state": "in-progress",
                "blocked_by": [],
            },
        )
        self.assertEqual(
            packet["objective"],
            "Deliver a user-visible vertical slice.",
        )
        self.assertEqual(
            packet["boundaries"],
            {
                "implementation": "Implement only this ticket's objective and acceptance criteria.",
                "control_plane": "Do not edit ticket status, managed state, or the roadmap directly.",
            },
        )
        self.assertEqual(
            packet["history"],
            {"last_pause": None, "last_confirmation": None, "handoff": None},
        )
        self.assertEqual(packet["confirm"], "run-ticket-plan confirm 01")
        self.assertEqual(
            activated["data"]["roadmap"],
            {"state": "CURRENT", "changed": True, "created": True},
        )
        self.assertIn(
            "**Status:** in-progress", ticket_path.read_text(encoding="utf-8")
        )
        roadmap = self.fixture.feature / "implementation-roadmap.md"
        self.assertTrue(roadmap.is_file())
        roadmap_text = roadmap.read_text(encoding="utf-8")
        self.assertIn("- Active: [`01` Ticket 01](issues/01-ticket.md)", roadmap_text)
        self.assertIn("- Progress: 0 / 1 Done", roadmap_text)
        self.assertIn("- W0: `01`", roadmap_text)
        self.assertIn("- Roots: `01`", roadmap_text)

        repeated, _ = self.run_cli("next")
        self.assertEqual(repeated["ticket"], "01")
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["data"]["work_packet"], packet)

    def test_roadmap_compacts_execution_waves_and_shared_dependencies(self) -> None:
        self.fixture.add_ticket("01", suffix="contract")
        self.fixture.add_ticket("02", suffix="runtime")
        self.fixture.add_ticket("03", blocked_by="02 - Runtime", suffix="tasks")
        self.fixture.add_ticket("04", blocked_by="02 - Runtime", suffix="security")
        self.fixture.add_ticket("05", blocked_by="`03`, `04`", suffix="read-models")

        self.run_cli("next", "--ticket", "01")
        roadmap = self.fixture.feature.joinpath("implementation-roadmap.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("- W0: `01`, `02`", roadmap)
        self.assertIn("- W1: `03`, `04`", roadmap)
        self.assertIn("- W2: `05`", roadmap)
        self.assertIn("| Ticket | State |", roadmap)
        self.assertNotIn("| Blocked by |", roadmap)
        self.assertNotIn("| Waits for |", roadmap)
        self.assertIn("[`03` Ticket 03](issues/03-tasks.md)", roadmap)
        self.assertIn("- Roots: `01`, `02`", roadmap)
        self.assertIn("- `03`, `04` <- `02`", roadmap)
        self.assertIn("- `05` <- `03`, `04`", roadmap)

    def test_roadmap_conflict_does_not_block_authoritative_ticket_state(self) -> None:
        ticket = self.fixture.add_ticket("01")
        roadmap = self.fixture.feature / "implementation-roadmap.md"
        roadmap.write_text("# Personal Notes\n", encoding="utf-8")

        activated, _ = self.run_cli("next")

        self.assertEqual(activated["outcome"], "WORK_PACKET")
        self.assertEqual(activated["data"]["roadmap"]["state"], "CONFLICT")
        self.assertEqual(roadmap.read_text(encoding="utf-8"), "# Personal Notes\n")
        self.assertIn("**Status:** in-progress", ticket.read_text(encoding="utf-8"))

    def test_formal_ticket_is_accepted_without_migration(self) -> None:
        self.fixture.add_formal_ticket("01")

        payload, _ = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["01"])
        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["01"]
        self.assertEqual(ticket.what_to_build, "Deliver the formal variant.")
        self.assertEqual(ticket.acceptance, ["The formal check passes."])

    def test_managed_state_is_not_parsed_as_paragraph_acceptance(self) -> None:
        path = self.fixture.issues / "01-section-variant.md"
        path.write_text(
            """# 01 Section variant

**Status:** ready-for-agent

## What to build

Deliver the section variant.

## Acceptance criteria

The paragraph criterion passes.
""",
            encoding="utf-8",
        )
        self.run_cli("next")
        self.run_cli("pause", "--reason", "Paused for another task.")

        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["01"]

        self.assertEqual(ticket.acceptance, ["The paragraph criterion passes."])

    def test_missing_dependency_and_cycle_are_rejected(self) -> None:
        self.fixture.add_ticket("01", blocked_by="02 - Missing")

        missing, _ = self.run_cli("status", expected=2)

        self.assertEqual(missing["outcome"], "ERROR")
        self.assertIsNone(missing["ticket"])
        self.assertFalse(missing["changed"])
        self.assertEqual(missing["data"], {})
        self.assertIsNone(missing["next"])
        self.assertEqual(missing["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("missing dependency", missing["error"]["details"]["errors"][0])

        self.fixture.add_ticket("02", blocked_by="01 - First")
        cycle, _ = self.run_cli("status", expected=2)
        self.assertEqual(cycle["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(
            any(
                "dependency cycle" in error
                for error in cycle["error"]["details"]["errors"]
            )
        )

    def test_ticket_requires_acceptance_criteria(self) -> None:
        self.fixture.add_ticket("01", criteria=())

        payload, _ = self.run_cli("status", expected=2)

        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(
            any(
                "no acceptance criteria" in error
                for error in payload["error"]["details"]["errors"]
            )
        )

    def test_formal_none_is_not_an_acceptance_criterion(self) -> None:
        path = self.fixture.add_formal_ticket("01")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "- The formal check passes.", "None"
            ),
            encoding="utf-8",
        )

        payload, _ = self.run_cli("status", expected=2)

        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(
            any(
                "no acceptance criteria" in error
                for error in payload["error"]["details"]["errors"]
            )
        )

    def test_failed_confirm_can_pause_for_remediation_and_resume(self) -> None:
        criteria = ("The focused check passes.",)
        parent = self.fixture.add_ticket("01", criteria=criteria)
        self.run_cli("next", "--ticket", "01")
        failed = self.submit_result("01", criteria, failing=set(criteria))

        self.assertEqual(failed["outcome"], "FAIL")
        self.assertTrue(failed["changed"])

        remediation = self.fixture.add_ticket(
            "05",
            what="Repair the failing focused check for ticket 01.",
            criteria=criteria,
            suffix="remediation",
        )
        paused, _ = self.run_cli(
            "pause",
            "--ticket",
            "01",
            "--until",
            "05",
            "--reason",
            "The focused check failed.",
        )
        self.assertEqual(paused["data"]["waits_for"], ["05"])
        self.assertEqual(paused["data"]["frontier"], ["05"])
        paused_roadmap = self.fixture.feature.joinpath(
            "implementation-roadmap.md"
        ).read_text(encoding="utf-8")
        self.assertIn("- W0: `05`", paused_roadmap)
        self.assertIn("- W1: `01`", paused_roadmap)
        self.assertIn("- `01` <- `05` (remediation)", paused_roadmap)

        selected, _ = self.run_cli("next")
        self.assertEqual(selected["ticket"], "05")
        self.run_cli("pass", "--ticket", "05", "--note", "Repair verified manually.")
        status, _ = self.run_cli("status")
        self.assertEqual(status["data"]["resumable"], ["01"])

        resumed, _ = self.run_cli("next")
        self.assertEqual(resumed["ticket"], "01")
        self.assertEqual(
            resumed["data"]["work_packet"]["history"]["last_pause"],
            {"reason": "The focused check failed.", "until": ["05"]},
        )
        self.assertIn("**Status:** in-progress", parent.read_text(encoding="utf-8"))
        self.assertIn("**Status:** Done", remediation.read_text(encoding="utf-8"))

    def test_pause_binds_a_durable_handoff_to_the_resumed_work_packet(self) -> None:
        self.fixture.add_ticket("01")
        self.run_cli("next", "--ticket", "01")
        handoff = self.handoff_file("01")

        paused, _ = self.run_cli(
            "pause",
            "--ticket",
            "01",
            "--reason",
            "Context is ending.",
            "--handoff-file",
            str(handoff),
        )

        self.assertEqual(paused["data"]["handoff"], str(handoff.resolve()))
        status, _ = self.run_cli("status")
        self.assertEqual(status["data"]["handoffs"], {"01": str(handoff.resolve())})
        resumed, _ = self.run_cli("next")
        self.assertEqual(
            resumed["data"]["work_packet"]["history"]["handoff"],
            str(handoff.resolve()),
        )

    def test_pause_rejects_a_handoff_outside_the_feature_handoffs_directory(
        self,
    ) -> None:
        self.fixture.add_ticket("01")
        self.run_cli("next")
        misplaced = self.handoff_file("01").rename(
            self.fixture.feature / "misplaced-handoff.md"
        )

        rejected, _ = self.run_cli(
            "pause",
            "--reason",
            "Context is ending.",
            "--handoff-file",
            str(misplaced),
            expected=2,
        )

        self.assertEqual(rejected["error"]["code"], "HANDOFF_INVALID")

    def test_pause_retries_stay_idempotent(self) -> None:
        ticket = self.fixture.add_ticket("01")
        self.fixture.add_ticket("05")
        self.run_cli("next", "--ticket", "01")
        arguments = (
            "--ticket",
            "01",
            "--reason",
            "Focused verification failed.",
            "--until",
            "05",
        )

        first, _ = self.run_cli("pause", *arguments)
        repeated, _ = self.run_cli("pause", *arguments)

        self.assertEqual(first["outcome"], "PAUSED")
        self.assertEqual(first["ticket"], "01")
        self.assertTrue(first["changed"])
        self.assertEqual(
            first["data"],
            {
                "reason": "Focused verification failed.",
                "waits_for": ["05"],
                "handoff": None,
                "roadmap": {
                    "state": "CURRENT",
                    "changed": True,
                    "created": False,
                },
                "frontier": ["05"],
            },
        )
        self.assertEqual(
            first["next"],
            {
                "command": "run-ticket-plan next 05",
                "reason": "oldest executable ticket",
            },
        )
        self.assertFalse(repeated["changed"])
        self.assertEqual(
            ticket.read_text(encoding="utf-8").count(ticketctl.STATE_START), 1
        )
        self.run_cli("next", "--ticket", "05")
        delayed, _ = self.run_cli("pause", *arguments)
        self.assertEqual((delayed["ticket"], delayed["outcome"]), ("01", "PAUSED"))
        self.assertFalse(delayed["changed"])

    def test_manual_pause_reports_deadlock_until_explicitly_resumed(self) -> None:
        self.fixture.add_ticket("01")
        self.run_cli("next")
        self.run_cli("pause", "--reason", "Waiting for an operator decision.")

        status, _ = self.run_cli("status")
        self.assertEqual(status["outcome"], "DEADLOCK")
        self.assertEqual(status["data"]["resumable"], [])
        blocked, _ = self.run_cli("next", expected=2)
        self.assertEqual(blocked["error"]["code"], "DEADLOCK")

        resumed, _ = self.run_cli("next", "--ticket", "01")
        self.assertEqual(resumed["ticket"], "01")
        self.assertEqual(resumed["outcome"], "WORK_PACKET")

    def test_managed_state_serialization_omits_default_fields(self) -> None:
        ticket = self.fixture.add_ticket("01")
        self.run_cli("next")
        self.run_cli("pause", "--reason", "Waiting for input.")

        match = ticketctl.STATE_RE.search(ticket.read_text(encoding="utf-8"))
        self.assertIsNotNone(match)
        self.assertEqual(
            json.loads(match.group(1)),
            {"version": 1, "pause_reason": "Waiting for input."},
        )
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            ticketctl._replace_state(ticket.read_text(), {"version": 1, "typo": True})

    def test_pause_rejects_an_oversized_reason_before_writing(self) -> None:
        ticket = self.fixture.add_ticket("01")
        self.run_cli("next")

        rejected, _ = self.run_cli("pause", "--reason", "x" * 1_001, expected=2)

        self.assertEqual(rejected["error"]["code"], "REASON_INVALID")
        self.assertFalse(rejected["changed"])
        self.assertIn("**Status:** in-progress", ticket.read_text(encoding="utf-8"))

    def test_pause_rejects_a_remediation_cycle(self) -> None:
        self.fixture.add_ticket("01")
        self.fixture.add_ticket("05", blocked_by="01 - Parent")
        self.run_cli("next", "--ticket", "01")

        payload, _ = self.run_cli(
            "pause",
            "--ticket",
            "01",
            "--until",
            "05",
            "--reason",
            "Needs remediation.",
            expected=2,
        )

        self.assertEqual(payload["error"]["code"], "DEPENDENCY_CYCLE")
        self.assertEqual(
            ticketctl.load_tracker(self.fixture.feature).tickets["01"].status,
            "in-progress",
        )

    def test_confirm_pass_marks_done_with_verified_completion(self) -> None:
        criteria = ("The focused check passes.", "The behavior is observable.")
        self.fixture.add_ticket("01", criteria=criteria)
        self.fixture.add_ticket("02", blocked_by="`01` - First")
        self.run_cli("next", "--ticket", "01")

        packet, _ = self.run_cli("prepare-confirm", "--ticket", "01")
        self.assertEqual(packet["outcome"], "CONFIRMATION_REQUEST")
        self.assertEqual(packet["ticket"], "01")
        self.assertFalse(packet["changed"])
        self.assertEqual(packet["data"]["acceptance_criteria"], list(criteria))
        self.assertEqual(packet["data"]["objective"], "Deliver ticket 01.")
        confirmed = self.submit_result("01", criteria)

        self.assertEqual(confirmed["outcome"], "PASS")
        self.assertTrue(confirmed["changed"])
        self.assertEqual(confirmed["data"]["frontier"], ["02"])
        self.assertEqual(
            confirmed["next"],
            {
                "command": "run-ticket-plan next 02",
                "reason": "oldest executable ticket",
            },
        )
        completed = ticketctl.load_tracker(self.fixture.feature).tickets["01"]
        self.assertEqual(completed.state["completion"]["method"], "verified")

    def test_confirm_retries_stay_idempotent(self) -> None:
        criteria = ("The focused check passes.",)
        ticket = self.fixture.add_ticket("01", criteria=criteria)
        self.fixture.add_ticket("02", blocked_by="01 - First")
        self.run_cli("next")
        result_path = self.result_file("01", criteria)

        first, _ = self.run_cli(
            "confirm", "--ticket", "01", "--result-file", str(result_path)
        )
        repeated, _ = self.run_cli(
            "confirm", "--ticket", "01", "--result-file", str(result_path)
        )

        self.assertTrue(first["changed"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["outcome"], "PASS")
        self.assertEqual(
            ticket.read_text(encoding="utf-8").count(ticketctl.STATE_START), 1
        )
        self.run_cli("next", "--ticket", "02")
        delayed, _ = self.run_cli(
            "confirm", "--ticket", "01", "--result-file", str(result_path)
        )
        self.assertEqual((delayed["ticket"], delayed["outcome"]), ("01", "PASS"))
        self.assertFalse(delayed["changed"])

    def test_confirm_preserves_blocked_and_failed_criterion_evidence(self) -> None:
        criteria = ("The local check passes.", "The external approval exists.")
        self.fixture.add_ticket("01", criteria=criteria)
        self.run_cli("next")
        result = {
            "version": 1,
            "ticket": "01",
            "summary": "Local behavior failed and approval is unavailable.",
            "criteria": [
                {
                    "criterion": criteria[0],
                    "result": "FAIL",
                    "evidence": "Focused test exited 1.",
                },
                {
                    "criterion": criteria[1],
                    "result": "BLOCKED",
                    "evidence": "Independent approval has not been issued.",
                },
            ],
        }
        path = self.root / "blocked-confirmation.json"
        path.write_text(json.dumps(result), encoding="utf-8")

        confirmed, _ = self.run_cli(
            "confirm", "--ticket", "01", "--result-file", str(path)
        )

        self.assertEqual(confirmed["outcome"], "BLOCKED")
        self.assertEqual(confirmed["ticket"], "01")
        self.assertTrue(confirmed["changed"])
        self.assertEqual(confirmed["data"]["summary"], result["summary"])
        self.assertEqual(confirmed["data"]["criteria"], result["criteria"])
        self.assertEqual(
            confirmed["next"],
            {
                "command": "run-ticket-plan confirm 01",
                "reason": "retry after resolving non-passing criteria",
            },
        )
        repeated, _ = self.run_cli("next")
        last_confirmation = repeated["data"]["work_packet"]["history"][
            "last_confirmation"
        ]
        self.assertEqual(
            set(last_confirmation),
            {"result", "result_digest", "summary", "criteria"},
        )
        self.assertEqual(
            last_confirmation["criteria"],
            result["criteria"],
        )

    def test_manual_pass_records_a_distinct_completion_method(self) -> None:
        self.fixture.add_ticket("01")
        self.run_cli("next")

        completed, _ = self.run_cli(
            "pass", "--ticket", "01", "--note", "Verified on a physical device."
        )

        self.assertEqual(completed["outcome"], "PASS")
        self.assertEqual(completed["data"]["completion"]["method"], "manual-pass")
        self.assertEqual(
            completed["data"]["completion"]["note"],
            "Verified on a physical device.",
        )

    def test_manual_pass_rejects_a_blank_note_before_writing(self) -> None:
        ticket = self.fixture.add_ticket("01")
        self.run_cli("next")

        rejected, _ = self.run_cli("pass", "--note", "   ", expected=2)

        self.assertEqual(rejected["error"]["code"], "NOTE_INVALID")
        self.assertFalse(rejected["changed"])
        self.assertIn("**Status:** in-progress", ticket.read_text(encoding="utf-8"))

    def test_manual_pass_retries_stay_idempotent(self) -> None:
        ticket = self.fixture.add_ticket("01")
        self.run_cli("next")
        arguments = ("--ticket", "01", "--note", "Verified by the requester.")

        first, _ = self.run_cli("pass", *arguments)
        repeated, _ = self.run_cli("pass", *arguments)

        self.assertEqual(first["outcome"], "PASS")
        self.assertTrue(first["changed"])
        self.assertEqual(
            first["data"]["completion"],
            {"method": "manual-pass", "note": "Verified by the requester."},
        )
        self.assertIsNone(first["next"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["data"]["completion"], first["data"]["completion"])
        self.assertEqual(
            ticket.read_text(encoding="utf-8").count(ticketctl.STATE_START), 1
        )
        self.fixture.add_ticket("02", blocked_by="01 - First")
        self.run_cli("next", "--ticket", "02")
        delayed, _ = self.run_cli("pass", *arguments)
        self.assertEqual((delayed["ticket"], delayed["outcome"]), ("01", "PASS"))
        self.assertFalse(delayed["changed"])

    def test_confirmation_rejects_invalid_structures(self) -> None:
        criteria = ("Criterion one.", "Criterion two.")
        self.fixture.add_ticket("01", criteria=criteria)
        self.run_cli("next")
        path = self.result_file("01", criteria)
        base = json.loads(path.read_text(encoding="utf-8"))

        def unknown_field(result: dict[str, object]) -> None:
            result["metadata"] = {"unbounded": "payload"}

        def oversized_evidence(result: dict[str, object]) -> None:
            result["criteria"][0]["evidence"] = "x" * 4_001

        def missing_criterion(result: dict[str, object]) -> None:
            result["criteria"] = result["criteria"][:1]

        def inconsistent_subchecks(result: dict[str, object]) -> None:
            result["criteria"][0]["subchecks"] = [
                {"check": "First.", "result": "PASS", "evidence": "Passed."},
                {"check": "Second.", "result": "FAIL", "evidence": "Failed."},
            ]

        for name, mutate in {
            "unknown field": unknown_field,
            "oversized evidence": oversized_evidence,
            "missing criterion": missing_criterion,
            "inconsistent subchecks": inconsistent_subchecks,
        }.items():
            with self.subTest(name=name):
                result = json.loads(json.dumps(base))
                mutate(result)
                path.write_text(json.dumps(result), encoding="utf-8")
                rejected, _ = self.run_cli(
                    "confirm", "--result-file", str(path), expected=2
                )
                self.assertEqual(rejected["error"]["code"], "RESULT_INVALID")

        self.assertEqual(
            ticketctl.load_tracker(self.fixture.feature).tickets["01"].status,
            "in-progress",
        )

    def test_confirmation_rejects_an_oversized_result_file(self) -> None:
        criterion = "The focused check passes."
        self.fixture.add_ticket("01", criteria=(criterion,))
        self.run_cli("next")
        path = self.root / "oversized-confirmation.json"
        path.write_text(
            " " * (ticketctl.CONFIRMATION_RESULT_MAX_BYTES + 1), encoding="utf-8"
        )

        rejected, _ = self.run_cli("confirm", "--result-file", str(path), expected=2)

        self.assertEqual(rejected["error"]["code"], "RESULT_INVALID")

    def test_confirmation_digest_uses_the_canonical_projection(self) -> None:
        criterion = "The focused check passes."
        self.fixture.add_ticket("01", criteria=(criterion,))
        self.run_cli("next")
        path = self.result_file("01", (criterion,))
        result = json.loads(path.read_text(encoding="utf-8"))
        result["summary"] = f"  {result['summary']}  "
        result["criteria"][0]["evidence"] = "  Focused check exited 0.  "
        path.write_text(json.dumps(result), encoding="utf-8")
        first, _ = self.run_cli("confirm", "--ticket", "01", "--result-file", str(path))

        result["summary"] = result["summary"].strip()
        result["criteria"][0]["evidence"] = result["criteria"][0]["evidence"].strip()
        path.write_text(json.dumps(result), encoding="utf-8")
        repeated, _ = self.run_cli(
            "confirm", "--ticket", "01", "--result-file", str(path)
        )

        self.assertTrue(first["changed"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["data"]["summary"], result["summary"])
        self.assertEqual(
            repeated["data"]["criteria"][0]["evidence"],
            result["criteria"][0]["evidence"],
        )

    def test_managed_state_rejects_unknown_fields(self) -> None:
        path = self.fixture.add_ticket("01")
        managed = ticketctl._replace_state(
            path.read_text(encoding="utf-8"), ticketctl._default_state()
        ).replace(
            '"version": 1',
            '"unexpected": {"unbounded": "payload"},\n  "version": 1',
        )
        path.write_text(managed, encoding="utf-8")

        rejected, _ = self.run_cli("status", expected=2)

        self.assertEqual(rejected["error"]["code"], "VALIDATION_ERROR")

    def test_status_aliases_are_normalized(self) -> None:
        self.fixture.add_ticket("01", status="In Progress")
        self.fixture.add_ticket("02", status="passed")
        tracker = ticketctl.load_tracker(self.fixture.feature)
        self.assertEqual(tracker.tickets["01"].status, "in-progress")
        self.assertEqual(tracker.tickets["02"].status, "done")

    def test_isolated_feature_runs_the_full_recovery_loop_to_completion(self) -> None:
        criteria = {
            "01": ("The parent behavior passes.",),
            "02": ("The dependent behavior passes.",),
            "05": ("The parent failure is repaired.",),
        }
        self.fixture.add_ticket("01", criteria=criteria["01"], suffix="parent")
        self.fixture.add_ticket(
            "02", blocked_by="01 - Parent", criteria=criteria["02"], suffix="dependent"
        )
        self.fixture.add_ticket("05", criteria=criteria["05"], suffix="remediation")

        self.run_cli("next", "--ticket", "01")
        failed = self.submit_result("01", criteria["01"], failing=set(criteria["01"]))
        self.assertEqual(failed["outcome"], "FAIL")

        handoff = self.handoff_file("01")
        self.run_cli(
            "pause",
            "--ticket",
            "01",
            "--reason",
            "Parent verification failed.",
            "--until",
            "05",
            "--handoff-file",
            str(handoff),
        )
        remediation, _ = self.run_cli("next")
        self.assertEqual(remediation["ticket"], "05")
        self.submit_result("05", criteria["05"])

        resumed, _ = self.run_cli("next")
        self.assertEqual(resumed["ticket"], "01")
        resumed_packet = resumed["data"]["work_packet"]
        self.assertEqual(resumed_packet["history"]["handoff"], str(handoff.resolve()))
        self.assertEqual(
            resumed_packet["history"]["last_confirmation"]["result"], "FAIL"
        )
        self.submit_result("01", criteria["01"])

        dependent, _ = self.run_cli("next")
        recovered_after_interruption, _ = self.run_cli("next")
        self.assertEqual(
            recovered_after_interruption["data"]["work_packet"],
            dependent["data"]["work_packet"],
        )
        self.submit_result("02", criteria["02"])
        complete, _ = self.run_cli("status")
        self.assertEqual(complete["outcome"], "COMPLETE")
        self.assertEqual(complete["data"]["by_state"], {"done": ["01", "02", "05"]})

    def test_status_output_stays_compact_for_a_large_plan(self) -> None:
        for number in range(1, 61):
            self.fixture.add_ticket(f"{number:02d}")

        payload, stdout = self.run_cli("status")

        self.assertEqual(len(payload["data"]["frontier"]), 60)
        self.assertLess(len(stdout.encode("utf-8")), 2_500)

    def test_mutations_reject_multiple_active_tickets(self) -> None:
        self.fixture.add_ticket("01", status="in-progress")
        self.fixture.add_ticket("02", status="in-progress")

        for command in ("next", "pause", "pass", "prepare-confirm"):
            with self.subTest(command=command):
                arguments = ["--ticket", "01"]
                if command == "pause":
                    arguments.extend(["--reason", "Pause requested."])
                payload, _ = self.run_cli(command, *arguments, expected=2)
                self.assertEqual(payload["error"]["code"], "ACTIVE_TICKET_CONFLICT")

    def test_unknown_command_returns_the_structured_error_envelope(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "unknown-mode"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["command"], "unknown-mode")
        self.assertEqual(payload["outcome"], "ERROR")
        self.assertEqual(payload["error"]["code"], "USAGE_ERROR")

    def test_missing_git_returns_a_structured_environment_error(self) -> None:
        self.fixture.add_ticket("01")
        environment = os.environ.copy()
        environment["PATH"] = ""
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "status",
                "--feature-root",
                str(self.fixture.feature),
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertEqual(payload["outcome"], "ERROR")
        self.assertEqual(payload["error"]["code"], "ENVIRONMENT_ERROR")

    def test_reload_failure_after_ticket_write_reports_committed_change(self) -> None:
        ticket = self.fixture.add_ticket("01")
        real_load_tracker = ticketctl.load_tracker
        calls = 0

        def fail_after_write(feature_root: Path) -> ticketctl.Tracker:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_load_tracker(feature_root)
            raise OSError("simulated reload failure")

        emitted: list[dict[str, object]] = []
        with (
            mock.patch.object(ticketctl, "load_tracker", side_effect=fail_after_write),
            mock.patch.object(ticketctl, "_emit", side_effect=emitted.append),
        ):
            returncode = ticketctl.main(
                ["next", "--feature-root", str(self.fixture.feature), "--ticket", "01"]
            )

        self.assertEqual(returncode, 2)
        self.assertEqual(len(emitted), 1)
        payload = emitted[0]
        self.assertEqual(set(payload), ENVELOPE_KEYS)
        self.assertEqual(payload["outcome"], "ERROR")
        self.assertEqual(payload["ticket"], "01")
        self.assertTrue(payload["changed"])
        self.assertEqual(payload["error"]["code"], "POST_COMMIT_ERROR")
        self.assertIn("**Status:** in-progress", ticket.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
