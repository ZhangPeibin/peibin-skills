from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "ticketctl.py"
FAKE_GH = Path(__file__).with_name("fake_gh.py")
PAUSED_STATE = """<!-- run-ticket-plan:state:start -->
```json
{"version": 1, "pause_reason": "Maintainer pause."}
```
<!-- run-ticket-plan:state:end -->"""


class GitHubTicketCtlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "github.json"
        self.state_path.write_text(
            json.dumps(
                {
                    "repository": "octo/demo",
                    "login": "alice",
                    "permissions": {"alice": "admin", "bob": "triage"},
                    "issues": [
                        {
                            "number": 1,
                            "title": "Demo feature",
                            "body": "Feature specification.",
                            "state": "OPEN",
                            "url": "https://github.com/octo/demo/issues/1",
                            "labels": [],
                            "assignees": [],
                            "user": {"login": "alice"},
                            "createdAt": "2026-01-01T00:00:00Z",
                        },
                        {
                            "number": 2,
                            "title": "Build the foundation",
                            "body": (
                                "## Parent\n\n#1\n\n"
                                "## What to build\n\nBuild the foundation.\n\n"
                                "## Acceptance criteria\n\n- [ ] Foundation works.\n\n"
                                "## Blocked by\n\n- None — can start immediately.\n"
                            ),
                            "state": "OPEN",
                            "url": "https://github.com/octo/demo/issues/2",
                            "labels": [{"name": "ready-for-agent"}],
                            "assignees": [],
                            "user": {"login": "alice"},
                            "createdAt": "2026-01-02T00:00:00Z",
                        },
                        {
                            "number": 3,
                            "title": "Build the consumer",
                            "body": (
                                "## Parent\n\n#1\n\n"
                                "## What to build\n\nBuild the consumer.\n\n"
                                "## Acceptance criteria\n\n- [ ] Consumer works.\n\n"
                                "## Blocked by\n\n- #2\n"
                            ),
                            "state": "OPEN",
                            "url": "https://github.com/octo/demo/issues/3",
                            "labels": [{"name": "ready-for-agent"}],
                            "assignees": [],
                            "user": {"login": "alice"},
                            "createdAt": "2026-01-03T00:00:00Z",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        wrapper = fake_bin / "gh"
        wrapper.write_text(
            f"#!/bin/sh\nexec '{sys.executable}' '{FAKE_GH}' \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment["FAKE_GH_STATE"] = str(self.state_path)
        self.environment["PATH"] = (
            f"{fake_bin}{os.pathsep}{self.environment['PATH']}"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self, command: str, *arguments: str, expected: int = 0
    ) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                command,
                "--tracker",
                "github",
                "--repo-root",
                str(self.root),
                "--repo",
                "octo/demo",
                *arguments,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            env=self.environment,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            completed.stderr + completed.stdout,
        )
        return json.loads(completed.stdout)

    def update_issue(self, number: int, **updates: object) -> None:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        issue = next(issue for issue in state["issues"] if issue["number"] == number)
        issue.update(updates)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def update_state(self, **updates: object) -> None:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state.update(updates)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def add_issue(
        self, number: int, *, parent: int = 1, blocked_by: str = "None."
    ) -> None:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["issues"].append(
            {
                "number": number,
                "title": f"Ticket {number}",
                "body": (
                    f"## Parent\n\n#{parent}\n\n"
                    f"## What to build\n\nBuild ticket {number}.\n\n"
                    f"## Acceptance criteria\n\n- [ ] Ticket {number} works.\n\n"
                    f"## Blocked by\n\n{blocked_by}\n"
                ),
                "state": "OPEN",
                "url": f"https://github.com/octo/demo/issues/{number}",
                "labels": [{"name": "ready-for-agent"}],
                "assignees": [],
                "user": {"login": "alice"},
                "createdAt": f"2026-01-{number:02d}T00:00:00Z",
            }
        )
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def test_status_derives_the_remote_feature_without_writing_a_roadmap(self) -> None:
        payload = self.run_cli("status")

        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["outcome"], "READY")
        self.assertEqual(payload["data"]["active"], None)
        self.assertEqual(payload["data"]["frontier"], ["2"])
        self.assertEqual(payload["data"]["blocked_by"], {"3": ["2"]})
        self.assertEqual(
            payload["data"]["roadmap"],
            {"state": "MISSING", "changed": False, "created": False},
        )
        self.assertEqual(
            payload["next"],
            {
                "command": "tickets next 2",
                "reason": "oldest executable ticket",
            },
        )
        self.assertFalse(self.root.joinpath(".scratch").exists())

    def test_next_claims_the_frontier_and_persists_the_shared_roadmap(self) -> None:
        activated = self.run_cli("next")

        self.assertEqual(activated["schema_version"], 3)
        self.assertEqual(activated["outcome"], "WORK_PACKET")
        self.assertEqual(activated["ticket"], "2")
        self.assertTrue(activated["changed"])
        packet = activated["data"]["work_packet"]
        resolved_root = self.root.resolve()
        self.assertEqual(
            packet["source"],
            {
                "tracker": "github",
                "feature_root": str(resolved_root / ".scratch" / "github-1"),
                "repository_root": str(resolved_root),
                "repository": "octo/demo",
                "feature_issue": "1",
                "ticket_url": "https://github.com/octo/demo/issues/2",
            },
        )
        roadmap = self.root / ".scratch" / "github-1" / "implementation-roadmap.md"
        roadmap_text = roadmap.read_text(encoding="utf-8")
        self.assertIn("GitHub Issues are authoritative", roadmap_text)
        self.assertIn(
            "[`2` Build the foundation](https://github.com/octo/demo/issues/2)",
            roadmap_text,
        )

        active = self.run_cli("status")
        self.assertEqual(active["outcome"], "ACTIVE")
        self.assertEqual(active["data"]["active"], "2")

        repeated = self.run_cli("next")
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["data"]["work_packet"], packet)

    def test_done_closes_only_the_active_issue_and_advances_the_frontier(self) -> None:
        self.run_cli("next")

        completed = self.run_cli(
            "done", "--ticket", "2", "--note", "Implemented and committed."
        )

        self.assertEqual(completed["outcome"], "DONE")
        self.assertEqual(completed["ticket"], "2")
        self.assertTrue(completed["changed"])
        self.assertEqual(completed["data"]["frontier"], ["3"])
        status = self.run_cli("status")
        self.assertEqual(status["outcome"], "READY")
        self.assertEqual(status["data"]["by_state"]["done"], ["2"])
        self.assertEqual(status["data"]["frontier"], ["3"])
        self.assertEqual(
            status["data"]["completion_methods"], {"2": "explicit-done"}
        )

        repeated = self.run_cli(
            "done", "--ticket", "2", "--note", "Implemented and committed."
        )
        self.assertFalse(repeated["changed"])

    def test_pause_and_explicit_resume_preserve_remote_recovery_history(self) -> None:
        self.run_cli("next")

        paused = self.run_cli(
            "pause", "--ticket", "2", "--reason", "Waiting for a decision."
        )

        self.assertEqual(paused["outcome"], "PAUSED")
        self.assertTrue(paused["changed"])
        self.assertEqual(paused["data"]["frontier"], [])
        status = self.run_cli("status")
        self.assertEqual(status["outcome"], "DEADLOCK")
        self.assertEqual(status["data"]["by_state"]["paused"], ["2"])

        repeated = self.run_cli(
            "pause", "--ticket", "2", "--reason", "Waiting for a decision."
        )
        self.assertFalse(repeated["changed"])

        resumed = self.run_cli("next", "--ticket", "2")
        self.assertEqual(resumed["outcome"], "WORK_PACKET")
        self.assertTrue(resumed["changed"])
        self.assertEqual(
            resumed["data"]["work_packet"]["history"]["last_pause"],
            {"reason": "Waiting for a decision.", "until": []},
        )

    def test_issue_claimed_by_another_user_is_not_executable(self) -> None:
        self.update_issue(2, assignees=[{"login": "bob"}])
        self.add_issue(4)

        status = self.run_cli("status")

        self.assertEqual(status["outcome"], "ACTIVE")
        self.assertEqual(status["data"]["active"], "2")
        self.assertEqual(status["data"]["frontier"], ["4"])
        self.assertEqual(status["data"]["by_state"]["in-progress"], ["2"])
        self.assertIsNone(status["next"])
        rejected = self.run_cli("next", "--ticket", "4", expected=2)
        self.assertEqual(rejected["error"]["code"], "ACTIVE_TICKET_EXISTS")
        pause = self.run_cli(
            "pause", "--ticket", "2", "--reason", "Not mine.", expected=2
        )
        self.assertEqual(pause["error"]["code"], "TICKET_NOT_ACTIVE")

    def test_unlabelled_issue_cannot_be_explicitly_activated(self) -> None:
        self.update_issue(2, labels=[])

        rejected = self.run_cli("next", "--ticket", "2", expected=2)

        self.assertEqual(rejected["error"]["code"], "TICKET_PAUSED")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        issue = next(issue for issue in state["issues"] if issue["number"] == 2)
        self.assertEqual(issue["assignees"], [])
        self.assertEqual(issue.get("comments", []), [])

    def test_conflicting_remote_claim_is_rolled_back(self) -> None:
        self.add_issue(4)
        self.update_state(claim_on_add={"number": 2, "login": "bob"})

        conflicted = self.run_cli("next", "--ticket", "4", expected=2)

        self.assertTrue(conflicted["changed"])
        self.assertEqual(conflicted["error"]["code"], "POST_COMMIT_ERROR")
        self.assertTrue(conflicted["error"]["details"]["rolled_back"])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        issue_2 = next(issue for issue in state["issues"] if issue["number"] == 2)
        issue_4 = next(issue for issue in state["issues"] if issue["number"] == 4)
        self.assertEqual(issue_2["assignees"], [{"login": "bob"}])
        self.assertEqual(issue_4["assignees"], [])

    def test_pause_retry_finishes_a_partial_remote_mutation(self) -> None:
        self.run_cli("next")
        self.update_state(
            failures=[
                {
                    "arguments": ["issue", "edit", "--remove-assignee"],
                    "remaining": 1,
                    "message": "temporary edit failure",
                }
            ]
        )

        partial = self.run_cli(
            "pause",
            "--ticket",
            "2",
            "--reason",
            "Waiting for a decision.",
            expected=2,
        )

        self.assertTrue(partial["changed"])
        self.assertEqual(partial["error"]["code"], "POST_COMMIT_ERROR")
        recovered = self.run_cli(
            "pause", "--ticket", "2", "--reason", "Waiting for a decision."
        )
        self.assertTrue(recovered["changed"])
        repeated = self.run_cli(
            "pause", "--ticket", "2", "--reason", "Waiting for a decision."
        )
        self.assertFalse(repeated["changed"])

    def test_done_retry_finishes_a_partial_remote_mutation(self) -> None:
        self.run_cli("next")
        self.update_state(
            failures=[
                {
                    "arguments": ["issue", "close"],
                    "remaining": 1,
                    "message": "temporary close failure",
                }
            ]
        )

        partial = self.run_cli(
            "done",
            "--ticket",
            "2",
            "--note",
            "Implemented and committed.",
            expected=2,
        )

        self.assertTrue(partial["changed"])
        self.assertEqual(partial["error"]["code"], "POST_COMMIT_ERROR")
        recovered = self.run_cli(
            "done", "--ticket", "2", "--note", "Implemented and committed."
        )
        self.assertTrue(recovered["changed"])
        repeated = self.run_cli(
            "done", "--ticket", "2", "--note", "Implemented and committed."
        )
        self.assertFalse(repeated["changed"])

    def test_resume_assignment_failure_preserves_no_argument_retry_target(self) -> None:
        self.add_issue(4)
        self.add_issue(10)
        self.run_cli("next", "--ticket", "10")
        self.run_cli(
            "pause",
            "--ticket",
            "10",
            "--reason",
            "Waiting for remediation.",
            "--wait-for",
            "4",
        )
        self.update_issue(4, state="CLOSED")
        self.update_state(
            failures=[
                {
                    "arguments": ["issue", "edit", "--add-assignee"],
                    "remaining": 1,
                    "message": "temporary assignment failure",
                }
            ]
        )

        failed = self.run_cli("next", expected=2)

        self.assertFalse(failed["changed"])
        self.assertEqual(failed["error"]["code"], "ENVIRONMENT_ERROR")
        recovered = self.run_cli("next")
        self.assertEqual(recovered["ticket"], "10")

    def test_resume_comment_failure_is_completed_by_exact_retry(self) -> None:
        self.add_issue(4)
        self.add_issue(10)
        self.run_cli("next", "--ticket", "10")
        self.run_cli(
            "pause",
            "--ticket",
            "10",
            "--reason",
            "Waiting for remediation.",
            "--wait-for",
            "4",
        )
        self.update_issue(4, state="CLOSED")
        self.update_state(
            failures=[
                {
                    "arguments": ["issue", "comment"],
                    "remaining": 1,
                    "message": "temporary comment failure",
                }
            ]
        )

        partial = self.run_cli("next", expected=2)

        self.assertTrue(partial["changed"])
        self.assertEqual(partial["error"]["code"], "POST_COMMIT_ERROR")
        recovered = self.run_cli("next")
        self.assertEqual(recovered["ticket"], "10")
        self.assertTrue(recovered["changed"])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        issue = next(issue for issue in state["issues"] if issue["number"] == 10)
        self.assertEqual(issue["assignees"], [{"login": "alice"}])
        self.assertEqual(len(issue["comments"]), 2)

    def test_github_read_failure_never_falls_back_to_local_state(self) -> None:
        self.update_state(
            failures=[
                {
                    "arguments": [
                        "repos/octo/demo/issues?state=all&per_page=100"
                    ],
                    "remaining": 1,
                    "message": "authentication failed",
                }
            ]
        )

        failed = self.run_cli("status", expected=2)

        self.assertEqual(failed["schema_version"], 3)
        self.assertEqual(failed["error"]["code"], "ENVIRONMENT_ERROR")
        self.assertFalse(self.root.joinpath(".scratch").exists())

    def test_remediation_completion_makes_a_remote_ticket_resumable(self) -> None:
        self.add_issue(4)
        self.run_cli("next", "--ticket", "2")

        paused = self.run_cli(
            "pause",
            "--ticket",
            "2",
            "--reason",
            "Needs remediation.",
            "--wait-for",
            "4",
        )

        self.assertEqual(paused["data"]["waits_for"], ["4"])
        self.assertEqual(paused["data"]["frontier"], ["4"])
        self.run_cli("next", "--ticket", "4")
        self.run_cli("done", "--ticket", "4", "--note", "Remediated.")
        status = self.run_cli("status")
        self.assertEqual(status["data"]["resumable"], ["2"])
        self.assertEqual(status["next"]["command"], "tickets next 2")

        resumed = self.run_cli("next", "--ticket", "2")
        self.assertEqual(
            resumed["data"]["work_packet"]["history"]["last_pause"],
            {"reason": "Needs remediation.", "until": ["4"]},
        )

    def test_status_falls_back_to_body_links_when_native_dag_apis_are_unavailable(
        self,
    ) -> None:
        self.update_state(
            failures=[
                {
                    "arguments": ["repos/octo/demo/issues/1/sub_issues"],
                    "remaining": 1,
                    "message": "HTTP 404: Not Found",
                },
                {
                    "arguments": [
                        "repos/octo/demo/issues/2/dependencies/blocked_by"
                    ],
                    "remaining": 1,
                    "message": "HTTP 404: Not Found",
                },
                {
                    "arguments": [
                        "repos/octo/demo/issues/3/dependencies/blocked_by"
                    ],
                    "remaining": 1,
                    "message": "HTTP 404: Not Found",
                },
            ]
        )

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["2"])
        self.assertEqual(payload["data"]["blocked_by"], {"3": ["2"]})

    def test_ambiguous_remote_features_require_an_explicit_parent(self) -> None:
        self.update_state(
            issues=json.loads(self.state_path.read_text(encoding="utf-8"))["issues"]
            + [
                {
                    "number": 9,
                    "title": "Another feature",
                    "body": "Another specification.",
                    "state": "OPEN",
                    "url": "https://github.com/octo/demo/issues/9",
                    "labels": [],
                    "assignees": [],
                    "user": {"login": "alice"},
                    "createdAt": "2026-01-09T00:00:00Z",
                }
            ]
        )
        self.add_issue(10, parent=9)

        ambiguous = self.run_cli("status", expected=2)

        self.assertEqual(ambiguous["error"]["code"], "FEATURE_REQUIRED")
        self.assertEqual(ambiguous["error"]["details"]["candidates"], [1, 9])
        selected = self.run_cli("status", "--feature", "1")
        self.assertEqual(selected["data"]["frontier"], ["2"])

    def test_explicit_feature_uses_native_sub_issues_and_dependencies(self) -> None:
        self.update_state(slurp_pages=True)
        foundation = (
            "## What to build\n\nBuild the foundation.\n\n"
            "## Acceptance criteria\n\n- [ ] Foundation works.\n\n"
            "## Blocked by\n\nNone.\n"
        )
        consumer = (
            "## What to build\n\nBuild the consumer.\n\n"
            "## Acceptance criteria\n\n- [ ] Consumer works.\n\n"
            "## Blocked by\n\nNone.\n"
        )
        self.update_issue(2, body=foundation, native_parent=1)
        self.update_issue(
            3,
            body=consumer,
            native_parent=1,
            native_blocked_by=[2],
        )

        payload = self.run_cli("status", "--feature", "1")

        self.assertEqual(payload["data"]["frontier"], ["2"])
        self.assertEqual(payload["data"]["blocked_by"], {"3": ["2"]})

    def test_native_only_feature_is_auto_discovered(self) -> None:
        foundation = (
            "## What to build\n\nBuild the foundation.\n\n"
            "## Acceptance criteria\n\n- [ ] Foundation works.\n\n"
            "## Blocked by\n\nNone.\n"
        )
        consumer = (
            "## What to build\n\nBuild the consumer.\n\n"
            "## Acceptance criteria\n\n- [ ] Consumer works.\n\n"
            "## Blocked by\n\nNone.\n"
        )
        self.update_issue(2, body=foundation, native_parent=1)
        self.update_issue(
            3,
            body=consumer,
            native_parent=1,
            native_blocked_by=[2],
        )

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["2"])
        self.assertEqual(payload["data"]["blocked_by"], {"3": ["2"]})

    def test_native_children_override_stale_body_parent_links(self) -> None:
        consumer = (
            "## What to build\n\nBuild the consumer.\n\n"
            "## Acceptance criteria\n\n- [ ] Consumer works.\n\n"
            "## Blocked by\n\nNone.\n"
        )
        self.update_issue(3, body=consumer, native_parent=1)

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["3"])
        all_states = [
            ticket
            for tickets in payload["data"]["by_state"].values()
            for ticket in tickets
        ]
        self.assertNotIn("2", all_states)

    def test_completed_remote_features_do_not_make_discovery_ambiguous(self) -> None:
        self.update_state(
            issues=json.loads(self.state_path.read_text(encoding="utf-8"))["issues"]
            + [
                {
                    "number": 9,
                    "title": "Completed feature",
                    "body": "Completed specification.",
                    "state": "CLOSED",
                    "url": "https://github.com/octo/demo/issues/9",
                    "labels": [],
                    "assignees": [],
                    "user": {"login": "alice"},
                    "createdAt": "2026-01-09T00:00:00Z",
                }
            ]
        )
        self.add_issue(10, parent=9)
        self.update_issue(10, state="CLOSED")

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["2"])

    def test_untrusted_issue_comment_cannot_change_controller_state(self) -> None:
        self.update_issue(
            2,
            comments=[
                {
                    "id": 1,
                    "body": PAUSED_STATE,
                    "user": {"login": "mallory"},
                    "author_association": "NONE",
                }
            ],
        )

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["2"])

    def test_member_without_issue_permission_cannot_change_controller_state(self) -> None:
        self.update_state(permissions={"alice": "admin", "bob": "read"})
        self.update_issue(
            2,
            comments=[
                {
                    "id": 1,
                    "body": PAUSED_STATE,
                    "user": {"login": "bob"},
                    "author_association": "MEMBER",
                }
            ],
        )

        payload = self.run_cli("status")

        self.assertEqual(payload["data"]["frontier"], ["2"])

    def test_untrusted_body_parent_cannot_join_the_feature(self) -> None:
        self.add_issue(4)
        self.update_issue(
            4,
            body="## Parent\n\n#1\n\nNo controller acceptance criteria.",
            user={"login": "mallory"},
        )

        payload = self.run_cli("status")

        all_states = [
            ticket
            for tickets in payload["data"]["by_state"].values()
            for ticket in tickets
        ]
        self.assertNotIn("4", all_states)

    def test_maintainer_controller_comment_is_shared_across_users(self) -> None:
        self.update_issue(
            2,
            comments=[
                {
                    "id": 1,
                    "body": PAUSED_STATE,
                    "user": {"login": "bob"},
                    "author_association": "MEMBER",
                }
            ],
        )

        payload = self.run_cli("status")

        self.assertEqual(payload["outcome"], "DEADLOCK")
        self.assertEqual(payload["data"]["by_state"]["paused"], ["2"])


if __name__ == "__main__":
    unittest.main()
