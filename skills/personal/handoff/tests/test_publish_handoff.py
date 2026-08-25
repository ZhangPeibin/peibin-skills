from __future__ import annotations

import errno
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_handoff.py"
VALIDATOR = SCRIPT.parent / "validate_handoff.py"
FIXTURE = Path(__file__).parent / "fixtures" / "valid-handoff.md"
COMPACT_FIXTURE = Path(__file__).parent / "fixtures" / "valid-compact-handoff.md"
SPEC = importlib.util.spec_from_file_location("publish_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
publish_handoff = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT.parent))
SPEC.loader.exec_module(publish_handoff)
sys.path.remove(str(SCRIPT.parent))

CONTENT = FIXTURE.read_bytes()
COMPACT_CONTENT = COMPACT_FIXTURE.read_bytes()


class FailingStream:
    def __init__(self, wrapped: object, operation: str):
        self.wrapped = wrapped
        self.operation = operation

    def __enter__(self) -> FailingStream:
        return self

    def __exit__(self, *args: object) -> None:
        self.wrapped.close()

    def write(self, content: bytes) -> int:
        if self.operation == "write":
            raise OSError("write failed")
        return self.wrapped.write(content)

    def flush(self) -> None:
        if self.operation == "flush":
            raise OSError("flush failed")
        self.wrapped.flush()

    def fileno(self) -> int:
        return self.wrapped.fileno()


class PublishHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_no_temporary_files(self) -> None:
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_invalid_artifact_does_not_create_default_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            publish_handoff.publish_handoff(
                b"not a handoff", temp_directory=self.root
            )

        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not portable")
    def test_compact_artifact_uses_the_same_private_publisher(self) -> None:
        path = publish_handoff.publish_handoff(
            COMPACT_CONTENT, temp_directory=self.root
        )

        self.assertEqual(path.parent, self.root)
        self.assertEqual(path.read_bytes(), COMPACT_CONTENT)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assert_no_temporary_files()

    def test_invalid_artifact_with_force_preserves_explicit_target(self) -> None:
        target = self.root / "existing.md"
        target.write_bytes(b"old")
        invalid = CONTENT.replace(b'handoff_schema: "1.0"', b'handoff_schema: "9.0"')

        with self.assertRaisesRegex(ValueError, "handoff_schema"):
            publish_handoff.publish_handoff(invalid, target, force=True)

        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_invalid_heading_never_creates_or_replaces_target(self) -> None:
        invalid = CONTENT.replace(b"## Next Action", b"## Next Action###", 1)
        with self.assertRaisesRegex(ValueError, "Next Action"):
            publish_handoff.publish_handoff(
                invalid, temp_directory=self.root
            )
        self.assertEqual(list(self.root.iterdir()), [])

        target = self.root / "existing.md"
        target.write_bytes(b"old")
        with self.assertRaisesRegex(ValueError, "Next Action"):
            publish_handoff.publish_handoff(invalid, target, force=True)
        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_markdown_structure_bypasses_fail_before_any_publish(self) -> None:
        indented = CONTENT.replace(b"Coding details.", b" ## Next Action\n\nInjected.")
        setext = CONTENT.replace(
            b"Coding details.", b"Next Action\n-----------\n\nInjected."
        )
        container = CONTENT.replace(
            b"Coding details.", b"> ## Next Action\n\nInjected."
        )
        invalid_fence = CONTENT.replace(
            b"Coding details.", b"```bad`info\n## Next Action\n```"
        )

        with self.assertRaisesRegex(ValueError, "strict Markdown subset"):
            publish_handoff.publish_handoff(
                indented, temp_directory=self.root
            )
        self.assertEqual(list(self.root.iterdir()), [])

        explicit = self.root / "explicit.md"
        with self.assertRaisesRegex(ValueError, "strict Markdown subset"):
            publish_handoff.publish_handoff(setext, explicit)
        self.assertFalse(explicit.exists())

        existing = self.root / "existing.md"
        existing.write_bytes(b"old")
        for content in (container, invalid_fence):
            with self.subTest(content=content), self.assertRaises(ValueError):
                publish_handoff.publish_handoff(content, existing, force=True)
            self.assertEqual(existing.read_bytes(), b"old")
            self.assert_no_temporary_files()

    def test_structure_bypass_cli_failures_are_diagnostic_and_non_mutating(self) -> None:
        cases = (
            CONTENT.replace(b"Coding details.", b"   ## Next Action\n\nInjected."),
            CONTENT.replace(
                b"Coding details.", b"Next Action\n-----------\n\nInjected."
            ),
            CONTENT.replace(b"Coding details.", b"- ## Next Action\n\nInjected."),
            CONTENT.replace(
                b"Coding details.", b"```bad`info\n## Next Action\n```"
            ),
        )
        target = self.root / "existing.md"
        target.write_bytes(b"old")
        for content in cases:
            with self.subTest(content=content):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--output",
                        str(target),
                        "--force",
                    ],
                    input=content,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"publish_handoff:", result.stderr)
                self.assertEqual(target.read_bytes(), b"old")
                self.assert_no_temporary_files()

    def test_deeper_heading_cli_failure_preserves_existing_target(self) -> None:
        invalid = CONTENT.replace(
            b"Coding details.", b"#### Next Action\n\nInjected."
        )
        target = self.root / "existing.md"
        target.write_bytes(b"old")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(target),
                "--force",
            ],
            input=invalid,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"strict Markdown subset", result.stderr)
        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_raw_html_heading_cli_failure_preserves_existing_target(self) -> None:
        invalid = CONTENT.replace(
            b"Coding details.", b"<h2>Next Action</h2>"
        )
        target = self.root / "existing.md"
        target.write_bytes(b"old")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(target),
                "--force",
            ],
            input=invalid,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"strict Markdown subset", result.stderr)
        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_multiline_raw_html_heading_fails_before_publish(self) -> None:
        invalid = CONTENT.replace(
            b"Coding details.", b"<h2\n>\nNext Action\n</h2\n>"
        )
        target = self.root / "existing.md"
        target.write_bytes(b"old")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(target),
                "--force",
            ],
            input=invalid,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"strict Markdown subset", result.stderr)
        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_publisher_and_standalone_validator_accept_same_fixture(self) -> None:
        validation = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=CONTENT,
            capture_output=True,
            check=False,
        )
        target = self.root / "validated.md"
        publication = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(target)],
            input=CONTENT,
            capture_output=True,
            check=False,
        )

        self.assertEqual(validation.returncode, 0, validation.stderr.decode())
        self.assertEqual(publication.returncode, 0, publication.stderr.decode())
        self.assertEqual(target.read_bytes(), CONTENT)

    def test_default_path_uses_temp_directory_and_64_bit_name(self) -> None:
        path = publish_handoff.publish_handoff(CONTENT, temp_directory=self.root)

        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent, self.root)
        self.assertRegex(
            path.name, r"^handoff-\d{8}-\d{6}-[0-9a-f]{16}\.md$"
        )
        self.assertEqual(path.read_bytes(), CONTENT)

    def test_default_names_are_unique_under_concurrency(self) -> None:
        count = 32
        with ThreadPoolExecutor(max_workers=8) as executor:
            paths = list(
                executor.map(
                    lambda _: publish_handoff.publish_handoff(
                        CONTENT, temp_directory=self.root
                    ),
                    range(count),
                )
            )

        self.assertEqual(len(set(paths)), count)
        self.assertTrue(all(path.read_bytes() == CONTENT for path in paths))
        self.assert_no_temporary_files()

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not portable")
    def test_published_file_is_private(self) -> None:
        path = publish_handoff.publish_handoff(CONTENT, temp_directory=self.root)

        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_explicit_new_target_is_published_without_replace(self) -> None:
        target = self.root / "chosen.md"

        with (
            mock.patch.object(
                publish_handoff.os, "link", wraps=os.link
            ) as link,
            mock.patch.object(
                publish_handoff.os, "replace", wraps=os.replace
            ) as replace,
        ):
            result = publish_handoff.publish_handoff(CONTENT, target)

        self.assertEqual(result, target)
        self.assertEqual(target.read_bytes(), CONTENT)
        link.assert_called_once()
        replace.assert_not_called()

    def test_existing_target_without_force_is_preserved_and_rejected(self) -> None:
        target = self.root / "chosen.md"
        target.write_bytes(b"old")

        with self.assertRaisesRegex(
            FileExistsError, "output already exists.*--force"
        ):
            publish_handoff.publish_handoff(CONTENT, target)

        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_existing_target_with_force_is_replaced_atomically(self) -> None:
        target = self.root / "chosen.md"
        target.write_bytes(b"old")

        with mock.patch.object(
            publish_handoff.os, "replace", wraps=os.replace
        ) as replace:
            result = publish_handoff.publish_handoff(CONTENT, target, force=True)

        self.assertEqual(result, target)
        self.assertEqual(target.read_bytes(), CONTENT)
        replace.assert_called_once()
        temporary, destination = map(Path, replace.call_args.args)
        self.assertEqual(temporary.parent, target.parent)
        self.assertEqual(destination, target)
        self.assertTrue(temporary.name.endswith(".tmp"))

    def test_force_requires_explicit_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires.*--output"):
            publish_handoff.publish_handoff(
                CONTENT, force=True, temp_directory=self.root
            )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_and_whitespace_stdin_are_rejected(self) -> None:
        for content in (b"", b" \n\t"):
            with self.subTest(content=content), self.assertRaisesRegex(
                ValueError, "non-blank"
            ):
                publish_handoff.publish_handoff(
                    content, temp_directory=self.root
                )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_relative_explicit_path_is_rejected_without_writing(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute path"):
            publish_handoff.publish_handoff(CONTENT, Path("handoff.md"))

        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_output_directory_is_rejected(self) -> None:
        target = self.root / "missing" / "handoff.md"

        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            publish_handoff.publish_handoff(CONTENT, target)

        self.assertFalse(target.exists())

    def test_write_and_flush_failures_clean_temporary_file(self) -> None:
        real_fdopen = os.fdopen
        for operation in ("write", "flush"):
            with self.subTest(operation=operation):
                target = self.root / f"{operation}.md"

                def failing_fdopen(fd: int, mode: str) -> FailingStream:
                    return FailingStream(real_fdopen(fd, mode), operation)

                with (
                    mock.patch.object(
                        publish_handoff.os,
                        "fdopen",
                        side_effect=failing_fdopen,
                    ),
                    self.assertRaisesRegex(OSError, f"{operation} failed"),
                ):
                    publish_handoff.publish_handoff(CONTENT, target)
                self.assertFalse(target.exists())
                self.assert_no_temporary_files()

    def test_file_fsync_failure_cleans_temporary_file(self) -> None:
        target = self.root / "fsync.md"

        with (
            mock.patch.object(
                publish_handoff.os,
                "fsync",
                side_effect=OSError("fsync failed"),
            ),
            self.assertRaisesRegex(OSError, "fsync failed"),
        ):
            publish_handoff.publish_handoff(CONTENT, target)

        self.assertFalse(target.exists())
        self.assert_no_temporary_files()

    def test_replace_failure_preserves_existing_file_and_cleans_temp(self) -> None:
        target = self.root / "chosen.md"
        target.write_bytes(b"old")

        with mock.patch.object(
            publish_handoff.os, "replace", side_effect=OSError("replace failed")
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                publish_handoff.publish_handoff(CONTENT, target, force=True)

        self.assertEqual(target.read_bytes(), b"old")
        self.assert_no_temporary_files()

    def test_unsupported_hard_link_is_diagnostic_and_cleans_temp(self) -> None:
        target = self.root / "unsupported.md"
        error = OSError(errno.EOPNOTSUPP, "hard links are not supported")

        with (
            mock.patch.object(publish_handoff.os, "link", side_effect=error),
            self.assertRaisesRegex(OSError, "hard links are not supported"),
        ):
            publish_handoff.publish_handoff(CONTENT, target)

        self.assertFalse(target.exists())
        self.assert_no_temporary_files()

    def test_cli_writes_only_absolute_path_to_stdout(self) -> None:
        target = self.root / "cli.md"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(target)],
            input=CONTENT,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode(), f"{target}\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(target.read_bytes(), CONTENT)

    def test_cli_force_replaces_existing_target(self) -> None:
        target = self.root / "cli.md"
        target.write_bytes(b"old")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(target),
                "--force",
            ],
            input=CONTENT,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode(), f"{target}\n")
        self.assertEqual(target.read_bytes(), CONTENT)

    def test_cli_failures_have_empty_stdout_and_diagnostic_stderr(self) -> None:
        target = self.root / "existing.md"
        target.write_bytes(b"old")
        cases = (
            (["--output", str(self.root / "invalid.md")], b"\xff"),
            (["--output", str(target)], CONTENT),
            (["--force"], CONTENT),
            ([], b" \n"),
        )
        for arguments, content in cases:
            with self.subTest(arguments=arguments, content=content):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *arguments],
                    input=content,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertTrue(result.stderr.strip())
        self.assertEqual(target.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
