from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "ticketctl.py"
SPEC = importlib.util.spec_from_file_location("ticketctl", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ticketctl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ticketctl
SPEC.loader.exec_module(ticketctl)


class TrackerFixture:
    def __init__(self, root: Path):
        self.root = root
        self.feature = root / ".scratch" / "demo"
        self.issues = self.feature / "issues"
        self.issues.mkdir(parents=True)
        (self.feature / "spec.md").write_text("# Canonical Spec\n", encoding="utf-8")
        (root / "source.txt").write_text("source\n", encoding="utf-8")
        (root / "check_pass.py").write_text("print('check')\n", encoding="utf-8")
        (root / "check_fail.py").write_text(
            "print('check')\nraise SystemExit(7)\n", encoding="utf-8"
        )

    def add_ticket(
        self,
        ticket_id: str,
        *,
        status: str = "Open",
        depends: str = "None",
        unblocks: str = "None",
        suffix: str = "ticket",
        delivery: str | None = None,
        acceptance: str = "- The local command passes.",
        external: str = "None",
        invariants: str = "- The source artifact exists.",
        evidence: str = "None",
        core_gates: str | None = None,
    ) -> Path:
        delivery = delivery or f"Deliver ticket {ticket_id}."
        gate_metadata = (
            f"\n**Core Completion Gates:** {core_gates}\n"
            if core_gates is not None
            else ""
        )
        path = self.issues / f"{ticket_id}-{suffix}.md"
        path.write_text(
            f"""# {ticket_id} Ticket {ticket_id}

**Status:** {status}

**Wave / Layer:** W0 / Test

**Owner Repo:** {self.root.name}

**Depends On:** {depends}

**Unblocks:** {unblocks}
{gate_metadata}

## Delivery

{delivery}

## Core Acceptance

{acceptance}

## External Integration Evidence

{external}

## Invariants / Out Of Scope

{invariants}

## Evidence

{evidence}

## Unknown Extension

Preserve extension {ticket_id}.

## Comments

Preserve comment {ticket_id}.
""",
            encoding="utf-8",
        )
        return path

    def add_standard_tickets(self) -> None:
        self.add_ticket("00")
        self.add_ticket("01")
        self.add_ticket("02", depends="`01`")
        self.add_ticket("03", depends="`01`")
        self.add_ticket("04", depends="`01`")
        self.add_ticket("05", depends="`00`, `01`")
        self.add_ticket("13", depends="`00`")

    def set_status(self, ticket_id: str, status: str) -> None:
        tracker = ticketctl.load_tracker(self.feature)
        ticket = tracker.tickets[ticket_id]
        ticket.path.write_text(
            ticketctl._replace_metadata(ticket.text, "Status", status), encoding="utf-8"
        )

    def add_plan(
        self,
        ticket_id: str,
        *,
        command_exit: int = 0,
        extra_checks: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        plan = self.plan_payload(
            ticket_id,
            command_exit=command_exit,
            extra_checks=extra_checks,
        )
        self.write_plan(ticket_id, plan)
        return plan

    def plan_payload(
        self,
        ticket_id: str,
        *,
        command_exit: int = 0,
        extra_checks: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        command = [
            sys.executable,
            "check_pass.py" if command_exit == 0 else "check_fail.py",
        ]
        checks: list[dict[str, object]] = [
            {
                "id": "AC-1",
                "source": "Core Acceptance",
                "requirement": "The local command passes.",
                "kind": "command",
                "command": command,
                "expected": "exit_code == 0",
                "blocking": True,
                "covers": ["DEL-1", "AC-1"],
            },
            {
                "id": "INV-1",
                "source": "Invariants / Out Of Scope",
                "requirement": "The source artifact exists.",
                "kind": "file",
                "path": "source.txt",
                "expected": "path exists and hash is recorded",
                "blocking": True,
                "covers": ["INV-1"],
            },
        ]
        checks.extend(extra_checks or [])
        return {"version": 1, "ticket": ticket_id, "checks": checks}

    def write_plan(self, ticket_id: str, plan: dict[str, object]) -> None:
        tracker = ticketctl.load_tracker(self.feature)
        ticket = tracker.tickets[ticket_id]
        body = f"```json\n{json.dumps(plan, indent=2)}\n```"
        ticket.path.write_text(
            ticketctl._replace_or_insert_section(
                ticket.text, "Verification Plan", body
            ),
            encoding="utf-8",
        )

    def set_section(self, ticket_id: str, section: str, body: str) -> None:
        tracker = ticketctl.load_tracker(self.feature)
        ticket = tracker.tickets[ticket_id]
        ticket.path.write_text(
            ticketctl._replace_or_insert_section(ticket.text, section, body),
            encoding="utf-8",
        )

    def latest_result(self, ticket_id: str) -> dict[str, object]:
        ticket = ticketctl.load_tracker(self.feature).tickets[ticket_id]
        return ticketctl._load_results(ticket)[-1]

    def write_single_result(self, ticket_id: str, result: dict[str, object]) -> None:
        ticket = ticketctl.load_tracker(self.feature).tickets[ticket_id]
        body = ticketctl._result_body(None, result)
        ticket.path.write_text(
            ticketctl._replace_or_insert_section(
                ticket.text, "Verification Result", body
            ),
            encoding="utf-8",
        )

    def add_roadmap(
        self,
        frontier: list[str] | None = None,
        *,
        filename: str = "roadmap.md",
        heading_cards: bool = False,
        markers: bool = True,
    ) -> Path:
        tracker = ticketctl.load_tracker(self.feature)
        ready = ticketctl.frontier(tracker) if frontier is None else frontier
        if heading_cards:
            ticket_lines = "\n\n".join(
                f"#### [{'x' if ticket.status == 'Done' else ' '}] "
                f"[`{ticket_id}` Ticket {ticket_id}](issues/{ticket.path.name})\n\n"
                f"- Contract note for {ticket_id}; preserve exactly."
                for ticket_id, ticket in sorted(tracker.tickets.items())
            )
        else:
            ticket_lines = "\n".join(
                f"- [{'x' if ticket.status == 'Done' else ' '}] `{ticket_id}` Ticket {ticket_id}"
                for ticket_id, ticket in sorted(tracker.tickets.items())
            )
        frontier_lines = (
            "\n".join(f"- `{ticket_id}`" for ticket_id in ready) or "_None._"
        )
        frontier_body = (
            f"<!-- ticketctl:frontier:start -->\n"
            f"{frontier_lines}\n"
            f"<!-- ticketctl:frontier:end -->"
            if markers
            else frontier_lines
        )
        path = self.feature / filename
        path.write_text(
            f"""# Roadmap

## Tickets

{ticket_lines}

## Current Frontier

{frontier_body}

## Product Notes

Never rewrite this content.
""",
            encoding="utf-8",
        )
        return path


class TicketCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = TrackerFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self, *arguments: str, expected: int = 0
    ) -> tuple[dict[str, object], str]:
        return self.run_fixture_cli(self.fixture, *arguments, expected=expected)

    def run_fixture_cli(
        self,
        fixture: TrackerFixture,
        *arguments: str,
        expected: int = 0,
    ) -> tuple[dict[str, object], str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=fixture.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode, expected, completed.stderr + completed.stdout
        )
        return json.loads(completed.stdout), completed.stderr

    def common_args(self, command: str) -> list[str]:
        return [command, "--feature-root", str(self.fixture.feature)]

    def write_plan_input(
        self, fixture: TrackerFixture, plan: dict[str, object]
    ) -> Path:
        path = fixture.root.parent / f".{fixture.root.name}-{plan['ticket']}-plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def start_fixture_cli(
        self, fixture: TrackerFixture, *arguments: str
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=fixture.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def finish_fixture_cli(
        self, process: subprocess.Popen[str], *, expected: int = 0
    ) -> tuple[dict[str, object], str]:
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, expected, stderr + stdout)
        return json.loads(stdout), stderr

    def add_pausing_check(self, fixture: TrackerFixture) -> None:
        (fixture.root / "check_pause.py").write_text(
            "import time\n\ntime.sleep(0.75)\nprint('released')\n",
            encoding="utf-8",
        )

    def complete_pending_external(
        self,
        fixture: TrackerFixture,
        *,
        consumer: bool = False,
        external_only: bool = False,
    ) -> None:
        fixture.add_ticket(
            "00",
            delivery="None" if external_only else None,
            acceptance="None" if external_only else "- The local command passes.",
            external="- Independent consumer conformance.",
            invariants="None" if external_only else "- The source artifact exists.",
        )
        if consumer:
            fixture.add_ticket("01", depends="`00`")
        args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
        self.run_fixture_cli(fixture, "claim", *args)
        external_check: dict[str, object] = {
            "id": "EXT-1",
            "source": "External Integration Evidence",
            "requirement": "Independent consumer conformance.",
            "kind": "external",
            "expected": "consumer PASS",
            "blocking": False,
            "covers": ["EXT-1"],
        }
        if external_only:
            fixture.write_plan(
                "00", {"version": 1, "ticket": "00", "checks": [external_check]}
            )
        else:
            fixture.add_plan("00", extra_checks=[external_check])
        self.run_fixture_cli(fixture, "verify", *args)
        self.run_fixture_cli(fixture, "complete", *args)

    def complete_two_pending_externals(self, fixture: TrackerFixture) -> None:
        fixture.add_ticket(
            "00",
            external=(
                "- Independent consumer conformance.\n- Independent operator handoff."
            ),
        )
        args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
        self.run_fixture_cli(fixture, "claim", *args)
        fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer conformance.",
                    "kind": "external",
                    "expected": "consumer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                },
                {
                    "id": "EXT-2",
                    "source": "External Integration Evidence",
                    "requirement": "Independent operator handoff.",
                    "kind": "manual-review",
                    "expected": "operator PASS",
                    "blocking": False,
                    "covers": ["EXT-2"],
                },
            ],
        )
        self.run_fixture_cli(fixture, "verify", *args)
        self.run_fixture_cli(fixture, "complete", *args)

    def record_external(
        self,
        fixture: TrackerFixture,
        check_id: str,
        *,
        repair: bool = False,
    ) -> dict[str, object]:
        arguments = [
            "record-external",
            "--feature-root",
            str(fixture.feature),
            "--ticket",
            "00",
            "--check",
            check_id,
            "--result",
            "PASS",
            "--reference",
            f"artifact://handoff/{check_id.lower()}",
            "--issuer",
            f"issuer-{check_id.lower()}",
        ]
        if repair:
            arguments.append("--repair")
        payload, _ = self.run_fixture_cli(fixture, *arguments)
        return payload

    @staticmethod
    def git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )

    def initialize_git(self, fixture: TrackerFixture) -> None:
        self.git(fixture.root, "init", "--quiet")
        self.git(fixture.root, "config", "user.name", "Ticket Test")
        self.git(fixture.root, "config", "user.email", "ticket@example.invalid")

    def commit_all(
        self, fixture: TrackerFixture, message: str = "tracker baseline"
    ) -> None:
        self.initialize_git(fixture)
        self.git(fixture.root, "add", ".")
        self.git(fixture.root, "commit", "--quiet", "-m", message)

    def test_status_is_read_only_and_reports_control_plane_state(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01")
        self.commit_all(self.fixture)
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }

        status, _ = self.run_cli(*self.common_args("status"))

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        self.assertEqual(after, before)
        self.assertIsNone(status["active_ticket"])
        self.assertEqual(status["frontier"], ["00", "01"])
        self.assertEqual(status["recommended_next"], "00")
        self.assertEqual(status["roadmap"]["state"], "MISSING")
        self.assertEqual(status["tickets"]["00"]["verification_plan"], "MISSING")
        self.assertEqual(status["tracker_baseline"]["status"], "PASS")

    def test_prepare_next_returns_minimal_read_only_planning_packet(self) -> None:
        self.fixture.add_ticket(
            "00",
            unblocks="`01`",
            external="- Independent consumer confirms the contract.",
        )
        self.fixture.add_ticket("01", depends="`00`")
        self.commit_all(self.fixture)
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }

        packet, _ = self.run_cli(*self.common_args("prepare-next"))

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }
        self.assertEqual(after, before)
        self.assertEqual(packet["command"], "prepare-next")
        self.assertEqual(packet["packet_version"], 1)
        self.assertEqual(packet["ticket"]["id"], "00")
        self.assertEqual(packet["ticket"]["status"], "Open")
        self.assertEqual(packet["ticket"]["plan_state"], "MISSING")
        self.assertTrue(packet["planning_required"])
        self.assertFalse(packet["recovered_activation"])
        self.assertEqual(packet["frontier"], ["00"])
        self.assertEqual(
            packet["requirements"],
            [
                {
                    "id": "DEL-1",
                    "source": "Delivery",
                    "requirement": "Deliver ticket 00.",
                    "blocking_required": False,
                },
                {
                    "id": "AC-1",
                    "source": "Core Acceptance",
                    "requirement": "The local command passes.",
                    "blocking_required": True,
                },
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer confirms the contract.",
                    "blocking_required": False,
                },
                {
                    "id": "INV-1",
                    "source": "Invariants / Out Of Scope",
                    "requirement": "The source artifact exists.",
                    "blocking_required": True,
                },
            ],
        )
        self.assertEqual(packet["plan_contract"]["version"], 1)
        self.assertEqual(packet["plan_contract"]["ticket"], "00")
        self.assertEqual(
            packet["plan_contract"]["allowed_check_kinds"],
            ["command", "file", "assertion", "artifact", "external", "manual-review"],
        )
        self.assertEqual(packet["tracker_baseline"]["status"], "PASS")
        self.assertIn("sha256", packet["canonical_spec"])
        self.assertIn("immutable_contract_hash", packet)
        self.assertNotIn("tickets", packet)
        self.assertNotIn("state_drift", packet)

    def test_prepare_next_resumes_interrupted_activation_and_reuses_valid_plan(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01")
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")

        interrupted, _ = self.run_cli(*self.common_args("prepare-next"))

        self.assertEqual(interrupted["ticket"]["id"], "00")
        self.assertEqual(interrupted["ticket"]["status"], "In Progress")
        self.assertTrue(interrupted["recovered_activation"])
        self.assertTrue(interrupted["planning_required"])
        rejected, _ = self.run_cli(
            *self.common_args("prepare-next"), "--ticket", "01", expected=2
        )
        self.assertEqual(rejected["error"], "ACTIVE_TICKET_EXISTS")
        self.assertEqual(rejected["details"]["active_ticket"], "00")

        plan = self.fixture.plan_payload("00")
        self.fixture.write_plan("00", plan)
        reusable, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "00")

        self.assertFalse(reusable["planning_required"])
        self.assertEqual(reusable["ticket"]["plan_state"], "VALID")
        self.assertEqual(reusable["existing_plan"]["hash"], ticketctl._plan_hash(plan))
        self.assertEqual(reusable["existing_plan"]["check_ids"], ["AC-1", "INV-1"])

    def test_next_reuses_valid_plan_already_on_open_ticket(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        plan = self.fixture.add_plan("00")

        packet, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "00")
        activated, _ = self.run_cli(*self.common_args("next"), "--ticket", "00")

        self.assertFalse(packet["planning_required"])
        self.assertEqual(packet["ticket"]["plan_state"], "VALID")
        self.assertFalse(activated["recovered"])
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(ticket.status, "In Progress")
        self.assertEqual(ticketctl._load_plan(ticket), plan)

    def test_open_ticket_invalid_plan_is_never_overwritten(self) -> None:
        path = self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        invalid_plan = self.fixture.plan_payload("00")
        invalid_plan["checks"][0]["source"] = "Invented Section"
        self.fixture.write_plan("00", invalid_plan)
        original = path.read_bytes()

        prepared, _ = self.run_cli(
            *self.common_args("prepare-next"), "--ticket", "00", expected=2
        )
        valid_path = self.write_plan_input(
            self.fixture, self.fixture.plan_payload("00")
        )
        activated, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "00",
            "--plan-file",
            str(valid_path),
            expected=2,
        )

        self.assertEqual(prepared["error"], "PLAN_INVALID")
        self.assertEqual(activated["error"], "PLAN_INVALID")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.fixture.feature / "implementation-roadmap.md").exists())

    def test_open_ticket_valid_plan_rejects_conflicting_replacement(self) -> None:
        path = self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        self.fixture.add_plan("00")
        original = path.read_bytes()
        conflicting_plan = self.fixture.plan_payload("00")
        conflicting_plan["checks"][0]["id"] = "ALT-1"
        plan_path = self.write_plan_input(self.fixture, conflicting_plan)

        rejected, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "00",
            "--plan-file",
            str(plan_path),
            expected=2,
        )

        self.assertEqual(rejected["error"], "PLAN_CONFLICT")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.fixture.feature / "implementation-roadmap.md").exists())

    def test_prepare_next_allows_legacy_done_dependencies_and_malformed_roadmap(
        self,
    ) -> None:
        self.fixture.add_ticket("00", status="Done")
        self.fixture.add_ticket("01", depends="`00`")
        self.commit_all(self.fixture)
        roadmap = self.fixture.feature / "implementation-roadmap.md"
        roadmap.write_text("# malformed derived view\n", encoding="utf-8")
        before = roadmap.read_bytes()

        packet, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "01")

        self.assertEqual(packet["ticket"]["id"], "01")
        self.assertEqual(packet["nonfatal_drift_summary"]["migratable"], 1)
        self.assertEqual(packet["roadmap"]["state"], "MALFORMED")
        self.assertEqual(roadmap.read_bytes(), before)

    def test_next_rechecks_tracker_after_prepare_next(self) -> None:
        path = self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        packet, _ = self.run_cli(*self.common_args("prepare-next"))
        self.assertEqual(packet["ticket"]["id"], "00")
        plan_path = self.write_plan_input(self.fixture, self.fixture.plan_payload("00"))
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Deliver ticket 00.", "Changed after packet generation."
            ),
            encoding="utf-8",
        )

        rejected, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "00",
            "--plan-file",
            str(plan_path),
            expected=2,
        )

        self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(ticket.status, "Open")
        self.assertNotIn("Verification Plan", ticket.sections)

    def test_prepare_next_packet_contains_complete_plan_generation_constraints(
        self,
    ) -> None:
        self.fixture.add_ticket(
            "00",
            external="- Independent device verification must PASS.",
            core_gates="EXT-1",
        )
        self.commit_all(self.fixture)

        packet, _ = self.run_cli(*self.common_args("prepare-next"))

        external = next(
            item for item in packet["requirements"] if item["id"] == "EXT-1"
        )
        self.assertTrue(external["blocking_required"])
        contract = packet["plan_contract"]
        self.assertTrue(contract["checks_must_have_unique_ids"])
        self.assertTrue(contract["core_gate_coverage_must_be_blocking"])
        self.assertTrue(
            contract["repository_instructions_must_not_add_or_reinterpret_requirements"]
        )
        self.assertEqual(
            contract["verification_mechanics_must_be_grounded_in"],
            ["Planning Packet", "applicable repository instructions"],
        )
        self.assertEqual(
            contract["unplanable_blocking_requirement_outcome"],
            "CONTRACT_NOT_PLANABLE",
        )
        self.assertTrue(contract["external_only_checks_must_be_nonblocking"])
        self.assertEqual(
            contract["kind_requirements"]["command"],
            {"requires": ["command"], "expected": "exit_code == 0"},
        )
        self.assertEqual(contract["kind_requirements"]["file"], {"requires": ["path"]})
        self.assertEqual(
            contract["kind_requirements"]["external"]["evidence"],
            "independent issuer only",
        )
        self.assertIn(
            "shell executables and operators", contract["prohibited_commands"]
        )
        self.assertIn("publish, deploy, or release", contract["prohibited_commands"])

    def test_prepare_next_stays_compact_for_large_existing_project(self) -> None:
        large_spec = "# Canonical Spec\n\n" + ("Frozen product context.\n" * 4000)
        (self.fixture.feature / "spec.md").write_text(large_spec, encoding="utf-8")
        for number in range(60):
            self.fixture.add_ticket(f"{number:02d}")
        roadmap = self.fixture.add_roadmap()
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8")
            + "\n## Large Preserved Context\n\n"
            + ("Roadmap prose that the planner must not load.\n" * 2000),
            encoding="utf-8",
        )
        self.commit_all(self.fixture)

        packet, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "00")
        encoded = json.dumps(packet, ensure_ascii=False)

        self.assertEqual(len(packet["frontier"]), 60)
        self.assertLess(len(encoded.encode("utf-8")), 12_000)
        self.assertNotIn("Frozen product context.", encoded)
        self.assertNotIn("Roadmap prose that the planner must not load.", encoded)
        self.assertNotIn("Deliver ticket 59.", encoded)

    def test_prepare_next_supports_non_git_project_without_writes(self) -> None:
        self.fixture.add_ticket("00")
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        packet, _ = self.run_cli(*self.common_args("prepare-next"))

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(packet["ticket"]["id"], "00")
        self.assertEqual(
            packet["tracker_baseline"],
            {"status": "NOT_A_GIT_REPOSITORY", "revision": None},
        )
        self.assertEqual(packet["roadmap"]["state"], "MISSING")

    def test_prepare_next_recovers_configured_roadmap_midstream(self) -> None:
        self.fixture.add_ticket("00")
        roadmap = self.fixture.add_roadmap(filename="delivery-plan.md")
        (self.fixture.feature / ".ticketctl.json").write_text(
            json.dumps({"roadmap": "delivery-plan.md"}), encoding="utf-8"
        )
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.write_plan("00", self.fixture.plan_payload("00"))
        roadmap_before = roadmap.read_bytes()

        packet, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "00")

        self.assertTrue(packet["recovered_activation"])
        self.assertFalse(packet["planning_required"])
        self.assertEqual(packet["roadmap"]["path"], str(roadmap.resolve()))
        self.assertEqual(packet["roadmap"]["state"], "STALE")
        self.assertEqual(roadmap.read_bytes(), roadmap_before)

        recovered, _ = self.run_cli(*self.common_args("next"), "--ticket", "00")

        self.assertTrue(recovered["recovered"])
        self.assertTrue(recovered["roadmap"]["changed"])
        self.assertEqual(recovered["roadmap"]["path"], str(roadmap.resolve()))
        self.assertFalse(
            (self.fixture.feature / "implementation-roadmap.md").exists()
        )
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["roadmap"]["state"], "CURRENT")

    def test_prepare_next_preserves_large_selected_ticket_contract(self) -> None:
        large_requirement = (
            "Selected ticket contract " + ("must remain exact. " * 1200)
        ).rstrip()
        self.fixture.add_ticket("00", acceptance=f"- {large_requirement}")
        self.fixture.add_ticket(
            "01", acceptance=f"- {'Unselected contract. ' * 1200}"
        )
        self.commit_all(self.fixture)

        packet, _ = self.run_cli(*self.common_args("prepare-next"), "--ticket", "00")
        encoded = json.dumps(packet, ensure_ascii=False)
        acceptance = next(
            requirement
            for requirement in packet["requirements"]
            if requirement["id"] == "AC-1"
        )

        self.assertEqual(acceptance["requirement"], large_requirement)
        self.assertGreater(len(encoded.encode("utf-8")), 20_000)
        self.assertNotIn("Unselected contract.", encoded)

    def test_prepare_next_fails_closed_for_ineligible_project_states(self) -> None:
        scenarios = (
            "blocked",
            "multiple-active",
            "fatal",
            "no-frontier",
            "blocked-dependency",
        )
        for scenario in scenarios:
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                if scenario == "fatal":
                    fixture.add_ticket("00", status="Done")
                    fixture.set_section(
                        "00",
                        "Verification Result",
                        '```json\n{"version": 1, "ticket": "00", "result": "PASS"}\n```',
                    )
                elif scenario == "no-frontier":
                    fixture.add_ticket("00", status="Done")
                elif scenario == "blocked-dependency":
                    fixture.add_ticket("00")
                    fixture.add_ticket("01", depends="`00`")
                else:
                    fixture.add_ticket("00")
                    if scenario == "multiple-active":
                        fixture.add_ticket("01")
                self.commit_all(fixture)

                requested_ticket: str | None = None
                if scenario == "blocked":
                    fixture.set_status("00", "Blocked")
                elif scenario == "multiple-active":
                    fixture.set_status("00", "In Progress")
                    fixture.set_status("01", "In Progress")
                elif scenario == "blocked-dependency":
                    requested_ticket = "01"

                arguments = [
                    "prepare-next",
                    "--feature-root",
                    str(fixture.feature),
                ]
                if requested_ticket is not None:
                    arguments.extend(["--ticket", requested_ticket])
                rejected, _ = self.run_fixture_cli(fixture, *arguments, expected=2)

                expected_error = {
                    "blocked": "ACTIVE_TICKET_EXISTS",
                    "multiple-active": "ACTIVE_TICKET_CONFLICT",
                    "fatal": "STATE_DRIFT",
                    "no-frontier": "NO_FRONTIER",
                    "blocked-dependency": "NOT_FRONTIER",
                }[scenario]
                self.assertEqual(rejected["error"], expected_error)

    def test_fresh_project_runs_prepare_next_through_confirm(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)

        packet, _ = self.run_cli(*self.common_args("prepare-next"))
        plan = self.fixture.plan_payload(packet["ticket"]["id"])
        plan_path = self.write_plan_input(self.fixture, plan)
        activated, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            packet["ticket"]["id"],
            "--plan-file",
            str(plan_path),
        )
        (self.root / "source.txt").write_text(
            "implemented after planning packet\n", encoding="utf-8"
        )

        confirmed, _ = self.run_cli(
            *self.common_args("confirm"), "--ticket", packet["ticket"]["id"]
        )

        self.assertEqual(activated["status"], "In Progress")
        self.assertEqual(confirmed["result"], "PASS")
        self.assertEqual(confirmed["status"], "Done")
        self.assertEqual(
            ticketctl.load_tracker(self.fixture.feature).tickets["00"].status, "Done"
        )

    def test_next_activates_lowest_frontier_and_emits_work_packet(self) -> None:
        self.fixture.add_ticket("00", unblocks="`01`")
        self.fixture.add_ticket("01")
        self.commit_all(self.fixture)
        plan = self.fixture.plan_payload("00")
        plan_path = self.write_plan_input(self.fixture, plan)
        product_before = {
            name: (self.root / name).read_bytes()
            for name in ("source.txt", "check_pass.py", "check_fail.py")
        }

        activated, _ = self.run_cli(
            *self.common_args("next"), "--plan-file", str(plan_path)
        )

        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        self.assertEqual(ticket.status, "In Progress")
        self.assertEqual(ticketctl._load_plan(ticket), plan)
        roadmap = self.fixture.feature / "implementation-roadmap.md"
        self.assertTrue(roadmap.is_file())
        roadmap_text = roadmap.read_text(encoding="utf-8")
        self.assertIn("#### [ ] [`00` Ticket 00](issues/00-ticket.md)", roadmap_text)
        self.assertIn("- Function: Deliver ticket 00.", roadmap_text)
        self.assertIn("- Verification:", roadmap_text)
        self.assertIn("- Depends On: None", roadmap_text)
        self.assertIn("- Unblocks: `01`", roadmap_text)
        packet = activated["work_packet"]
        self.assertEqual(packet["ticket_id"], "00")
        self.assertEqual(packet["status"], "In Progress")
        self.assertEqual(packet["verification_plan_hash"], ticketctl._plan_hash(plan))
        self.assertEqual(packet["verification_check_ids"], ["AC-1", "INV-1"])
        self.assertEqual(Path(packet["roadmap_path"]), roadmap.resolve())
        self.assertEqual(packet["handoff_command"], "run-ticket-plan confirm 00")
        self.assertEqual(
            {
                name: (self.root / name).read_bytes()
                for name in ("source.txt", "check_pass.py", "check_fail.py")
            },
            product_before,
        )
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tracker_baseline"]["status"], "PASS")
        self.assertEqual(status["active_ticket"], "00")
        self.assertEqual(status["roadmap"]["state"], "CURRENT")

    def test_next_rejects_nonfrontier_and_a_second_execution_lane(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00`")
        self.commit_all(self.fixture)
        plan_01 = self.write_plan_input(self.fixture, self.fixture.plan_payload("01"))
        rejected, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "01",
            "--plan-file",
            str(plan_01),
            expected=2,
        )
        self.assertEqual(rejected["error"], "NOT_FRONTIER")

        plan_00 = self.write_plan_input(self.fixture, self.fixture.plan_payload("00"))
        self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "00",
            "--plan-file",
            str(plan_00),
        )
        blocked, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "01",
            "--plan-file",
            str(plan_01),
            expected=2,
        )
        self.assertEqual(blocked["error"], "ACTIVE_TICKET_EXISTS")
        self.assertEqual(blocked["details"]["active_ticket"], "00")

    def test_next_recovers_partial_activation_and_is_idempotent(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        plan = self.fixture.plan_payload("00")
        plan_path = self.write_plan_input(self.fixture, plan)

        recovered, _ = self.run_cli(
            *self.common_args("next"),
            "--ticket",
            "00",
            "--plan-file",
            str(plan_path),
        )
        repeated, _ = self.run_cli(*self.common_args("next"), "--ticket", "00")

        self.assertTrue(recovered["recovered"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(
            repeated["work_packet"]["verification_plan_hash"],
            recovered["work_packet"]["verification_plan_hash"],
        )
        self.assertEqual(repeated["roadmap"]["changed"], False)

    def test_next_rejects_invalid_plan_without_mutation(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        plan = self.fixture.plan_payload("00")
        plan["checks"] = plan["checks"][:1]
        plan_path = self.write_plan_input(self.fixture, plan)

        rejected, _ = self.run_cli(
            *self.common_args("next"),
            "--plan-file",
            str(plan_path),
            expected=2,
        )

        self.assertEqual(rejected["error"], "PLAN_INVALID")
        self.assertEqual(
            ticketctl.load_tracker(self.fixture.feature).tickets["00"].status, "Open"
        )
        self.assertFalse((self.fixture.feature / "implementation-roadmap.md").exists())

    def test_next_rejects_forged_requirement_binding_without_mutation(self) -> None:
        for field, forged_value in (
            ("source", "Invented Section"),
            ("requirement", "Invented acceptance text."),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                self.commit_all(fixture)
                plan = fixture.plan_payload("00")
                plan["checks"][0][field] = forged_value
                plan_path = self.write_plan_input(fixture, plan)

                rejected, _ = self.run_fixture_cli(
                    fixture,
                    "next",
                    "--feature-root",
                    str(fixture.feature),
                    "--plan-file",
                    str(plan_path),
                    expected=2,
                )

                self.assertEqual(rejected["error"], "PLAN_INVALID")
                self.assertIn(
                    "frozen requirement", " ".join(rejected["details"]["errors"])
                )
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                self.assertEqual(ticket.status, "Open")
                self.assertNotIn("Verification Plan", ticket.sections)
                self.assertFalse(
                    (fixture.feature / "implementation-roadmap.md").exists()
                )

    def test_confirm_pass_completes_and_repeated_confirm_is_rejected(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        plan_path = self.write_plan_input(self.fixture, self.fixture.plan_payload("00"))
        self.run_cli(*self.common_args("next"), "--plan-file", str(plan_path))
        source = self.root / "source.txt"
        source.write_text("implemented by the external worker\n", encoding="utf-8")
        implemented_source = source.read_bytes()

        confirmed, _ = self.run_cli(*self.common_args("confirm"))

        self.assertEqual(confirmed["result"], "PASS")
        self.assertEqual(confirmed["status"], "Done")
        self.assertEqual(confirmed["frontier"], [])
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(ticket.status, "Done")
        self.assertEqual(source.read_bytes(), implemented_source)
        self.assertIn("Verification PASS at", ticket.sections["Evidence"])
        roadmap = (self.fixture.feature / "implementation-roadmap.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("#### [x] [`00`", roadmap)
        rejected, _ = self.run_cli(
            *self.common_args("confirm"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "INVALID_TRANSITION")

    def test_confirm_rejects_forged_requirement_binding_before_checks(self) -> None:
        self.fixture.add_ticket("00")
        marker = self.root / "verification-ran.txt"
        (self.root / "check_marker.py").write_text(
            "from pathlib import Path\nPath('verification-ran.txt').write_text('ran')\n",
            encoding="utf-8",
        )
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        plan = self.fixture.plan_payload("00")
        plan["checks"][0]["source"] = "Invented Section"
        plan["checks"][0]["requirement"] = "Invented acceptance text."
        plan["checks"][0]["command"] = [sys.executable, "check_marker.py"]
        self.fixture.write_plan("00", plan)

        rejected, _ = self.run_cli(*self.common_args("confirm"), expected=2)

        self.assertEqual(rejected["error"], "PLAN_INVALID")
        self.assertFalse(marker.exists())
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(ticket.status, "In Progress")
        self.assertNotIn("Verification Result", ticket.sections)

    def test_confirm_remediate_and_blocked_never_repair_source(self) -> None:
        for outcome in ("REMEDIATE", "BLOCKED"):
            with (
                self.subTest(outcome=outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                if outcome == "REMEDIATE":
                    fixture.add_ticket("00")
                    plan = fixture.plan_payload("00", command_exit=7)
                else:
                    fixture.add_ticket(
                        "00",
                        acceptance="None",
                        external="- Independent approval.",
                        invariants="None",
                        core_gates="EXT-1",
                    )
                    plan = {
                        "version": 1,
                        "ticket": "00",
                        "checks": [
                            {
                                "id": "EXT-1",
                                "source": "External Integration Evidence",
                                "requirement": "Independent approval.",
                                "kind": "manual-review",
                                "expected": "independent PASS",
                                "blocking": True,
                                "covers": ["DEL-1", "EXT-1"],
                            }
                        ],
                    }
                self.commit_all(fixture)
                plan_path = self.write_plan_input(fixture, plan)
                self.run_fixture_cli(
                    fixture,
                    "next",
                    "--feature-root",
                    str(fixture.feature),
                    "--plan-file",
                    str(plan_path),
                )
                source_before = (fixture.root / "source.txt").read_bytes()

                confirmed, _ = self.run_fixture_cli(
                    fixture,
                    "confirm",
                    "--feature-root",
                    str(fixture.feature),
                )

                self.assertEqual(confirmed["result"], outcome)
                expected_status = "In Progress" if outcome == "REMEDIATE" else "Blocked"
                self.assertEqual(confirmed["status"], expected_status)
                self.assertEqual(
                    (fixture.root / "source.txt").read_bytes(), source_before
                )
                self.assertEqual(
                    confirmed["handoff"]["implementation_agent_may_edit_source"],
                    outcome == "REMEDIATE",
                )

    def test_confirm_blocks_source_mutating_check_without_touching_real_worktree(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        (self.root / "check_mutates_source.py").write_text(
            "from pathlib import Path\n"
            "Path('source.txt').write_text('mutated by verification\\n')\n"
            "print('mutation attempted')\n",
            encoding="utf-8",
        )
        self.commit_all(self.fixture)
        plan = self.fixture.plan_payload("00")
        plan["checks"][0]["command"] = [sys.executable, "check_mutates_source.py"]
        plan_path = self.write_plan_input(self.fixture, plan)
        self.run_cli(*self.common_args("next"), "--plan-file", str(plan_path))
        source = self.root / "source.txt"
        source_before = source.read_bytes()

        confirmed, _ = self.run_cli(*self.common_args("confirm"))

        self.assertEqual(confirmed["result"], "BLOCKED")
        self.assertEqual(confirmed["status"], "Blocked")
        self.assertEqual(source.read_bytes(), source_before)
        command_result = confirmed["verification"]["checks"][0]
        self.assertEqual(command_result["result"], "BLOCKED")
        self.assertIn("isolated source", command_result["evidence"]["error"])
        self.assertIn("side-effect free", command_result["unlocking_condition"])

    def test_confirm_sandbox_denies_absolute_write_to_real_worktree(self) -> None:
        self.fixture.add_ticket("00")
        source = self.root / "source.txt"
        (self.root / "check_writes_real_source.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(source)!r}).write_text('escaped sandbox\\n')\n",
            encoding="utf-8",
        )
        self.commit_all(self.fixture)
        plan = self.fixture.plan_payload("00")
        plan["checks"][0]["command"] = [sys.executable, "check_writes_real_source.py"]
        plan_path = self.write_plan_input(self.fixture, plan)
        self.run_cli(*self.common_args("next"), "--plan-file", str(plan_path))
        source_before = source.read_bytes()

        confirmed, _ = self.run_cli(*self.common_args("confirm"))

        self.assertIn(confirmed["result"], {"REMEDIATE", "BLOCKED"})
        self.assertNotEqual(confirmed["status"], "Done")
        self.assertEqual(source.read_bytes(), source_before)

    def test_roadmap_is_derived_and_can_be_explicitly_regenerated(self) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        roadmap = self.fixture.add_roadmap()
        baseline = ticketctl.tracker_baseline(
            ticketctl.load_tracker(self.fixture.feature)
        )
        self.assertEqual(baseline["status"], "PASS")
        self.assertNotIn(
            roadmap.relative_to(self.root).as_posix(), baseline["untracked_files"]
        )
        roadmap.write_text("# broken roadmap\n", encoding="utf-8")
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["roadmap"]["state"], "MALFORMED")

        repaired, _ = self.run_cli(*self.common_args("sync-roadmap"), "--regenerate")

        self.assertTrue(repaired["changed"])
        self.assertEqual(
            ticketctl.roadmap_drift(ticketctl.load_tracker(self.fixture.feature)), []
        )

    def test_removed_public_modes_return_migration_guidance(self) -> None:
        for command in ("run", "continue"):
            with self.subTest(command=command):
                rejected, _ = self.run_cli(
                    command,
                    "--feature-root",
                    str(self.fixture.feature),
                    expected=2,
                )
                self.assertEqual(rejected["error"], "MIGRATION_REQUIRED")
                self.assertIn("status, next, or confirm", rejected["message"])

    def test_dependency_range_expansion_and_external_references(self) -> None:
        parsed = ticketctl.parse_references("`03-05`, `57 PASS`, `E00-E02`")
        self.assertEqual(
            [dependency.ticket_id for dependency in parsed],
            ["03", "04", "05", "57", "E00", "E01", "E02"],
        )
        self.assertEqual(parsed[3].condition, "PASS")
        self.assertTrue(all(item.external for item in parsed[-3:]))

        self.fixture.add_ticket("00", depends="`E00-E14`")
        payload, _ = self.run_cli(*self.common_args("validate"))
        self.assertTrue(payload["valid"])

    def test_prose_dependencies_are_explicit_and_do_not_cascade_errors(self) -> None:
        examples = {
            "`05`、`07`、`13`，并由此传递门禁": ["05", "07", "13"],
            "timestamp-mode `47-48`、`50-52`": ["47", "48", "50", "51", "52"],
            "PASS -> `58`; REMEDIATE -> operator action": ["58"],
            "`54-56` 与同一 Core source commit 的 `57 PASS`": [
                "54",
                "55",
                "56",
                "57",
            ],
        }
        for prose, expected_ids in examples.items():
            with self.subTest(prose=prose):
                parsed = ticketctl.parse_references(prose)
                self.assertEqual(
                    [dependency.ticket_id for dependency in parsed], expected_ids
                )
        conditioned = ticketctl.parse_references(examples.popitem()[0])
        self.assertEqual(conditioned[-1].condition, "PASS")
        self.assertEqual(
            ticketctl.parse_references(
                "Ordinary prose mentions 2024 and $5000.", strict=False
            ),
            [],
        )

        self.fixture.add_ticket("00", depends="broken explicit reference `99-x`")
        payload, _ = self.run_cli(*self.common_args("validate"), expected=2)
        errors = payload["details"]["errors"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Depends On", errors[0])
        self.assertNotIn("missing Core dependency", errors[0])

    def test_unblocks_is_navigation_only(self) -> None:
        self.fixture.add_ticket("00", unblocks="Later work may include `99`")
        payload, _ = self.run_cli(*self.common_args("validate"))
        self.assertTrue(payload["valid"])

    def test_validate_rejects_missing_self_cycle_duplicate_and_malformed(self) -> None:
        cases = {
            "missing": lambda fixture: fixture.add_ticket("00", depends="`99`"),
            "self": lambda fixture: fixture.add_ticket("00", depends="`00`"),
            "cycle": self._make_cycle,
            "duplicate": self._make_duplicate,
            "duplicate_dependency": self._make_duplicate_dependency,
            "malformed": self._make_malformed,
        }
        for name, builder in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                builder(fixture)
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "validate",
                        "--feature-root",
                        str(fixture.feature),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(
                    json.loads(completed.stdout)["error"], "VALIDATION_ERROR"
                )

    @staticmethod
    def _make_cycle(fixture: TrackerFixture) -> None:
        fixture.add_ticket("00", depends="`01`")
        fixture.add_ticket("01", depends="`00`")

    @staticmethod
    def _make_duplicate(fixture: TrackerFixture) -> None:
        fixture.add_ticket("00")
        fixture.add_ticket("00", suffix="duplicate")

    @staticmethod
    def _make_duplicate_dependency(fixture: TrackerFixture) -> None:
        fixture.add_ticket("00")
        fixture.add_ticket("01", depends="`00`, `00`")

    @staticmethod
    def _make_malformed(fixture: TrackerFixture) -> None:
        (fixture.issues / "00-bad.md").write_text("# 00 Bad\n", encoding="utf-8")

    def test_frontier_exact_sequences(self) -> None:
        self.fixture.add_standard_tickets()
        tracker = ticketctl.load_tracker(self.fixture.feature)
        self.assertEqual(ticketctl.frontier(tracker), ["00", "01"])

        self.fixture.set_status("00", "Done")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)),
            ["01", "13"],
        )

        self.fixture.set_status("00", "Open")
        self.fixture.set_status("01", "Done")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)),
            ["00", "02", "03", "04"],
        )

        self.fixture.set_status("00", "Done")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)),
            ["02", "03", "04", "05", "13"],
        )

    def test_claim_requires_frontier_and_preserves_unknown_markdown(self) -> None:
        self.fixture.add_ticket("00")
        blocked_path = self.fixture.add_ticket("01", depends="`00`")
        original_blocked = blocked_path.read_text(encoding="utf-8")
        payload, _ = self.run_cli(
            *self.common_args("claim"), "--ticket", "01", expected=2
        )
        self.assertEqual(payload["error"], "NOT_FRONTIER")
        self.assertEqual(blocked_path.read_text(encoding="utf-8"), original_blocked)

        path = self.fixture.issues / "00-ticket.md"
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        updated = path.read_text(encoding="utf-8")
        self.assertIn("**Status:** In Progress", updated)
        self.assertIn("Preserve extension 00.", updated)
        self.assertIn("Preserve comment 00.", updated)
        duplicate, _ = self.run_cli(
            *self.common_args("claim"), "--ticket", "00", expected=2
        )
        self.assertEqual(duplicate["error"], "INVALID_TRANSITION")

    def test_claim_rejects_untracked_tracker_even_when_source_is_committed(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        self.initialize_git(self.fixture)
        self.git(self.root, "add", "source.txt", "check_pass.py", "check_fail.py")
        self.git(self.root, "commit", "--quiet", "-m", "source baseline")

        payload, _ = self.run_cli(
            *self.common_args("claim"), "--ticket", "00", expected=2
        )
        self.assertEqual(payload["error"], "TRACKER_BASELINE_MISSING")
        untracked = payload["details"]["untracked_files"]
        self.assertIn(".scratch/demo/spec.md", untracked)
        self.assertIn(".scratch/demo/issues/00-ticket.md", untracked)
        self.assertIn(
            "**Status:** Open", (self.fixture.issues / "00-ticket.md").read_text()
        )

    def test_committed_tracker_baseline_allows_claim(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_roadmap()
        self.initialize_git(self.fixture)
        self.git(self.root, "add", ".")
        self.git(self.root, "commit", "--quiet", "-m", "tracker baseline")

        payload, _ = self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.assertEqual(payload["tracker_baseline"]["status"], "PASS")
        self.assertIn(
            "**Status:** In Progress",
            (self.fixture.issues / "00-ticket.md").read_text(),
        )

    def test_plan_check_requires_explicit_complete_coverage(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_plan("00")
        valid, _ = self.run_cli(*self.common_args("plan-check"), "--ticket", "00")
        self.assertEqual(valid["check_ids"], ["AC-1", "INV-1"])

        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        plan = ticketctl._load_plan(ticket)
        plan["checks"] = plan["checks"][:1]
        body = f"```json\n{json.dumps(plan)}\n```"
        ticket.path.write_text(
            ticketctl._replace_or_insert_section(
                ticket.text, "Verification Plan", body
            ),
            encoding="utf-8",
        )
        invalid, _ = self.run_cli(
            *self.common_args("plan-check"), "--ticket", "00", expected=2
        )
        self.assertEqual(invalid["error"], "PLAN_INVALID")
        self.assertIn("INV-1", invalid["details"]["errors"][0])

    def test_plan_requires_delivery_evidence_and_external_gate_coverage(self) -> None:
        self.fixture.add_ticket(
            "00",
            external="- Independent device verification must PASS.",
            evidence="- Record the local artifact checksum.",
        )
        plan = self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EVD-1",
                    "source": "Evidence",
                    "requirement": "Record the local artifact checksum.",
                    "kind": "file",
                    "path": "source.txt",
                    "expected": "path exists and hash is recorded",
                    "blocking": True,
                    "covers": ["EVD-1"],
                },
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent device verification must PASS.",
                    "kind": "manual-review",
                    "expected": "independent reviewer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                },
            ],
        )
        valid, _ = self.run_cli(*self.common_args("plan-check"), "--ticket", "00")
        self.assertEqual(
            set(valid["requirements"]), {"DEL-1", "AC-1", "INV-1", "EVD-1", "EXT-1"}
        )

        for missing_id in ("DEL-1", "EVD-1", "EXT-1"):
            with self.subTest(missing=missing_id):
                changed = json.loads(json.dumps(plan))
                for check in changed["checks"]:
                    check["covers"] = [
                        item for item in check["covers"] if item != missing_id
                    ]
                self.fixture.write_plan("00", changed)
                invalid, _ = self.run_cli(
                    *self.common_args("plan-check"), "--ticket", "00", expected=2
                )
                self.assertIn(missing_id, " ".join(invalid["details"]["errors"]))

        impersonated = json.loads(json.dumps(plan))
        external_check = next(
            check for check in impersonated["checks"] if check["id"] == "EXT-1"
        )
        external_check["kind"] = "assertion"
        external_check["evidence"] = {
            "result": "PASS",
            "reference": "self-authored note",
        }
        self.fixture.write_plan("00", impersonated)
        invalid, _ = self.run_cli(
            *self.common_args("plan-check"), "--ticket", "00", expected=2
        )
        self.assertIn(
            "not external/manual-review", " ".join(invalid["details"]["errors"])
        )

    def test_ext_only_checkpoint_warns_but_does_not_block_core_completion(self) -> None:
        path = self.fixture.add_ticket(
            "00", external="- Consumer device fixture verification."
        )
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Consumer device fixture verification.",
                    "kind": "manual-review",
                    "expected": "consumer reports PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("plan-check"), "--ticket", "00")
        verified, _ = self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.assertEqual(verified["result"], "PASS")
        self.assertEqual(verified["warnings"], ["EXT-1"])
        self.assertEqual(verified["checks"][-1]["result"], "BLOCKED")

        completed, _ = self.run_cli(*self.common_args("complete"), "--ticket", "00")
        self.assertEqual(completed["status"], "Done")
        self.assertEqual(completed["pending_external_checkpoints"], ["EXT-1"])
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(
            status["tickets"]["00"]["pending_external_checkpoints"], ["EXT-1"]
        )
        self.assertIn("**Status:** Done", path.read_text(encoding="utf-8"))

    def test_external_core_gates_and_cross_covered_acceptance_must_block(self) -> None:
        cases = ("declared", "acceptance")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket(
                    "00",
                    external="- Independent release review.",
                    core_gates="EXT-1" if case == "declared" else None,
                )
                covers = ["EXT-1"] if case == "declared" else ["EXT-1", "AC-1"]
                fixture.add_plan(
                    "00",
                    extra_checks=[
                        {
                            "id": "EXT-1",
                            "source": "External Integration Evidence",
                            "requirement": "Independent release review.",
                            "kind": "manual-review",
                            "expected": "reviewer PASS",
                            "blocking": False,
                            "covers": covers,
                        }
                    ],
                )
                invalid, _ = self.run_fixture_cli(
                    fixture,
                    "plan-check",
                    "--feature-root",
                    str(fixture.feature),
                    "--ticket",
                    "00",
                    expected=2,
                )
                self.assertEqual(invalid["error"], "PLAN_INVALID")
                self.assertIn("blocking", " ".join(invalid["details"]["errors"]))

    def test_nonblocking_acceptance_or_invariant_cannot_bypass_completion(self) -> None:
        for check_id in ("AC-1", "INV-1"):
            with (
                self.subTest(check_id=check_id),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                self.run_fixture_cli(
                    fixture,
                    "claim",
                    "--feature-root",
                    str(fixture.feature),
                    "--ticket",
                    "00",
                )
                plan = fixture.add_plan("00", command_exit=7)
                target = next(
                    check for check in plan["checks"] if check["id"] == check_id
                )
                target["blocking"] = False
                fixture.write_plan("00", plan)

                for command in ("plan-check", "verify", "complete"):
                    rejected, _ = self.run_fixture_cli(
                        fixture,
                        command,
                        "--feature-root",
                        str(fixture.feature),
                        "--ticket",
                        "00",
                        expected=2,
                    )
                    self.assertEqual(rejected["error"], "PLAN_INVALID")
                self.assertIn(
                    "**Status:** In Progress",
                    (fixture.issues / "00-ticket.md").read_text(encoding="utf-8"),
                )

    def test_failed_verification_is_remediate_and_cannot_complete(self) -> None:
        path = self.fixture.add_ticket("00")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00", command_exit=7)
        result, _ = self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.assertEqual(result["result"], "REMEDIATE")
        self.assertIn("AC-1", result["failed_checks"])
        self.assertIn("**Status:** In Progress", path.read_text(encoding="utf-8"))

        rejected, _ = self.run_cli(
            *self.common_args("complete"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
        self.assertNotIn("**Status:** Done", path.read_text(encoding="utf-8"))

    def test_stale_source_revision_cannot_complete(self) -> None:
        self.fixture.add_ticket("00")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        (self.root / "source.txt").write_text(
            "changed after verification\n", encoding="utf-8"
        )
        rejected, _ = self.run_cli(
            *self.common_args("complete"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
        self.assertIn("stale", " ".join(rejected["details"]["errors"]))

    def test_spec_contract_plan_and_artifact_changes_reject_completion(self) -> None:
        cases = ("spec", "contract", "plan", "artifact")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                extra_checks: list[dict[str, object]] = []
                roadmap: Path | None = None
                if case == "artifact":
                    roadmap = fixture.add_roadmap()
                    extra_checks.append(
                        {
                            "id": "ART-1",
                            "source": "Delivery",
                            "requirement": "Deliver ticket 00.",
                            "kind": "artifact",
                            "path": ".scratch/demo/roadmap.md",
                            "expected": "path exists and hash is recorded",
                            "blocking": True,
                            "covers": ["DEL-1"],
                        }
                    )
                ticket_args = [
                    "--feature-root",
                    str(fixture.feature),
                    "--ticket",
                    "00",
                ]
                self.run_fixture_cli(fixture, "claim", *ticket_args)
                plan = fixture.add_plan("00", extra_checks=extra_checks)
                self.run_fixture_cli(fixture, "verify", *ticket_args)

                expected_message = ""
                if case == "spec":
                    (fixture.feature / "spec.md").write_text(
                        "# Changed Canonical Spec\n", encoding="utf-8"
                    )
                    expected_message = "immutable_contract_hash"
                elif case == "contract":
                    fixture.set_section("00", "Delivery", "Changed delivery contract.")
                    expected_message = "immutable_contract_hash"
                elif case == "plan":
                    plan["checks"][0]["timeout_seconds"] = 30
                    fixture.write_plan("00", plan)
                    expected_message = "Verification Plan"
                else:
                    assert roadmap is not None
                    roadmap.write_text(
                        roadmap.read_text(encoding="utf-8").replace(
                            "Never rewrite this content.", "Changed after verification."
                        ),
                        encoding="utf-8",
                    )
                    expected_message = "artifact hash is stale"

                rejected, _ = self.run_fixture_cli(
                    fixture, "complete", *ticket_args, expected=2
                )
                self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
                self.assertIn(expected_message, " ".join(rejected["details"]["errors"]))

    def test_pass_dependency_requires_current_valid_result_and_rechecks_on_complete(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00 PASS`")
        self.fixture.set_status("00", "Done")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)), []
        )

        self.fixture.set_status("00", "Open")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        prerequisite_plan = self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)), ["01"]
        )

        (self.root / "source.txt").write_text("stale source\n", encoding="utf-8")
        self.assertEqual(
            ticketctl.frontier(ticketctl.load_tracker(self.fixture.feature)), []
        )
        (self.root / "source.txt").write_text("source\n", encoding="utf-8")

        self.run_cli(*self.common_args("claim"), "--ticket", "01")
        self.fixture.add_plan("01")
        self.run_cli(*self.common_args("verify"), "--ticket", "01")
        prerequisite_plan["checks"][0]["timeout_seconds"] = 30
        self.fixture.write_plan("00", prerequisite_plan)
        rejected, _ = self.run_cli(
            *self.common_args("complete"), "--ticket", "01", expected=2
        )
        self.assertIn(
            "Core dependency 00 is unsatisfied",
            " ".join(rejected["details"]["errors"]),
        )

    def test_historical_completion_survives_source_change_but_pass_gate_does_not(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00`")
        self.fixture.add_ticket("02", depends="`00 PASS`")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")

        (self.root / "source.txt").write_text(
            "normal source change for later tickets\n", encoding="utf-8"
        )
        status, _ = self.run_cli(*self.common_args("status"))
        ticket_zero_drift = [
            item for item in status["state_drift"] if item.get("ticket") == "00"
        ]
        self.assertEqual(ticket_zero_drift, [])
        self.assertEqual(status["frontier"], ["01"])
        self.run_cli(*self.common_args("claim"), "--ticket", "01")

    def test_legacy_done_reconcile_requires_full_verification_flow(self) -> None:
        path = self.fixture.add_ticket("00", status="Done")
        self.fixture.add_ticket("01", depends="`00`")
        original = ticketctl.load_tracker(self.fixture.feature).tickets["00"]

        status, _ = self.run_cli(*self.common_args("status"))
        legacy = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(legacy["severity"], "migratable")
        self.assertIn("reconcile", legacy["repair"])
        self.assertIn("--reason <operator-reason>", legacy["repair"])
        self.assertEqual(status["frontier"], ["01"])

        reconciled, _ = self.run_cli(*self.common_args("reconcile"), "--ticket", "00")
        self.assertEqual(reconciled["from"], "Done")
        self.assertEqual(reconciled["to"], "In Progress")
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Reconciliation Record", text)
        self.assertIn("**Status:** In Progress", text)
        current = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(
            current.metadata["Depends On"], original.metadata["Depends On"]
        )
        self.assertEqual(current.sections["Delivery"], original.sections["Delivery"])

        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        repaired, _ = self.run_cli(*self.common_args("status"))
        self.assertFalse(
            [item for item in repaired["state_drift"] if item.get("ticket") == "00"]
        )
        rejected, _ = self.run_cli(
            *self.common_args("reconcile"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "RECONCILIATION_NOT_REQUIRED")

    def test_reconcile_records_operator_reason_and_returns_recovery_handoff(
        self,
    ) -> None:
        path = self.fixture.add_ticket("00", status="Done")
        reason = "Rebuild legacy completion evidence after audit 17."

        reconciled, _ = self.run_cli(
            *self.common_args("reconcile"),
            "--ticket",
            "00",
            "--reason",
            reason,
        )

        self.assertEqual(
            reconciled["next"],
            "Create or repair the Verification Plan, then run verify and complete.",
        )
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        record = ticketctl._json_blocks(ticket.sections["Reconciliation Record"])[-1]
        self.assertEqual(record["reason"], reason)
        self.assertEqual(ticket.status, "In Progress")
        self.assertNotIn("Verification Plan", ticket.sections)
        self.assertNotIn("Verification Result", ticket.sections)
        self.assertIn("**Status:** In Progress", path.read_text(encoding="utf-8"))

    def test_legacy_prose_plan_and_empty_result_remain_migratable(self) -> None:
        self.fixture.add_ticket("00", status="Done")
        self.fixture.add_ticket("01", depends="`00`")
        self.fixture.add_ticket("02", depends="`00 PASS`")
        self.fixture.set_section(
            "00",
            "Verification Plan",
            "- Run the legacy test suite.\n- Record the clean checkout commit.",
        )
        self.fixture.set_section("00", "Verification Result", "")

        status, _ = self.run_cli(*self.common_args("status"))
        legacy = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(legacy["severity"], "migratable")
        self.assertIn("reconcile", legacy["repair"])
        self.assertEqual(
            status["tickets"]["00"]["completion_integrity"], "LEGACY_MIGRATION_REQUIRED"
        )
        self.assertEqual(status["frontier"], ["01"])

    def test_structured_result_without_matching_plan_is_fatal(self) -> None:
        self.fixture.add_ticket("00", status="Done")
        self.fixture.add_ticket("01", depends="`00`")
        self.fixture.set_section(
            "00",
            "Verification Result",
            '```json\n{"version": 1, "ticket": "00", "result": "PASS"}\n```',
        )

        status, _ = self.run_cli(*self.common_args("status"))
        fatal = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(fatal["severity"], "fatal")
        self.assertIn("matching Verification Plan", " ".join(fatal["details"]))
        self.assertEqual(status["frontier"], [])

    def test_malformed_structured_result_is_reported_as_fatal_drift(self) -> None:
        self.fixture.add_ticket("00", status="Done")
        self.fixture.add_ticket("01", depends="`00`")
        self.fixture.add_plan("00")
        self.fixture.set_section(
            "00",
            "Verification Result",
            '```json\n{"version": 1, broken}\n```',
        )

        status, _ = self.run_cli(*self.common_args("status"))
        fatal = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(fatal["severity"], "fatal")
        self.assertIn("invalid JSON fenced block", " ".join(fatal["details"]))
        self.assertEqual(status["frontier"], [])

    def test_missing_external_or_manual_evidence_blocks_and_resume_is_explicit(
        self,
    ) -> None:
        path = self.fixture.add_ticket(
            "00",
            external="Independent device verification must PASS.",
            core_gates="EXT-1",
        )
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent device verification must PASS.",
                    "kind": "manual-review",
                    "expected": "independent reviewer PASS",
                    "blocking": True,
                    "covers": ["EXT-1"],
                }
            ],
        )
        result, _ = self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["blocked_by"], ["EXT-1"])
        self.assertIn("unlocking_condition", result["checks"][-1])
        self.assertIn("**Status:** Blocked", path.read_text(encoding="utf-8"))

        self.run_cli(*self.common_args("resume"), "--ticket", "00")
        self.assertIn("**Status:** In Progress", path.read_text(encoding="utf-8"))
        invalid_resume, _ = self.run_cli(
            *self.common_args("resume"), "--ticket", "00", expected=2
        )
        self.assertEqual(invalid_resume["error"], "INVALID_TRANSITION")

    def test_full_integration_preserves_content_and_updates_frontier_and_roadmap(
        self,
    ) -> None:
        self.fixture.add_standard_tickets()
        roadmap = self.fixture.add_roadmap()
        ticket_path = self.fixture.issues / "00-ticket.md"
        before = ticket_path.read_text(encoding="utf-8")

        validated, _ = self.run_cli(*self.common_args("validate"))
        self.assertTrue(validated["valid"])
        initial, _ = self.run_cli(*self.common_args("frontier"))
        self.assertEqual(initial["frontier"], ["00", "01"])
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("plan-check"), "--ticket", "00")
        verified, _ = self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.assertEqual(verified["result"], "PASS")
        completed, _ = self.run_cli(*self.common_args("complete"), "--ticket", "00")
        self.assertEqual(completed["status"], "Done")
        self.assertEqual(completed["frontier"], ["01", "13"])
        after_frontier, _ = self.run_cli(*self.common_args("frontier"))
        self.assertEqual(after_frontier["frontier"], ["01", "13"])
        self.run_cli(*self.common_args("sync-roadmap"))
        self.run_cli(*self.common_args("validate"))

        after = ticket_path.read_text(encoding="utf-8")
        self.assertIn("Preserve extension 00.", before)
        self.assertIn("Preserve extension 00.", after)
        self.assertIn("Preserve comment 00.", after)
        roadmap_text = roadmap.read_text(encoding="utf-8")
        self.assertIn("- [x] `00` Ticket 00", roadmap_text)
        self.assertIn("- `01`", roadmap_text)
        self.assertIn("- `13`", roadmap_text)
        self.assertIn("Never rewrite this content.", roadmap_text)

    def test_roadmap_absent_malformed_and_repeated_sync(self) -> None:
        self.fixture.add_ticket("00")
        absent, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertFalse(absent["present"])

        malformed = self.fixture.feature / "roadmap.md"
        malformed.write_text("# Roadmap\n\nambiguous\n", encoding="utf-8")
        original = malformed.read_text(encoding="utf-8")
        failure, _ = self.run_cli(*self.common_args("sync-roadmap"), expected=2)
        self.assertEqual(failure["error"], "STATE_DRIFT")
        self.assertEqual(malformed.read_text(encoding="utf-8"), original)

        malformed.unlink()
        roadmap = self.fixture.add_roadmap()
        first, _ = self.run_cli(*self.common_args("sync-roadmap"))
        first_text = roadmap.read_text(encoding="utf-8")
        second, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertEqual(roadmap.read_text(encoding="utf-8"), first_text)
        self.assertFalse(first["changed"])
        self.assertFalse(second["changed"])

    def test_implementation_roadmap_heading_cards_and_marker_only_sync(self) -> None:
        self.fixture.add_standard_tickets()
        roadmap = self.fixture.add_roadmap(
            filename="implementation-roadmap.md", heading_cards=True
        )
        original = roadmap.read_text(encoding="utf-8")
        roadmap.write_text(
            original.replace(
                "<!-- ticketctl:frontier:start -->",
                "Editorial note: preserve this line.\n\n<!-- ticketctl:frontier:start -->",
            ),
            encoding="utf-8",
        )
        self.fixture.set_status("00", "Done")
        self.fixture.set_status("01", "Done")

        payload, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertTrue(payload["changed"])
        updated = roadmap.read_text(encoding="utf-8")
        self.assertIn("#### [x] [`00` Ticket 00]", updated)
        self.assertIn("#### [x] [`01` Ticket 01]", updated)
        self.assertIn("Contract note for 00; preserve exactly.", updated)
        self.assertIn("Editorial note: preserve this line.", updated)
        self.assertIn("Never rewrite this content.", updated)
        self.assertEqual(payload["frontier"], ["02", "03", "04", "05", "13"])

    def test_markerless_roadmap_migrates_without_duplicate_frontier(self) -> None:
        self.fixture.add_standard_tickets()
        roadmap = self.fixture.add_roadmap(
            filename="implementation-roadmap.md",
            heading_cards=True,
            markers=False,
        )
        roadmap.write_text(
            roadmap.read_text(encoding="utf-8").replace(
                "## Product Notes",
                "Operational prose remains here.\n\n## Product Notes",
            ),
            encoding="utf-8",
        )
        self.fixture.set_status("00", "Done")
        self.fixture.set_status("01", "Done")

        first, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertTrue(first["changed"])
        migrated = roadmap.read_text(encoding="utf-8")
        self.assertEqual(migrated.count(ticketctl.FRONTIER_MARKER_START), 1)
        self.assertEqual(migrated.count(ticketctl.FRONTIER_MARKER_END), 1)
        _, _, frontier_body = ticketctl._frontier_region(migrated)
        for ticket_id in ("02", "03", "04", "05", "13"):
            self.assertEqual(frontier_body.count(f"- `{ticket_id}`"), 1)
        self.assertNotIn("- `00`", frontier_body)
        self.assertNotIn("- `01`", frontier_body)
        self.assertIn("Operational prose remains here.", migrated)

        second, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertFalse(second["changed"])
        self.assertEqual(roadmap.read_text(encoding="utf-8"), migrated)

    def test_roadmap_ambiguity_and_explicit_overrides(self) -> None:
        self.fixture.add_ticket("00")
        roadmap = self.fixture.add_roadmap(filename="roadmap.md")
        implementation = self.fixture.add_roadmap(filename="implementation-roadmap.md")

        ambiguous, _ = self.run_cli(*self.common_args("validate"), expected=2)
        self.assertEqual(ambiguous["error"], "ROADMAP_AMBIGUOUS")
        explicit, _ = self.run_cli(
            *self.common_args("validate"), "--roadmap", "roadmap.md"
        )
        self.assertEqual(explicit["roadmap"]["path"], str(roadmap.resolve()))

        (self.fixture.feature / ".ticketctl.json").write_text(
            json.dumps({"roadmap": "implementation-roadmap.md"}), encoding="utf-8"
        )
        configured, _ = self.run_cli(*self.common_args("validate"))
        self.assertEqual(configured["roadmap"]["path"], str(implementation.resolve()))
        cli_wins, _ = self.run_cli(
            *self.common_args("validate"), "--roadmap", "roadmap.md"
        )
        self.assertEqual(cli_wins["roadmap"]["path"], str(roadmap.resolve()))

    def test_config_is_part_of_published_tracker_baseline(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_roadmap()
        self.commit_all(self.fixture)
        config = self.fixture.feature / ".ticketctl.json"
        config.write_text(json.dumps({"roadmap": "roadmap.md"}), encoding="utf-8")

        rejected, _ = self.run_cli(
            *self.common_args("claim"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
        self.assertIn(
            ".scratch/demo/.ticketctl.json", rejected["details"]["untracked_files"]
        )

        self.git(self.root, "add", ".scratch/demo/.ticketctl.json")
        self.git(self.root, "commit", "--quiet", "-m", "publish tracker config")
        claimed, _ = self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.assertIn(
            ".scratch/demo/.ticketctl.json", claimed["tracker_baseline"]["files"]
        )

    def test_unpublished_spec_or_frozen_contract_blocks_claim(self) -> None:
        for case in ("spec", "delivery"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                self.commit_all(fixture)
                if case == "spec":
                    (fixture.feature / "spec.md").write_text(
                        "# Unpublished spec change\n", encoding="utf-8"
                    )
                    detail_key = "unpublished_spec_files"
                else:
                    fixture.set_section(
                        "00", "Delivery", "Unpublished delivery change."
                    )
                    detail_key = "frozen_contract_drift"

                rejected, _ = self.run_fixture_cli(
                    fixture,
                    "claim",
                    "--feature-root",
                    str(fixture.feature),
                    "--ticket",
                    "00",
                    expected=2,
                )
                self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
                self.assertTrue(rejected["details"][detail_key])

    def test_execution_only_ticket_changes_do_not_break_baseline(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01")
        self.commit_all(self.fixture)
        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        changed = ticketctl._replace_metadata(ticket.text, "Status", "Blocked")
        changed = ticketctl._replace_or_insert_section(
            changed, "Verification Plan", "Plan"
        )
        changed = ticketctl._replace_or_insert_section(
            changed, "Verification Result", "Result"
        )
        changed = ticketctl._replace_or_insert_section(
            changed, "Comments", "Runtime operator comment."
        )
        ticket.path.write_text(changed, encoding="utf-8")

        claimed, _ = self.run_cli(*self.common_args("claim"), "--ticket", "01")
        self.assertEqual(claimed["tracker_baseline"]["status"], "PASS")

    def test_verify_rechecks_baseline_before_running_checks(self) -> None:
        path = self.fixture.add_ticket("00")
        marker = self.root / "check-ran.txt"
        (self.root / "check_marker.py").write_text(
            "from pathlib import Path\nPath('check-ran.txt').write_text('ran')\n",
            encoding="utf-8",
        )
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        plan = self.fixture.add_plan("00")
        plan["checks"][0]["command"] = [sys.executable, "check_marker.py"]
        self.fixture.write_plan("00", plan)
        self.fixture.set_section("00", "Delivery", "Unpublished delivery rewrite.")

        rejected, _ = self.run_cli(
            *self.common_args("verify"), "--ticket", "00", expected=2
        )
        self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
        self.assertFalse(marker.exists())
        self.assertNotIn("## Verification Result", path.read_text(encoding="utf-8"))

    def test_verify_and_complete_recheck_unpublished_spec(self) -> None:
        for phase in ("verify", "complete"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                path = fixture.add_ticket("00")
                self.commit_all(fixture)
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                fixture.add_plan("00")
                if phase == "complete":
                    self.run_fixture_cli(fixture, "verify", *args)
                (fixture.feature / "spec.md").write_text(
                    "# Unpublished spec rewrite\n", encoding="utf-8"
                )

                rejected, _ = self.run_fixture_cli(fixture, phase, *args, expected=2)
                self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
                self.assertNotIn("**Status:** Done", path.read_text(encoding="utf-8"))

    def test_complete_rechecks_frozen_contract_after_verify(self) -> None:
        cases = ("Depends On", "Core Acceptance", "Invariants / Out Of Scope")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                path = fixture.add_ticket("00")
                self.commit_all(fixture)
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                fixture.add_plan("00")
                self.run_fixture_cli(fixture, "verify", *args)
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                if case == "Depends On":
                    changed = ticketctl._replace_metadata(
                        ticket.text, "Depends On", "`E00`"
                    )
                    path.write_text(changed, encoding="utf-8")
                else:
                    fixture.set_section("00", case, f"Unpublished {case} rewrite.")

                rejected, _ = self.run_fixture_cli(
                    fixture, "complete", *args, expected=2
                )
                self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
                self.assertNotIn("**Status:** Done", path.read_text(encoding="utf-8"))

    def test_plan_result_comments_and_controller_receipt_are_execution_state(
        self,
    ) -> None:
        self.fixture.add_ticket("00")
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        updated = ticketctl._append_completion_evidence(
            ticket, self.fixture.latest_result("00")
        )
        updated = ticketctl._replace_or_insert_section(
            updated, "Comments", "Execution-only comment."
        )
        ticket.path.write_text(updated, encoding="utf-8")

        baseline = ticketctl.tracker_baseline(
            ticketctl.load_tracker(self.fixture.feature)
        )
        self.assertEqual(baseline["status"], "PASS")

    def test_published_evidence_requirements_are_frozen(self) -> None:
        for phase in ("claim", "verify", "complete"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                path = fixture.add_ticket(
                    "00", evidence="- Preserve the signed artifact manifest."
                )
                self.commit_all(fixture)
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                if phase != "claim":
                    self.run_fixture_cli(fixture, "claim", *args)
                    fixture.add_plan(
                        "00",
                        extra_checks=[
                            {
                                "id": "EVD-1",
                                "source": "Evidence",
                                "requirement": "Preserve the signed artifact manifest.",
                                "kind": "file",
                                "path": "source.txt",
                                "expected": "path exists and hash is recorded",
                                "blocking": True,
                                "covers": ["EVD-1"],
                            }
                        ],
                    )
                    if phase == "complete":
                        self.run_fixture_cli(fixture, "verify", *args)
                fixture.set_section("00", "Evidence", "None")

                rejected, _ = self.run_fixture_cli(fixture, phase, *args, expected=2)
                self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
                self.assertNotIn("**Status:** Done", path.read_text(encoding="utf-8"))

    def test_completion_receipt_does_not_stale_frozen_evidence(self) -> None:
        self.fixture.add_ticket(
            "00", evidence="- Preserve the signed artifact manifest."
        )
        self.commit_all(self.fixture)
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EVD-1",
                    "source": "Evidence",
                    "requirement": "Preserve the signed artifact manifest.",
                    "kind": "file",
                    "path": "source.txt",
                    "expected": "path exists and hash is recorded",
                    "blocking": True,
                    "covers": ["EVD-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")

        tracker = ticketctl.load_tracker(self.fixture.feature)
        self.assertEqual(ticketctl.tracker_baseline(tracker)["status"], "PASS")
        self.assertEqual(
            ticketctl._historical_completion_integrity(tracker, tracker.tickets["00"])[
                "state"
            ],
            "VALID",
        )

        self.fixture.set_section(
            "00", "Evidence", "- Rewritten signed artifact requirement."
        )
        drifted = ticketctl.tracker_baseline(
            ticketctl.load_tracker(self.fixture.feature)
        )
        self.assertTrue(drifted["frozen_contract_drift"])

    def test_tampered_command_exit_code_and_argv_reject_completion(self) -> None:
        cases = ("exit_code", "command")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                fixture.add_plan("00", command_exit=7 if case == "exit_code" else 0)
                self.run_fixture_cli(fixture, "verify", *args)
                result = fixture.latest_result("00")
                command_result = result["checks"][0]
                if case == "exit_code":
                    result["result"] = "PASS"
                    result["failed_checks"] = []
                    command_result["result"] = "PASS"
                else:
                    command_result["evidence"]["command"] = ["different-command"]
                fixture.write_single_result("00", result)

                rejected, _ = self.run_fixture_cli(
                    fixture, "complete", *args, expected=2
                )
                self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
                self.assertIn(case, " ".join(rejected["details"]["errors"]))

    def test_result_summary_lists_are_recomputed_from_checks(self) -> None:
        for field in ("failed_checks", "blocked_by", "warnings"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                fixture.add_plan("00")
                self.run_fixture_cli(fixture, "verify", *args)
                result = fixture.latest_result("00")
                result[field] = ["AC-1"]
                fixture.write_single_result("00", result)

                rejected, _ = self.run_fixture_cli(
                    fixture, "complete", *args, expected=2
                )
                self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
                self.assertIn(field, " ".join(rejected["details"]["errors"]))

    def test_top_level_pass_cannot_hide_blocking_fail_or_blocked(self) -> None:
        for check_result in ("FAIL", "BLOCKED"):
            with (
                self.subTest(check_result=check_result),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                fixture.add_plan("00")
                self.run_fixture_cli(fixture, "verify", *args)
                result = fixture.latest_result("00")
                result["checks"][0]["result"] = check_result
                fixture.write_single_result("00", result)

                rejected, _ = self.run_fixture_cli(
                    fixture, "complete", *args, expected=2
                )
                self.assertEqual(rejected["error"], "COMPLETION_REJECTED")
                self.assertIn("result", " ".join(rejected["details"]["errors"]))

    def test_tampered_done_result_is_fatal_and_blocks_consumer(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00`")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        result = self.fixture.latest_result("00")
        result["checks"][0]["exit_code"] = 7
        self.fixture.write_single_result("00", result)

        status, _ = self.run_cli(*self.common_args("status"))
        fatal = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(fatal["severity"], "fatal")
        self.assertIn("exit_code", " ".join(fatal["details"]))
        self.assertEqual(status["frontier"], [])

    def test_record_external_closes_pending_without_rewriting_core_result(self) -> None:
        path = self.fixture.add_ticket(
            "00", external="- Independent consumer conformance."
        )
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer conformance.",
                    "kind": "manual-review",
                    "expected": "consumer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        before = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        plan_before = before.sections["Verification Plan"]
        result_before = before.sections["Verification Result"]
        receipt_before = ticketctl._completion_receipt_entry(
            self.fixture.latest_result("00")
        )

        recorded, _ = self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        self.assertTrue(recorded["changed"])
        self.assertEqual(recorded["pending_external_checkpoints"], [])
        after = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(after.sections["Verification Plan"], plan_before)
        self.assertEqual(after.sections["Verification Result"], result_before)
        self.assertIn(receipt_before, after.sections["Evidence"])
        self.assertIn("## External Checkpoint Result", path.read_text(encoding="utf-8"))
        self.assertEqual(after.status, "Done")

        repeated, _ = self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        self.assertFalse(repeated["changed"])
        self.assertEqual(
            len(ticketctl._json_blocks(after.sections["External Checkpoint Result"])), 1
        )

    def test_record_external_rejects_ineligible_checks_and_states(self) -> None:
        self.fixture.add_ticket(
            "00",
            external="- Blocking external approval.",
            core_gates="EXT-1",
        )
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Blocking external approval.",
                    "kind": "manual-review",
                    "expected": "review PASS",
                    "blocking": True,
                    "covers": ["EXT-1"],
                    "evidence": {
                        "result": "PASS",
                        "reference": "approval://17",
                        "issuer": "review-board",
                    },
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")

        for check_id in ("EXT-1", "AC-1", "UNKNOWN"):
            with self.subTest(check_id=check_id):
                rejected, _ = self.run_cli(
                    *self.common_args("record-external"),
                    "--ticket",
                    "00",
                    "--check",
                    check_id,
                    "--result",
                    "PASS",
                    "--reference",
                    "approval://later",
                    "--issuer",
                    "consumer-ci",
                    expected=2,
                )
                self.assertIn(
                    rejected["error"],
                    {"CHECKPOINT_NOT_ELIGIBLE", "UNKNOWN_CHECKPOINT"},
                )

        for status in ("Open", "In Progress", "Blocked"):
            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as directory,
            ):
                state_fixture = TrackerFixture(Path(directory))
                state_fixture.add_ticket("00", status=status)
                rejected, _ = self.run_fixture_cli(
                    state_fixture,
                    "record-external",
                    "--feature-root",
                    str(state_fixture.feature),
                    "--ticket",
                    "00",
                    "--check",
                    "EXT-1",
                    "--result",
                    "PASS",
                    "--reference",
                    "approval://later",
                    "--issuer",
                    "consumer-ci",
                    expected=2,
                )
                self.assertEqual(rejected["error"], "INVALID_TRANSITION")

    def test_external_checkpoint_tamper_is_separate_drift(self) -> None:
        self.fixture.add_ticket("00", external="- Independent consumer conformance.")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer conformance.",
                    "kind": "external",
                    "expected": "consumer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        self.fixture.set_section("00", "External Checkpoint Result", "")

        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tickets"]["00"]["completion_integrity"], "VALID")
        self.assertEqual(
            status["tickets"]["00"]["pending_external_checkpoints"], ["EXT-1"]
        )
        external_drift = [
            item
            for item in status["state_drift"]
            if item.get("ticket") == "00"
            and item.get("reason") == "external checkpoint evidence is invalid"
        ]
        self.assertEqual(len(external_drift), 1)

    def test_record_external_rejects_malformed_or_conflicting_evidence(self) -> None:
        self.fixture.add_ticket("00", external="- Independent consumer conformance.")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer conformance.",
                    "kind": "external",
                    "expected": "consumer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")

        for field, value in (("result", "FAIL"), ("reference", " "), ("issuer", " ")):
            with self.subTest(field=field):
                arguments = {
                    "result": "PASS",
                    "reference": "artifact://consumer/report-17",
                    "issuer": "consumer-ci",
                }
                arguments[field] = value
                rejected, _ = self.run_cli(
                    *self.common_args("record-external"),
                    "--ticket",
                    "00",
                    "--check",
                    "EXT-1",
                    "--result",
                    arguments["result"],
                    "--reference",
                    arguments["reference"],
                    "--issuer",
                    arguments["issuer"],
                    expected=2,
                )
                self.assertEqual(rejected["error"], "CHECKPOINT_EVIDENCE_INVALID")

        self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        conflict, _ = self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/different-report",
            "--issuer",
            "consumer-ci",
            expected=2,
        )
        self.assertEqual(conflict["error"], "CHECKPOINT_CONFLICT")

    def test_external_checkpoint_hash_tamper_reopens_only_that_checkpoint(self) -> None:
        self.fixture.add_ticket("00", external="- Independent consumer conformance.")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan(
            "00",
            extra_checks=[
                {
                    "id": "EXT-1",
                    "source": "External Integration Evidence",
                    "requirement": "Independent consumer conformance.",
                    "kind": "manual-review",
                    "expected": "consumer PASS",
                    "blocking": False,
                    "covers": ["EXT-1"],
                }
            ],
        )
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        record = ticketctl._json_blocks(ticket.sections["External Checkpoint Result"])[
            0
        ]
        record["reference"] = "artifact://tampered"
        self.fixture.set_section(
            "00",
            "External Checkpoint Result",
            ticketctl._external_result_body([record]),
        )

        status, _ = self.run_cli(*self.common_args("status"))
        ticket_status = status["tickets"]["00"]
        self.assertEqual(ticket_status["completion_integrity"], "VALID")
        self.assertEqual(ticket_status["external_checkpoint_integrity"], "INVALID")
        self.assertEqual(ticket_status["pending_external_checkpoints"], ["EXT-1"])
        self.assertEqual(status["frontier"], [])

        rejected, _ = self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
            expected=2,
        )
        self.assertEqual(rejected["error"], "EXTERNAL_CHECKPOINT_INVALID")

    def test_external_anchor_damage_isolated_from_core_and_consumer(self) -> None:
        cases = (
            "malformed_hash",
            "missing_anchor",
            "missing_record",
            "malformed_record",
            "duplicate_record",
            "broken_boundary",
            "duplicate_anchor",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                self.complete_pending_external(fixture, consumer=True)
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(
                    fixture,
                    "record-external",
                    *args,
                    "--check",
                    "EXT-1",
                    "--result",
                    "PASS",
                    "--reference",
                    "artifact://consumer/report-17",
                    "--issuer",
                    "consumer-ci",
                )
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                anchor_body = ticket.sections["External Checkpoint Anchor"]
                if case == "malformed_hash":
                    anchor_body = anchor_body.replace("sha256:", "invalid:", 1)
                    fixture.set_section("00", "External Checkpoint Anchor", anchor_body)
                elif case == "missing_anchor":
                    fixture.set_section("00", "External Checkpoint Anchor", "")
                elif case == "missing_record":
                    fixture.set_section("00", "External Checkpoint Result", "")
                elif case == "malformed_record":
                    fixture.set_section(
                        "00", "External Checkpoint Result", "```json\n{"
                    )
                elif case == "duplicate_record":
                    record_body = ticket.sections["External Checkpoint Result"]
                    fixture.set_section(
                        "00",
                        "External Checkpoint Result",
                        f"{record_body}\n\n{record_body}",
                    )
                elif case == "broken_boundary":
                    anchor_body = anchor_body.replace(
                        ticketctl.EXTERNAL_CHECKPOINT_MARKER_END, "", 1
                    )
                    fixture.set_section("00", "External Checkpoint Anchor", anchor_body)
                else:
                    anchor_line = next(
                        line
                        for line in anchor_body.splitlines()
                        if line.startswith("- External checkpoint")
                    )
                    anchor_body = anchor_body.replace(
                        ticketctl.EXTERNAL_CHECKPOINT_MARKER_END,
                        f"{anchor_line}\n{ticketctl.EXTERNAL_CHECKPOINT_MARKER_END}",
                        1,
                    )
                    fixture.set_section("00", "External Checkpoint Anchor", anchor_body)

                status, _ = self.run_fixture_cli(
                    fixture, "status", "--feature-root", str(fixture.feature)
                )
                ticket_status = status["tickets"]["00"]
                self.assertEqual(ticket_status["completion_integrity"], "VALID")
                self.assertEqual(
                    ticket_status["external_checkpoint_integrity"], "INVALID"
                )
                self.assertEqual(
                    ticket_status["pending_external_checkpoints"], ["EXT-1"]
                )
                self.assertEqual(status["frontier"], ["01"])
                self.assertEqual(status["drift_summary"]["fatal"], 0)
                self.assertEqual(status["drift_summary"]["informational"], 1)

    def test_external_damage_has_explicit_record_external_repair(self) -> None:
        self.complete_pending_external(self.fixture)
        args = ["--ticket", "00", "--check", "EXT-1", "--result", "PASS"]
        evidence = [
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        ]
        self.run_cli(*self.common_args("record-external"), *args, *evidence)
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        damaged = ticket.sections["External Checkpoint Anchor"].replace(
            "sha256:", "invalid:", 1
        )
        self.fixture.set_section("00", "External Checkpoint Anchor", damaged)

        rejected, _ = self.run_cli(
            *self.common_args("record-external"),
            *args,
            *evidence,
            expected=2,
        )
        self.assertEqual(rejected["error"], "EXTERNAL_CHECKPOINT_INVALID")
        self.assertIn("--repair", rejected["details"]["repair_command"])

        repaired, _ = self.run_cli(
            *self.common_args("record-external"),
            *args,
            *evidence,
            "--repair",
        )
        self.assertTrue(repaired["changed"])
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(
            status["tickets"]["00"]["external_checkpoint_integrity"], "VALID"
        )
        self.assertEqual(status["tickets"]["00"]["pending_external_checkpoints"], [])

    def test_duplicate_external_sections_are_informational_drift(self) -> None:
        for section in (
            "External Checkpoint Result",
            "External Checkpoint Anchor",
        ):
            with (
                self.subTest(section=section),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                self.complete_pending_external(fixture, consumer=True)
                self.record_external(fixture, "EXT-1")
                self.commit_all(fixture)
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                duplicated = (
                    f"{ticket.text.rstrip()}\n\n"
                    f"## {section}\n\n{ticket.sections[section]}\n"
                )
                ticket.path.write_text(duplicated, encoding="utf-8")

                status, _ = self.run_fixture_cli(
                    fixture, "status", "--feature-root", str(fixture.feature)
                )
                ticket_status = status["tickets"]["00"]
                self.assertEqual(ticket_status["completion_integrity"], "VALID")
                self.assertEqual(
                    ticket_status["external_checkpoint_integrity"], "INVALID"
                )
                self.assertEqual(
                    ticket_status["pending_external_checkpoints"], ["EXT-1"]
                )
                self.assertEqual(status["frontier"], ["01"])
                self.assertEqual(status["tracker_baseline"]["status"], "PASS")
                self.assertEqual(status["drift_summary"]["fatal"], 0)
                self.assertEqual(status["drift_summary"]["informational"], 1)

                repaired = self.record_external(fixture, "EXT-1", repair=True)
                self.assertTrue(repaired["changed"])
                repaired_ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                self.assertEqual(repaired_ticket.text.count(f"## {section}"), 1)
                repaired_status, _ = self.run_fixture_cli(
                    fixture, "status", "--feature-root", str(fixture.feature)
                )
                self.assertEqual(
                    repaired_status["tickets"]["00"]["external_checkpoint_integrity"],
                    "VALID",
                )

    def test_external_records_are_fault_isolated_and_repaired_per_check(self) -> None:
        damage_cases = (
            "malformed_json",
            "invalid_record_hash",
            "invalid_anchor_hash",
            "missing_anchor",
        )
        for damage in damage_cases:
            with (
                self.subTest(damage=damage),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                self.complete_two_pending_externals(fixture)
                self.record_external(fixture, "EXT-1")
                self.record_external(fixture, "EXT-2")
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                original_records = {
                    record["check"]: record
                    for record in ticketctl._json_blocks(
                        ticket.sections["External Checkpoint Result"]
                    )
                }

                if damage == "malformed_json":
                    body = ticket.sections["External Checkpoint Result"]
                    heading = "### EXT-2\n\n```json\n"
                    before, record_and_after = body.split(heading, 1)
                    _, after = record_and_after.split("\n```", 1)
                    damaged = f"{before}{heading}{{not-json}}\n```{after}"
                    fixture.set_section("00", "External Checkpoint Result", damaged)
                elif damage == "invalid_record_hash":
                    records = list(original_records.values())
                    records[1] = dict(records[1])
                    records[1]["record_hash"] = "sha256:" + "0" * 64
                    fixture.set_section(
                        "00",
                        "External Checkpoint Result",
                        ticketctl._external_result_body(records),
                    )
                else:
                    anchor_body = ticket.sections["External Checkpoint Anchor"]
                    anchor_line = next(
                        line
                        for line in anchor_body.splitlines()
                        if line.startswith("- External checkpoint `EXT-2`")
                    )
                    if damage == "invalid_anchor_hash":
                        damaged_line = anchor_line.replace(
                            original_records["EXT-2"]["record_hash"],
                            "sha256:" + "0" * 64,
                            1,
                        )
                        anchor_body = anchor_body.replace(anchor_line, damaged_line, 1)
                    else:
                        anchor_body = anchor_body.replace(f"{anchor_line}\n", "", 1)
                    fixture.set_section("00", "External Checkpoint Anchor", anchor_body)

                status, _ = self.run_fixture_cli(
                    fixture, "status", "--feature-root", str(fixture.feature)
                )
                ticket_status = status["tickets"]["00"]
                self.assertEqual(
                    ticket_status["external_checkpoint_integrity"], "INVALID"
                )
                self.assertEqual(
                    ticket_status["pending_external_checkpoints"], ["EXT-2"]
                )
                self.assertEqual(status["drift_summary"]["fatal"], 0)
                self.assertEqual(status["drift_summary"]["informational"], 1)

                repaired = self.record_external(fixture, "EXT-2", repair=True)
                self.assertTrue(repaired["changed"])
                repaired_ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                repaired_records = {
                    record["check"]: record
                    for record in ticketctl._json_blocks(
                        repaired_ticket.sections["External Checkpoint Result"]
                    )
                }
                self.assertEqual(repaired_records["EXT-1"], original_records["EXT-1"])
                self.assertEqual(set(repaired_records), {"EXT-1", "EXT-2"})
                self.assertEqual(
                    repaired_ticket.text.count("## External Checkpoint Result"), 1
                )
                self.assertEqual(
                    repaired_ticket.text.count("## External Checkpoint Anchor"), 1
                )
                final_status, _ = self.run_fixture_cli(
                    fixture, "status", "--feature-root", str(fixture.feature)
                )
                self.assertEqual(
                    final_status["tickets"]["00"]["external_checkpoint_integrity"],
                    "VALID",
                )
                self.assertEqual(
                    final_status["tickets"]["00"]["pending_external_checkpoints"],
                    [],
                )

    def test_revision_3_evidence_anchor_remains_compatible_and_isolated(self) -> None:
        self.complete_pending_external(self.fixture)
        self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        record = ticketctl._json_blocks(ticket.sections["External Checkpoint Result"])[
            0
        ]
        record["controller_version"] = 3
        record["record_hash"] = ticketctl._external_record_hash(record)
        text = ticketctl._replace_or_insert_section(
            ticket.text,
            "External Checkpoint Result",
            ticketctl._external_result_body([record]),
        )
        evidence = (
            f"{ticket.sections['Evidence'].rstrip()}\n\n"
            f"{ticketctl._external_anchor_body([record])}"
        )
        text = ticketctl._replace_or_insert_section(text, "Evidence", evidence)
        matches = list(ticketctl.SECTION_RE.finditer(text))
        anchor_index, anchor_match = next(
            (index, match)
            for index, match in enumerate(matches)
            if match.group(1).strip() == "External Checkpoint Anchor"
        )
        anchor_end = (
            matches[anchor_index + 1].start()
            if anchor_index + 1 < len(matches)
            else len(text)
        )
        ticket.path.write_text(
            text[: anchor_match.start()] + text[anchor_end:], encoding="utf-8"
        )

        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tickets"]["00"]["completion_integrity"], "VALID")
        self.assertEqual(
            status["tickets"]["00"]["external_checkpoint_integrity"], "VALID"
        )

        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.fixture.set_section(
            "00",
            "Evidence",
            ticket.sections["Evidence"].replace(
                record["record_hash"], "invalid:record-hash", 1
            ),
        )
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tickets"]["00"]["completion_integrity"], "VALID")
        self.assertEqual(
            status["tickets"]["00"]["external_checkpoint_integrity"], "INVALID"
        )

    def test_completion_receipt_damage_remains_core_fatal(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00`")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        damaged = ticket.sections["Evidence"].replace(
            "- Verification PASS", "- Damaged Verification PASS", 1
        )
        self.fixture.set_section("00", "Evidence", damaged)

        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tickets"]["00"]["completion_integrity"], "INVALID")
        self.assertEqual(status["drift_summary"]["fatal"], 1)
        self.assertEqual(status["frontier"], [])

    def test_execution_markers_cannot_hide_frozen_evidence_requirements(self) -> None:
        self.fixture.add_ticket(
            "00", evidence="- Preserve the signed artifact manifest."
        )
        self.commit_all(self.fixture)
        wrapped = (
            f"{ticketctl.EXTERNAL_CHECKPOINT_MARKER_START}\n"
            "- Preserve the signed artifact manifest.\n"
            f"{ticketctl.EXTERNAL_CHECKPOINT_MARKER_END}"
        )
        self.fixture.set_section("00", "Evidence", wrapped)
        tracker = ticketctl.load_tracker(self.fixture.feature)
        self.assertEqual(
            ticketctl._requirement_units(tracker.tickets["00"])["EVD-1"],
            "Preserve the signed artifact manifest.",
        )
        self.assertEqual(ticketctl.tracker_baseline(tracker)["status"], "PASS")

        hidden = (
            f"{ticketctl.EXTERNAL_CHECKPOINT_MARKER_START}\n"
            "None\n"
            f"{ticketctl.EXTERNAL_CHECKPOINT_MARKER_END}"
        )
        self.fixture.set_section("00", "Evidence", hidden)
        drifted = ticketctl.tracker_baseline(
            ticketctl.load_tracker(self.fixture.feature)
        )
        self.assertTrue(drifted["frozen_contract_drift"])

    def test_controller_like_evidence_prose_remains_frozen_and_covered(self) -> None:
        requirements = (
            "The reviewer must preserve Verification PASS evidence.",
            "External checkpoint `EXT-1` must be retained in the handoff.",
        )
        markers = (
            (None, None),
            (
                ticketctl.COMPLETION_RECEIPT_MARKER_START,
                ticketctl.COMPLETION_RECEIPT_MARKER_END,
            ),
            (
                ticketctl.EXTERNAL_CHECKPOINT_MARKER_START,
                ticketctl.EXTERNAL_CHECKPOINT_MARKER_END,
            ),
        )
        for requirement in requirements:
            for marker_start, marker_end in markers:
                with (
                    self.subTest(requirement=requirement, marker_start=marker_start),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    fixture = TrackerFixture(Path(directory))
                    fixture.add_ticket("00", evidence=f"- {requirement}")
                    self.commit_all(fixture)
                    if marker_start is not None and marker_end is not None:
                        fixture.set_section(
                            "00",
                            "Evidence",
                            f"{marker_start}\n- {requirement}\n{marker_end}",
                        )
                    tracker = ticketctl.load_tracker(fixture.feature)
                    ticket = tracker.tickets["00"]
                    self.assertEqual(
                        ticketctl._requirement_units(ticket)["EVD-1"], requirement
                    )
                    self.assertEqual(
                        ticketctl.tracker_baseline(tracker)["status"], "PASS"
                    )

                    fixture.add_plan("00")
                    rejected, _ = self.run_fixture_cli(
                        fixture,
                        "plan-check",
                        "--feature-root",
                        str(fixture.feature),
                        "--ticket",
                        "00",
                        expected=2,
                    )
                    self.assertEqual(rejected["error"], "PLAN_INVALID")
                    self.assertIn("EVD-1", " ".join(rejected["details"]["errors"]))

    def test_core_and_external_drift_are_reported_independently(self) -> None:
        self.complete_pending_external(self.fixture, consumer=True)
        record_args = [
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        ]
        self.run_cli(*record_args)
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.fixture.set_section(
            "00",
            "Evidence",
            ticket.sections["Evidence"].replace(
                "- Verification PASS", "- Damaged Verification PASS", 1
            ),
        )
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.fixture.set_section(
            "00",
            "External Checkpoint Anchor",
            ticket.sections["External Checkpoint Anchor"].replace(
                "sha256:", "invalid:", 1
            ),
        )

        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["drift_summary"]["fatal"], 1)
        self.assertEqual(status["drift_summary"]["informational"], 1)
        reasons = [
            item["reason"]
            for item in status["state_drift"]
            if item.get("ticket") == "00"
        ]
        self.assertEqual(
            reasons,
            [
                "Done ticket has invalid historical completion integrity",
                "external checkpoint evidence is invalid",
            ],
        )
        self.assertEqual(status["frontier"], [])

    def test_external_only_zero_pass_completion_and_legacy_receipt(self) -> None:
        self.complete_pending_external(self.fixture, external_only=True)
        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        self.assertIn("for checks: None.", ticket.sections["Evidence"])
        self.assertEqual(
            ticketctl._historical_completion_integrity(tracker, ticket)["state"],
            "VALID",
        )
        self.run_cli(
            *self.common_args("record-external"),
            "--ticket",
            "00",
            "--check",
            "EXT-1",
            "--result",
            "PASS",
            "--reference",
            "artifact://consumer/report-17",
            "--issuer",
            "consumer-ci",
        )
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["tickets"]["00"]["pending_external_checkpoints"], [])

        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        legacy = (
            ticket.sections["Evidence"]
            .replace(f"{ticketctl.COMPLETION_RECEIPT_MARKER_START}\n", "", 1)
            .replace(f"\n{ticketctl.COMPLETION_RECEIPT_MARKER_END}", "", 1)
        )
        self.fixture.set_section("00", "Evidence", legacy)
        tracker = ticketctl.load_tracker(self.fixture.feature)
        self.assertEqual(
            ticketctl._historical_completion_integrity(tracker, tracker.tickets["00"])[
                "state"
            ],
            "VALID",
        )

    def test_complete_validates_generated_receipt_before_mutation(self) -> None:
        self.fixture.add_ticket("00")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        path = self.fixture.issues / "00-ticket.md"
        before = path.read_bytes()
        args = argparse.Namespace(
            feature_root=str(self.fixture.feature), roadmap=None, ticket="00"
        )
        with (
            mock.patch.object(
                ticketctl,
                "_completion_receipt_entry",
                return_value="- invalid generated receipt",
            ),
            self.assertRaises(ticketctl.TicketCtlError),
        ):
            ticketctl.command_complete(args)
        self.assertEqual(path.read_bytes(), before)

    def test_verify_preserves_concurrent_comments(self) -> None:
        self.fixture.add_ticket("00")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.add_pausing_check(self.fixture)
        plan = self.fixture.add_plan("00")
        plan["checks"][0]["command"] = [sys.executable, "check_pause.py"]
        self.fixture.write_plan("00", plan)
        process = self.start_fixture_cli(
            self.fixture,
            *self.common_args("verify"),
            "--ticket",
            "00",
        )
        time.sleep(0.25)
        self.fixture.set_section("00", "Comments", "Concurrent user comment.")
        payload, _ = self.finish_fixture_cli(process)
        self.assertEqual(payload["result"], "PASS")
        ticket = ticketctl.load_tracker(self.fixture.feature).tickets["00"]
        self.assertEqual(ticket.sections["Comments"], "Concurrent user comment.")
        self.assertTrue(ticketctl._load_results(ticket))

    def test_verify_rejects_source_changes_during_check_execution(self) -> None:
        for mutation in ("modify", "add", "delete", "mode"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                self.add_pausing_check(fixture)
                plan = fixture.add_plan("00")
                plan["checks"][0]["command"] = [sys.executable, "check_pause.py"]
                fixture.write_plan("00", plan)
                process = self.start_fixture_cli(fixture, "verify", *args)
                time.sleep(0.25)

                source = fixture.root / "source.txt"
                if mutation == "modify":
                    source.write_text("concurrent rewrite\n", encoding="utf-8")
                elif mutation == "add":
                    (fixture.root / "concurrent.txt").write_text(
                        "concurrent addition\n", encoding="utf-8"
                    )
                elif mutation == "delete":
                    source.unlink()
                else:
                    source.chmod(0o600)
                rejected, _ = self.finish_fixture_cli(process, expected=2)
                self.assertEqual(rejected["error"], "CONCURRENT_MODIFICATION")
                self.assertIn("source_revision_before", rejected["details"])
                self.assertIn("source_revision_after", rejected["details"])
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                self.assertNotIn("Verification Result", ticket.sections)

    def test_verify_rejects_concurrent_plan_or_result_changes(self) -> None:
        for case in ("plan", "result"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                self.add_pausing_check(fixture)
                plan = fixture.add_plan("00")
                plan["checks"][0]["command"] = [sys.executable, "check_pause.py"]
                fixture.write_plan("00", plan)
                process = self.start_fixture_cli(fixture, "verify", *args)
                time.sleep(0.25)
                if case == "plan":
                    plan["checks"][0]["timeout_seconds"] = 30
                    fixture.write_plan("00", plan)
                else:
                    fixture.set_section(
                        "00",
                        "Verification Result",
                        '### Concurrent Attempt\n\n```json\n{"attempt": 99}\n```',
                    )
                rejected, _ = self.finish_fixture_cli(process, expected=2)
                self.assertEqual(rejected["error"], "CONCURRENT_MODIFICATION")
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                if case == "plan":
                    self.assertNotIn("Verification Result", ticket.sections)
                else:
                    self.assertIn(
                        "Concurrent Attempt", ticket.sections["Verification Result"]
                    )
                    self.assertEqual(len(ticketctl._load_results(ticket)), 1)

    def test_verify_rechecks_concurrent_frozen_tracker_changes(self) -> None:
        for case in ("Delivery", "spec"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                fixture = TrackerFixture(Path(directory))
                fixture.add_ticket("00")
                self.add_pausing_check(fixture)
                self.commit_all(fixture)
                args = ["--feature-root", str(fixture.feature), "--ticket", "00"]
                self.run_fixture_cli(fixture, "claim", *args)
                plan = fixture.add_plan("00")
                plan["checks"][0]["command"] = [sys.executable, "check_pause.py"]
                fixture.write_plan("00", plan)
                process = self.start_fixture_cli(fixture, "verify", *args)
                time.sleep(0.25)
                if case == "Delivery":
                    fixture.set_section("00", "Delivery", "Concurrent rewrite.")
                else:
                    (fixture.feature / "spec.md").write_text(
                        "# Concurrent spec rewrite\n", encoding="utf-8"
                    )
                rejected, _ = self.finish_fixture_cli(process, expected=2)
                self.assertEqual(rejected["error"], "TRACKER_BASELINE_MISSING")
                ticket = ticketctl.load_tracker(fixture.feature).tickets["00"]
                self.assertNotIn("Verification Result", ticket.sections)
                if case == "Delivery":
                    self.assertEqual(ticket.sections["Delivery"], "Concurrent rewrite.")

    def test_fatal_dependency_integrity_drift_blocks_consumer_claim(self) -> None:
        self.fixture.add_ticket("00")
        self.fixture.add_ticket("01", depends="`00`")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        plan = self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.run_cli(*self.common_args("complete"), "--ticket", "00")

        plan["checks"][0]["timeout_seconds"] = 30
        self.fixture.write_plan("00", plan)
        status, _ = self.run_cli(*self.common_args("status"))
        fatal = next(
            item for item in status["state_drift"] if item.get("ticket") == "00"
        )
        self.assertEqual(fatal["severity"], "fatal")
        self.assertEqual(status["frontier"], [])
        rejected, _ = self.run_cli(
            *self.common_args("claim"), "--ticket", "01", expected=2
        )
        self.assertEqual(rejected["error"], "NOT_FRONTIER")
        self.assertIn("00", rejected["details"]["unsatisfied_dependencies"])

    def test_unsafe_command_bypasses_are_rejected_and_verify_blocks(self) -> None:
        unsafe_commands = (
            ["bash", "-c", "echo bypass"],
            ["sh", "-c", "echo bypass"],
            [sys.executable, "-c", "print('bypass')"],
            ["node", "-e", "console.log('bypass')"],
            ["env", sys.executable, "check_pass.py"],
            ["git", "-c", "core.pager=cat", "status"],
            ["npm", "publish"],
            ["find", ".", "-delete"],
            ["make", "deploy-prod"],
        )
        for command in unsafe_commands:
            with (
                self.subTest(command=command),
                self.assertRaises(ticketctl.TicketCtlError),
            ):
                ticketctl._command_argv(command)

        path = self.fixture.add_ticket("00")
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        plan = self.fixture.add_plan("00")
        plan["checks"][0]["command"] = ["env", sys.executable, "check_pass.py"]
        self.fixture.write_plan("00", plan)
        result, _ = self.run_cli(*self.common_args("verify"), "--ticket", "00")
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["blocked_by"], ["AC-1"])
        self.assertIn("**Status:** Blocked", path.read_text(encoding="utf-8"))

    def test_isolated_dex_shaped_conformance_frontier(self) -> None:
        self.fixture.add_ticket("00", status="Done", unblocks="`05`、`13`")
        self.fixture.add_ticket("01", status="Done", unblocks="`02-05`")
        self.fixture.add_ticket("02", depends="完成 bootstrap 后由 `01` 解锁")
        self.fixture.add_ticket("03", depends="`01`，并保持同一实现波次")
        self.fixture.add_ticket("04", depends="timestamp-mode prerequisite `01`")
        self.fixture.add_ticket("05", depends="`00`、`01`，共同提供门禁")
        self.fixture.add_ticket("13", depends="`00`")
        self.fixture.add_roadmap(
            frontier=["02", "03", "04", "05", "13"],
            filename="implementation-roadmap.md",
            heading_cards=True,
        )

        validated, _ = self.run_cli(*self.common_args("validate"))
        status, _ = self.run_cli(*self.common_args("status"))
        ready, _ = self.run_cli(*self.common_args("frontier"))
        self.assertTrue(validated["valid"])
        self.assertEqual(status["frontier"], ["02", "03", "04", "05", "13"])
        self.assertEqual(ready["frontier"], ["02", "03", "04", "05", "13"])

    def test_open_evidence_and_stale_roadmap_report_state_drift_then_repair(
        self,
    ) -> None:
        self.fixture.add_standard_tickets()
        tracker = ticketctl.load_tracker(self.fixture.feature)
        ticket = tracker.tickets["00"]
        ticket.path.write_text(
            ticketctl._replace_or_insert_section(
                ticket.text,
                "Delivery",
                "The scaffold already exists at `source.txt`.",
            ),
            encoding="utf-8",
        )
        roadmap = self.fixture.add_roadmap()
        status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(status["state"], "STATE_DRIFT")
        ticket_drift = [
            item for item in status["state_drift"] if item.get("ticket") == "00"
        ]
        self.assertTrue(ticket_drift)
        self.assertIn("existing declared path source.txt", ticket_drift[0]["evidence"])
        self.assertIn(
            "**Status:** Open", (self.fixture.issues / "00-ticket.md").read_text()
        )

        self.fixture.set_status("00", "Done")
        self.fixture.set_status("01", "Done")
        stale_status, _ = self.run_cli(*self.common_args("status"))
        self.assertEqual(stale_status["state"], "STATE_DRIFT")
        synced, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertTrue(synced["changed"])
        self.assertTrue(synced["state_drift"])
        self.assertEqual(synced["frontier"], ["02", "03", "04", "05", "13"])
        repaired, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertFalse(repaired["changed"])
        self.assertIn("- [x] `00`", roadmap.read_text(encoding="utf-8"))

    def test_atomic_write_failure_keeps_original_and_removes_temp_file(self) -> None:
        path = self.fixture.add_ticket("00")
        original = path.read_text(encoding="utf-8")
        with (
            mock.patch.object(
                ticketctl.os, "replace", side_effect=OSError("simulated")
            ),
            self.assertRaises(OSError),
        ):
            ticketctl._atomic_write(path, "replacement")
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_complete_reports_recoverable_drift_after_roadmap_write_failure(
        self,
    ) -> None:
        path = self.fixture.add_ticket("00")
        roadmap = self.fixture.add_roadmap()
        self.run_cli(*self.common_args("claim"), "--ticket", "00")
        self.fixture.add_plan("00")
        self.run_cli(*self.common_args("verify"), "--ticket", "00")

        real_atomic_write = ticketctl._atomic_write

        def fail_only_roadmap(target: Path, text: str, **write_options: object) -> None:
            if target.name == "roadmap.md":
                raise OSError("simulated roadmap write failure")
            real_atomic_write(target, text, **write_options)

        args = argparse.Namespace(feature_root=str(self.fixture.feature), ticket="00")
        with (
            mock.patch.object(
                ticketctl, "_atomic_write", side_effect=fail_only_roadmap
            ),
            self.assertRaises(ticketctl.StateDriftFailure) as raised,
        ):
            ticketctl.command_complete(args)
        self.assertEqual(raised.exception.details["ticket_status"], "Done")
        self.assertIn("**Status:** Done", path.read_text(encoding="utf-8"))
        self.assertIn("- [ ] `00`", roadmap.read_text(encoding="utf-8"))

        repaired, _ = self.run_cli(*self.common_args("sync-roadmap"))
        self.assertTrue(repaired["changed"])
        self.assertIn("- [x] `00`", roadmap.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
