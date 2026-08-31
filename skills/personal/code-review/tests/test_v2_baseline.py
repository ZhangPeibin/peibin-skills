from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).parents[1]
SCOPE_CLI = SKILL_ROOT / "scripts" / "review_scope.py"
CONTRACT_CLI = SKILL_ROOT / "scripts" / "review_contract.py"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from review_scope import ScopeError, resolve_local, resolve_merge_base_target


def _run(
    *args: str,
    cwd: Path | None = None,
    stdin: object | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args],
        cwd=cwd,
        env=env,
        input=None if stdin is None else json.dumps(stdin),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _run("git", *args, cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


class V2BaselineBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo = Path(self.temp_dir.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.name", "Review Test")
        _git(self.repo, "config", "user.email", "review@example.com")
        (self.repo / "service.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (self.repo / "caller.py").write_text("from service import value\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "base")
        _git(self.repo, "branch", "-M", "main")

    def test_localized_change_builds_one_frozen_paired_review(self) -> None:
        (self.repo / "service.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        _git(self.repo, "add", "service.py")
        (self.repo / "caller.py").write_text(
            "from service import value\n\nprint(value())\n", encoding="utf-8"
        )
        (self.repo / "new_test.py").write_text("assert True\n", encoding="utf-8")
        status_before = _git(self.repo, "status", "--porcelain=v2", "-z").stdout

        scope_result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "local",
            "--repo",
            str(self.repo),
        )
        self.assertEqual(scope_result.returncode, 0, scope_result.stderr)
        self.assertEqual(
            _git(self.repo, "status", "--porcelain=v2", "-z").stdout,
            status_before,
        )
        scope = json.loads(scope_result.stdout)
        self.assertEqual(scope["schema_version"], "2.0")
        self.assertEqual(scope["target"]["kind"], "local")
        self.assertRegex(scope["scope_id"], r"^local:sha256:[0-9a-f]{64}$")
        self.assertEqual(
            {record["source"] for record in scope["changed_records"]},
            {"staged", "unstaged", "untracked"},
        )

        bundle_result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "build-one-unit",
            "--unit-id",
            "unit-service",
            "--feature-name",
            "Service value",
            stdin=scope,
        )
        self.assertEqual(bundle_result.returncode, 0, bundle_result.stderr)
        bundle = json.loads(bundle_result.stdout)
        graph = bundle["feature_graph"]
        plan = bundle["review_plan"]
        envelopes = bundle["reviewer_task_envelopes"]
        ledger = bundle["coverage_ledger"]

        self.assertEqual(len(graph["review_units"]), 1)
        self.assertEqual(
            {assignment["role"] for assignment in plan["assignments"]},
            {"internal_behavior", "external_contract"},
        )
        self.assertEqual(len(envelopes), 2)
        self.assertEqual({envelope["scope_id"] for envelope in envelopes}, {scope["scope_id"]})
        self.assertEqual(
            {tuple(envelope["changed_record_ids"]) for envelope in envelopes},
            {tuple(record["record_id"] for record in scope["changed_records"])},
        )
        self.assertEqual(envelopes[0]["core_policy"], envelopes[1]["core_policy"])
        self.assertNotIn("peer_result", envelopes[0])
        self.assertNotIn("peer_result", envelopes[1])
        self.assertTrue(all(not entry["covered"] for entry in ledger["records"]))

        reviewer_results = [
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
            for envelope in envelopes
        ]
        finalize_result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "complete-one-unit",
            stdin={"bundle": bundle, "reviewer_results": reviewer_results},
        )
        self.assertEqual(finalize_result.returncode, 0, finalize_result.stderr)
        completed = json.loads(finalize_result.stdout)
        self.assertTrue(completed["coverage_ledger"]["complete"])
        self.assertTrue(
            all(entry["covered"] for entry in completed["coverage_ledger"]["records"])
        )
        self.assertEqual(completed["candidate_findings"], [])

    def test_root_commit_uses_empty_tree_and_immutable_head(self) -> None:
        root_commit = _git(self.repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "commit",
            root_commit,
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        self.assertEqual(scope["target"]["kind"], "commit")
        self.assertEqual(
            scope["target"]["base_oid"],
            "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        )
        self.assertEqual(scope["target"]["head_oid"], root_commit)
        self.assertTrue(scope["changed_records"])
        self.assertTrue(all(record["source"] == "commit" for record in scope["changed_records"]))

    def test_explicit_range_is_preserved_while_endpoints_are_frozen(self) -> None:
        base = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        (self.repo / "service.py").write_text("def value():\n    return 3\n", encoding="utf-8")
        _git(self.repo, "add", "service.py")
        _git(self.repo, "commit", "-qm", "change value")
        head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        requested = f"{base[:8]}..{head[:8]}"

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "range",
            requested,
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        self.assertEqual(scope["target"]["kind"], "range")
        self.assertEqual(scope["target"]["requested"], requested)
        self.assertEqual(scope["target"]["semantics"], "explicit-two-dot")
        self.assertEqual(scope["target"]["base_oid"], base)
        self.assertEqual(scope["target"]["head_oid"], head)

    def test_branch_uses_merge_base_and_excludes_base_only_changes(self) -> None:
        common = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-qb", "feature")
        (self.repo / "caller.py").write_text(
            "from service import value\n\nprint(value())\n", encoding="utf-8"
        )
        _git(self.repo, "add", "caller.py")
        _git(self.repo, "commit", "-qm", "feature caller")
        feature_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-q", "main")
        (self.repo / "base_only.py").write_text("BASE_ONLY = True\n", encoding="utf-8")
        _git(self.repo, "add", "base_only.py")
        _git(self.repo, "commit", "-qm", "advance base")

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "branch",
            "--base",
            "main",
            "--head",
            "feature",
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        self.assertEqual(scope["target"]["kind"], "branch")
        self.assertEqual(scope["target"]["base_oid"], common)
        self.assertEqual(scope["target"]["head_oid"], feature_head)
        self.assertEqual(scope["target"]["semantics"], "merge-base")
        self.assertNotIn(
            "base_only.py",
            {record["new_path"] for record in scope["changed_records"]},
        )

    def test_pull_request_records_identity_and_uses_merge_base(self) -> None:
        common = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-qb", "pr-head")
        (self.repo / "caller.py").write_text(
            "from service import value\n\nprint(value())\n", encoding="utf-8"
        )
        _git(self.repo, "add", "caller.py")
        _git(self.repo, "commit", "-qm", "PR change")
        head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "pr",
            "--number",
            "314",
            "--base",
            "main",
            "--head",
            "pr-head",
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        self.assertEqual(scope["target"]["kind"], "pull_request")
        self.assertEqual(scope["target"]["pull_request_number"], 314)
        self.assertEqual(scope["target"]["base_oid"], common)
        self.assertEqual(scope["target"]["head_oid"], head)
        self.assertEqual(scope["target"]["semantics"], "merge-base")

    def test_untracked_scope_includes_only_safe_contained_files(self) -> None:
        (self.repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
        _git(self.repo, "add", ".gitignore")
        _git(self.repo, "commit", "-qm", "add ignore rule")
        (self.repo / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
        (self.repo / "asset.bin").write_bytes(b"a\0b")
        (self.repo / ".ENV.production").write_text(
            "TOKEN=do-not-read\n", encoding="utf-8"
        )
        (self.repo / "ignored.log").write_text("ignored\n", encoding="utf-8")
        (self.repo / "vendor").mkdir()
        (self.repo / "vendor" / "copied.py").write_text("COPIED = True\n", encoding="utf-8")
        (self.repo / "build").mkdir()
        (self.repo / "build" / "generated.js").write_text("generated\n", encoding="utf-8")
        (self.repo / "large.txt").write_text("01234567890123456789\n", encoding="utf-8")
        os.symlink("/tmp/outside-review-target", self.repo / "outside-link")
        os.mkfifo(self.repo / "review.pipe")

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "local",
            "--repo",
            str(self.repo),
            "--max-untracked-bytes",
            "15",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        included = {record["new_path"]: record for record in scope["changed_records"]}
        self.assertEqual(set(included), {"asset.bin", "safe.py"})
        self.assertEqual(included["asset.bin"]["change_type"], "binary")
        self.assertEqual(included["asset.bin"]["anchor"]["kind"], "change")
        self.assertNotIn("line", included["asset.bin"]["anchor"])
        exclusions = {item["path"]: item["reason"] for item in scope["exclusions"]}
        self.assertEqual(exclusions[".ENV.production"], "likely-secret")
        self.assertEqual(exclusions["ignored.log"], "ignored")
        self.assertEqual(exclusions["vendor"], "vendored")
        self.assertEqual(exclusions["build"], "generated")
        self.assertEqual(exclusions["large.txt"], "oversized")
        self.assertEqual(exclusions["outside-link"], "special-or-symlink")
        self.assertEqual(exclusions["review.pipe"], "special-or-symlink")
        self.assertNotIn("do-not-read", result.stdout)

    def test_public_scope_numeric_inputs_must_be_positive_in_cli_and_api(self) -> None:
        cli_cases = (
            (
                "max-untracked-bytes",
                [
                    "local",
                    "--repo",
                    str(self.repo),
                    "--max-untracked-bytes=-1",
                ],
            ),
            (
                "pull-request-number",
                [
                    "pr",
                    "--repo",
                    str(self.repo),
                    "--number=0",
                    "--base",
                    "HEAD",
                    "--head",
                    "HEAD",
                ],
            ),
        )
        for name, arguments in cli_cases:
            with self.subTest(surface="cli", name=name):
                result = _run(sys.executable, str(SCOPE_CLI), *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("positive integer", result.stderr)

        with self.subTest(surface="api", name="max-untracked-bytes"):
            with self.assertRaisesRegex(ScopeError, "positive integer"):
                resolve_local(self.repo, 0)
        with self.subTest(surface="api", name="pull-request-number"):
            with self.assertRaisesRegex(ScopeError, "positive integer"):
                resolve_merge_base_target(
                    self.repo,
                    kind="pull_request",
                    source="pull_request",
                    base="HEAD",
                    head="HEAD",
                    requested="PR #-1",
                    extra_target={"pull_request_number": -1},
                )

    def test_tracked_non_text_changes_use_typed_anchors_and_link_data(self) -> None:
        (self.repo / "binary.dat").write_bytes(b"old\0binary")
        (self.repo / "delete.txt").write_text("remove me\n", encoding="utf-8")
        (self.repo / "mode.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.repo / "rename-old.txt").write_text("same content\n", encoding="utf-8")
        os.symlink("/outside/old-target", self.repo / "tracked-link")
        nested = self.repo / "nested-repo"
        nested.mkdir()
        _git(nested, "init", "-q")
        _git(nested, "config", "user.name", "Nested Test")
        _git(nested, "config", "user.email", "nested@example.com")
        (nested / "value.txt").write_text("one\n", encoding="utf-8")
        _git(nested, "add", "value.txt")
        _git(nested, "commit", "-qm", "nested base")
        _git(self.repo, "add", "binary.dat", "delete.txt", "mode.sh", "rename-old.txt", "tracked-link", "nested-repo")
        _git(self.repo, "commit", "-qm", "add typed fixtures")

        (self.repo / "binary.dat").write_bytes(b"new\0binary")
        (self.repo / "delete.txt").unlink()
        os.chmod(self.repo / "mode.sh", 0o755)
        _git(self.repo, "mv", "rename-old.txt", "rename-new.txt")
        (self.repo / "tracked-link").unlink()
        os.symlink("/outside/new-target", self.repo / "tracked-link")
        (nested / "value.txt").write_text("two\n", encoding="utf-8")
        _git(nested, "add", "value.txt")
        _git(nested, "commit", "-qm", "nested update")
        _git(self.repo, "add", "-A")

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "local",
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        records = {
            record["new_path"] or record["old_path"]: record
            for record in json.loads(result.stdout)["changed_records"]
        }
        expected_types = {
            "binary.dat": "binary",
            "delete.txt": "deletion",
            "mode.sh": "file_mode",
            "rename-new.txt": "rename_only",
            "tracked-link": "symlink",
            "nested-repo": "gitlink",
        }
        self.assertEqual(
            {path: records[path]["change_type"] for path in expected_types},
            expected_types,
        )
        for path in expected_types.keys() - {"delete.txt"}:
            self.assertEqual(records[path]["anchor"]["kind"], "change")
            self.assertNotIn("line", records[path]["anchor"])
        self.assertEqual(records["delete.txt"]["anchor"]["side"], "old")
        self.assertEqual(records["tracked-link"]["old_link_target"], "/outside/old-target")
        self.assertEqual(records["tracked-link"]["new_link_target"], "/outside/new-target")

    def test_committed_symlink_targets_preserve_non_utf8_bytes(self) -> None:
        link_path = os.fsencode(self.repo / "byte-link")
        old_target = b"old-\xff-target"
        new_target = b"new-\xfe-target"
        os.symlink(old_target, link_path)
        _git(self.repo, "add", "byte-link")
        _git(self.repo, "commit", "-qm", "add byte-preserving link")
        os.unlink(link_path)
        os.symlink(new_target, link_path)
        _git(self.repo, "add", "byte-link")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(
            item
            for item in json.loads(result.stdout)["changed_records"]
            if item["new_path"] == "byte-link"
        )
        self.assertEqual(
            record["old_link_target"].encode("utf-8", "surrogateescape"),
            old_target,
        )
        self.assertEqual(
            record["new_link_target"].encode("utf-8", "surrogateescape"),
            new_target,
        )

    def test_unstaged_mode_only_change_is_not_given_a_line_anchor(self) -> None:
        script = self.repo / "run.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        _git(self.repo, "add", "run.sh")
        _git(self.repo, "commit", "-qm", "add script")
        os.chmod(script, 0o755)

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(
            item
            for item in json.loads(result.stdout)["changed_records"]
            if item["new_path"] == "run.sh"
        )
        self.assertEqual(record["source"], "unstaged")
        self.assertEqual(record["change_type"], "file_mode")
        self.assertEqual(record["anchor"]["kind"], "change")
        self.assertNotIn("line", record["anchor"])

    def test_unstaged_gitlink_update_has_a_reproducible_typed_identity(self) -> None:
        nested = self.repo / "nested"
        nested.mkdir()
        _git(nested, "init", "-q")
        _git(nested, "config", "user.name", "Nested Test")
        _git(nested, "config", "user.email", "nested@example.com")
        (nested / "value.txt").write_text("one\n", encoding="utf-8")
        _git(nested, "add", "value.txt")
        _git(nested, "commit", "-qm", "nested base")
        _git(self.repo, "add", "nested")
        _git(self.repo, "commit", "-qm", "add gitlink")
        (nested / "value.txt").write_text("two\n", encoding="utf-8")
        _git(nested, "add", "value.txt")
        _git(nested, "commit", "-qm", "advance nested")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(
            item
            for item in json.loads(result.stdout)["changed_records"]
            if item["new_path"] == "nested"
        )
        self.assertEqual(record["source"], "unstaged")
        self.assertEqual(record["change_type"], "gitlink")
        self.assertRegex(record["content_identity"], r"^gitlink:sha256:[0-9a-f]{64}$")
        self.assertEqual(record["anchor"]["kind"], "change")

    def test_text_replacement_prefers_a_new_side_anchor(self) -> None:
        (self.repo / "service.py").write_text("def value():\n    return 9\n", encoding="utf-8")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(
            item
            for item in json.loads(result.stdout)["changed_records"]
            if item["new_path"] == "service.py"
        )
        self.assertEqual(record["anchor"]["kind"], "line")
        self.assertEqual(record["anchor"]["side"], "new")
        self.assertEqual(record["anchor"]["line"], 2)

    def test_repository_filename_is_always_a_literal_pathspec(self) -> None:
        unusual = self.repo / ":(exclude)*"
        unusual.write_text("OLD = True\n", encoding="utf-8")
        _git(self.repo, "--literal-pathspecs", "add", "--", unusual.name)
        _git(self.repo, "commit", "-qm", "add unusual filename")
        unusual.write_text("NEW = True\n", encoding="utf-8")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        record = next(
            item
            for item in json.loads(result.stdout)["changed_records"]
            if item["new_path"] == unusual.name
        )
        self.assertEqual(record["anchor"]["kind"], "line")
        self.assertEqual(record["anchor"]["side"], "new")

    def test_sha256_root_commit_uses_its_repository_empty_tree(self) -> None:
        sha_temp = tempfile.TemporaryDirectory()
        self.addCleanup(sha_temp.cleanup)
        repo = Path(sha_temp.name)
        init = _run("git", "init", "-q", "--object-format=sha256", cwd=repo)
        if init.returncode != 0:
            self.skipTest("installed Git does not support SHA-256 repositories")
        _git(repo, "config", "user.name", "SHA Test")
        _git(repo, "config", "user.email", "sha@example.com")
        (repo / "root.txt").write_text("root\n", encoding="utf-8")
        _git(repo, "add", "root.txt")
        _git(repo, "commit", "-qm", "root")
        root_oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
        empty_tree_result = subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            cwd=repo,
            input="",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(empty_tree_result.returncode, 0, empty_tree_result.stderr)
        empty_tree = empty_tree_result.stdout.strip()

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "commit",
            root_oid,
            "--repo",
            str(repo),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        scope = json.loads(result.stdout)
        self.assertEqual(scope["target"]["base_oid"], empty_tree)
        self.assertEqual(len(scope["target"]["head_oid"]), 64)

    def test_git_inspection_does_not_execute_ambient_path_wrapper(self) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        wrapper_temp = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper_temp.cleanup)
        wrapper_dir = Path(wrapper_temp.name)
        log_path = wrapper_dir / "git-env.log"
        wrapper = wrapper_dir / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"printf '%s:%s:%s\\n' \"${{GIT_NO_LAZY_FETCH-}}\" \"${{GIT_TERMINAL_PROMPT-}}\" \"${{GIT_OPTIONAL_LOCKS-}}\" >> {shlex.quote(str(log_path))}\n"
            f"exec {shlex.quote(str(real_git))} \"$@\"\n",
            encoding="utf-8",
        )
        os.chmod(wrapper, 0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{wrapper_dir}{os.pathsep}{environment['PATH']}"
        (self.repo / "new.py").write_text("VALUE = 1\n", encoding="utf-8")

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "local",
            "--repo",
            str(self.repo),
            env=environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(log_path.exists())

    def test_scope_never_executes_external_diff_or_textconv(self) -> None:
        helper_temp = tempfile.TemporaryDirectory()
        self.addCleanup(helper_temp.cleanup)
        helper_dir = Path(helper_temp.name)
        marker = helper_dir / "executed"
        helper = helper_dir / "external-diff"
        helper.write_text(
            "#!/bin/sh\n" f"touch {shlex.quote(str(marker))}\n" "exit 0\n",
            encoding="utf-8",
        )
        os.chmod(helper, 0o755)
        _git(self.repo, "config", "diff.external", str(helper))
        (self.repo / "service.py").write_text("def value():\n    return 8\n", encoding="utf-8")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_multiple_best_merge_bases_are_rejected_as_ambiguous(self) -> None:
        _git(self.repo, "checkout", "-qb", "left")
        (self.repo / "left.py").write_text("LEFT = 1\n", encoding="utf-8")
        _git(self.repo, "add", "left.py")
        _git(self.repo, "commit", "-qm", "left one")
        left_one = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-q", "main")
        _git(self.repo, "checkout", "-qb", "right")
        (self.repo / "right.py").write_text("RIGHT = 1\n", encoding="utf-8")
        _git(self.repo, "add", "right.py")
        _git(self.repo, "commit", "-qm", "right one")
        right_one = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-q", "left")
        _git(self.repo, "merge", "--no-ff", "-qm", "merge right one", right_one)
        _git(self.repo, "checkout", "-q", "right")
        _git(self.repo, "merge", "--no-ff", "-qm", "merge left one", left_one)

        result = _run(
            sys.executable,
            str(SCOPE_CLI),
            "branch",
            "--base",
            "left",
            "--head",
            "right",
            "--repo",
            str(self.repo),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("multiple best merge bases", result.stderr)

    def test_empty_text_changes_use_path_level_typed_anchors(self) -> None:
        empty_tracked = self.repo / "empty.txt"
        empty_tracked.write_bytes(b"")
        _git(self.repo, "add", "empty.txt")
        _git(self.repo, "commit", "-qm", "add empty file")
        empty_tracked.unlink()
        (self.repo / "new-empty.txt").write_bytes(b"")

        result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        records = {
            item["new_path"] or item["old_path"]: item
            for item in json.loads(result.stdout)["changed_records"]
        }
        self.assertEqual(
            records["new-empty.txt"]["anchor"],
            {"kind": "change", "change_type": "text", "path": "new-empty.txt"},
        )
        self.assertEqual(
            records["empty.txt"]["anchor"],
            {"kind": "change", "change_type": "deletion", "path": "empty.txt"},
        )

    def test_merge_commit_requires_an_explicit_parent(self) -> None:
        _git(self.repo, "checkout", "-qb", "side")
        (self.repo / "side.py").write_text("SIDE = True\n", encoding="utf-8")
        _git(self.repo, "add", "side.py")
        _git(self.repo, "commit", "-qm", "side change")
        _git(self.repo, "checkout", "-q", "main")
        (self.repo / "main.py").write_text("MAIN = True\n", encoding="utf-8")
        _git(self.repo, "add", "main.py")
        _git(self.repo, "commit", "-qm", "main change")
        _git(self.repo, "merge", "--no-ff", "-qm", "merge side", "side")
        merge_oid = _git(self.repo, "rev-parse", "HEAD").stdout.strip()

        ambiguous = _run(
            sys.executable,
            str(SCOPE_CLI),
            "commit",
            merge_oid,
            "--repo",
            str(self.repo),
        )
        self.assertEqual(ambiguous.returncode, 2)
        self.assertIn("select one explicitly", ambiguous.stderr)

        selected = _run(
            sys.executable,
            str(SCOPE_CLI),
            "commit",
            merge_oid,
            "--parent",
            "1",
            "--repo",
            str(self.repo),
        )
        self.assertEqual(selected.returncode, 0, selected.stderr)
        scope = json.loads(selected.stdout)
        self.assertEqual(scope["target"]["parent_count"], 2)
        self.assertEqual(scope["target"]["semantics"], "explicit-parent")

    def test_local_fingerprint_is_reproducible_and_changes_with_content(self) -> None:
        candidate = self.repo / "new.py"
        candidate.write_text("VALUE = 1\n", encoding="utf-8")

        first = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        second = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout)["scope_id"], json.loads(second.stdout)["scope_id"])

        candidate.write_text("VALUE = 2\n", encoding="utf-8")
        changed = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertNotEqual(json.loads(first.stdout)["scope_id"], json.loads(changed.stdout)["scope_id"])

    def test_contracts_allow_additive_fields_but_reject_false_coverage(self) -> None:
        (self.repo / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
        scope_result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        self.assertEqual(scope_result.returncode, 0, scope_result.stderr)
        scope = json.loads(scope_result.stdout)
        bundle_result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "build-one-unit",
            "--unit-id",
            "unit-new",
            "--feature-name",
            "New value",
            stdin=scope,
        )
        self.assertEqual(bundle_result.returncode, 0, bundle_result.stderr)
        bundle = json.loads(bundle_result.stdout)

        additive_scope = {**scope, "future_optional_field": {"kept": True}}
        additive = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "scope_manifest",
            stdin=additive_scope,
        )
        self.assertEqual(additive.returncode, 0, additive.stderr)
        self.assertEqual(json.loads(additive.stdout)["future_optional_field"], {"kept": True})

        unsupported = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "scope_manifest",
            stdin={**scope, "schema_version": "3.0"},
        )
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("unsupported", unsupported.stderr)

        incomplete_graph = copy.deepcopy(bundle["feature_graph"])
        incomplete_graph["ownership"].clear()
        invalid_graph = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "feature_graph",
            stdin=incomplete_graph,
        )
        self.assertEqual(invalid_graph.returncode, 2)
        self.assertIn("ownership", invalid_graph.stderr)

        false_ledger = copy.deepcopy(bundle["coverage_ledger"])
        false_ledger["records"][0]["covered"] = True
        invalid_ledger = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "coverage_ledger",
            stdin=false_ledger,
        )
        self.assertEqual(invalid_ledger.returncode, 2)
        self.assertIn("both roles", invalid_ledger.stderr)

        truthy_strings = copy.deepcopy(bundle["coverage_ledger"])
        for entry in truthy_strings["records"]:
            entry["role_status"] = {
                "internal_behavior": "completed",
                "external_contract": "completed",
            }
            entry["attempts"] = {
                "internal_behavior": 1,
                "external_contract": 1,
            }
            entry["covered"] = "false"
        truthy_strings["complete"] = "false"
        invalid_types = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "coverage_ledger",
            stdin=truthy_strings,
        )
        self.assertEqual(invalid_types.returncode, 2)
        self.assertIn("boolean", invalid_types.stderr)

        invalid_attempts = copy.deepcopy(bundle["coverage_ledger"])
        invalid_attempts["records"][0]["attempts"]["internal_behavior"] = -1
        bad_attempts = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "coverage_ledger",
            stdin=invalid_attempts,
        )
        self.assertEqual(bad_attempts.returncode, 2)
        self.assertIn("non-negative integer", bad_attempts.stderr)

    def test_completion_rejects_tampered_evidence_and_incomplete_scope(self) -> None:
        (self.repo / "one.py").write_text("ONE = 1\n", encoding="utf-8")
        (self.repo / "two.py").write_text("TWO = 2\n", encoding="utf-8")
        scope_result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        self.assertEqual(scope_result.returncode, 0, scope_result.stderr)
        scope = json.loads(scope_result.stdout)
        bundle_result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "build-one-unit",
            "--unit-id",
            "unit-values",
            "--feature-name",
            "Values",
            stdin=scope,
        )
        self.assertEqual(bundle_result.returncode, 0, bundle_result.stderr)
        bundle = json.loads(bundle_result.stdout)

        incomplete = copy.deepcopy(bundle)
        kept_id = incomplete["feature_graph"]["review_units"][0]["changed_record_ids"][0]
        incomplete["feature_graph"]["review_units"][0]["changed_record_ids"] = [kept_id]
        incomplete["feature_graph"]["ownership"] = {kept_id: "unit-values"}
        incomplete["coverage_ledger"]["records"] = [
            entry
            for entry in incomplete["coverage_ledger"]["records"]
            if entry["record_id"] == kept_id
        ]
        incomplete["coverage_ledger"]["uncovered_record_ids"] = [kept_id]
        for envelope in incomplete["reviewer_task_envelopes"]:
            envelope["changed_record_ids"] = [kept_id]
        incomplete_results = [
            {
                "schema_version": "2.0",
                "task_id": envelope["task_id"],
                "scope_id": envelope["scope_id"],
                "unit_id": envelope["unit_id"],
                "role": envelope["role"],
                "status": "completed",
                "inspected_record_ids": [kept_id],
                "candidate_findings": [],
                "supporting_evidence": [],
                "proposed_checks": [],
                "unresolved_assumptions": [],
            }
            for envelope in incomplete["reviewer_task_envelopes"]
        ]
        omitted = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "complete-one-unit",
            stdin={"bundle": incomplete, "reviewer_results": incomplete_results},
        )
        self.assertEqual(omitted.returncode, 2)
        self.assertIn("Scope Manifest", omitted.stderr)

        tampered = copy.deepcopy(bundle)
        tampered["reviewer_task_envelopes"][0]["core_policy"]["mutation_authority"] = "all"
        valid_results = [
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
        unsafe = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "complete-one-unit",
            stdin={"bundle": tampered, "reviewer_results": valid_results},
        )
        self.assertEqual(unsafe.returncode, 2)
        self.assertIn("core policy", unsafe.stderr)

        unrelated_anchor_results = copy.deepcopy(valid_results)
        unrelated_anchor_results[0]["candidate_findings"] = [
            {
                "finding_id": "finding-unrelated",
                "title": "Unrelated anchor",
                "changed_record_id": envelope["changed_record_ids"][0],
                "anchor": {
                    "kind": "line",
                    "side": "new",
                    "path": "unrelated.py",
                    "line": 999,
                },
                "changed_scope_causality": "Claimed changed cause",
                "reachable_scenario": "A caller reaches it",
                "impact": "Behavior breaks",
                "checked_context": "Callers checked",
                "severity": "high",
                "confidence": "high",
                "correction_direction": "Correct it",
            }
        ]
        unrelated = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "complete-one-unit",
            stdin={"bundle": bundle, "reviewer_results": unrelated_anchor_results},
        )
        self.assertEqual(unrelated.returncode, 2)
        self.assertIn("frozen Scope Manifest", unrelated.stderr)

    def test_reviewer_result_rejects_untyped_candidates_and_non_arrays(self) -> None:
        malformed = {
            "schema_version": "2.0",
            "task_id": "task-1",
            "scope_id": "scope-1",
            "unit_id": "unit-1",
            "role": "internal_behavior",
            "status": "completed",
            "inspected_record_ids": ["record-1"],
            "candidate_findings": [
                {
                    "finding_id": "finding-1",
                    "title": "Broken",
                    "changed_record_id": "record-1",
                    "anchor": "file.py:1",
                    "changed_scope_causality": 123,
                    "reachable_scenario": "reachable",
                    "impact": "impact",
                    "checked_context": "context",
                    "severity": "banana",
                    "confidence": "guess",
                    "correction_direction": "fix",
                }
            ],
            "supporting_evidence": "not-an-array",
            "proposed_checks": {},
            "unresolved_assumptions": None,
        }
        result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "reviewer_result",
            stdin=malformed,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("supporting_evidence", result.stderr)

        malformed["supporting_evidence"] = []
        malformed["proposed_checks"] = []
        malformed["unresolved_assumptions"] = []
        malformed["candidate_findings"][0]["changed_scope_causality"] = "caused"
        bad_anchor = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "reviewer_result",
            stdin=malformed,
        )
        self.assertEqual(bad_anchor.returncode, 2)
        self.assertIn("anchor", bad_anchor.stderr)

        malformed_ids = copy.deepcopy(malformed)
        malformed_ids["candidate_findings"] = []
        malformed_ids["inspected_record_ids"] = [{"not": "an id"}]
        bad_ids = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "reviewer_result",
            stdin=malformed_ids,
        )
        self.assertEqual(bad_ids.returncode, 2)
        self.assertIn("inspected_record_ids", bad_ids.stderr)

    def test_scope_manifest_requires_provenance_arrays(self) -> None:
        (self.repo / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
        scope_result = _run(
            sys.executable, str(SCOPE_CLI), "local", "--repo", str(self.repo)
        )
        self.assertEqual(scope_result.returncode, 0, scope_result.stderr)
        scope = json.loads(scope_result.stdout)
        del scope["trusted_requirement_sources"]
        del scope["candidate_validation_commands"]

        result = _run(
            sys.executable,
            str(CONTRACT_CLI),
            "validate",
            "--kind",
            "scope_manifest",
            stdin=scope,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate_validation_commands", result.stderr)
        self.assertIn("trusted_requirement_sources", result.stderr)


if __name__ == "__main__":
    unittest.main()
