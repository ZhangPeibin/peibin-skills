#!/usr/bin/env python3
"""Validate and atomically publish handoff Markdown without accidental overwrite."""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Sequence

from validate_handoff import validate_handoff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Atomically publish UTF-8 handoff Markdown read from stdin."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Explicit absolute output path; defaults to the OS temp directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing explicit --output target atomically.",
    )
    return parser


def _default_target(directory: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return directory / f"handoff-{timestamp}-{secrets.token_hex(8)}.md"


def _private_fd(fd: int) -> None:
    try:
        os.fchmod(fd, 0o600)
    except (AttributeError, NotImplementedError, OSError):
        pass


def _private_path(path: Path) -> None:
    try:
        path.chmod(0o600)
    except (NotImplementedError, OSError):
        pass


def _sync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _publish_no_clobber(temporary_path: Path, target: Path) -> None:
    """Atomically expose temporary_path at target only when target is absent."""
    os.link(temporary_path, target)
    try:
        temporary_path.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            target.unlink()
        raise


def _atomic_write(target: Path, content: bytes, *, force: bool) -> None:
    fd = -1
    temporary_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary_path = Path(raw_path)
        _private_fd(fd)
        stream = os.fdopen(fd, "wb")
        fd = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary_path, target)
        else:
            _publish_no_clobber(temporary_path, target)
        temporary_path = None
        _private_path(target)
        _sync_directory(target.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary_path is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()


def publish_handoff(
    content: bytes,
    output: Path | None = None,
    *,
    force: bool = False,
    temp_directory: Path | None = None,
) -> Path:
    """Validate, publish, and return the final absolute path."""
    validate_handoff(content)
    if force and output is None:
        raise ValueError("--force requires an explicit --output path")

    if output is not None:
        if not output.is_absolute():
            raise ValueError("--output must be an absolute path")
        target = output.absolute()
        if not target.parent.is_dir():
            raise FileNotFoundError(
                f"output directory does not exist: {target.parent}"
            )
        try:
            _atomic_write(target, content, force=force)
        except FileExistsError as exc:
            raise FileExistsError(
                f"output already exists: {target}; pass --force only when replacement is intended"
            ) from exc
        return target

    directory = (temp_directory or Path(tempfile.gettempdir())).absolute()
    if not directory.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {directory}")
    for _ in range(100):
        target = _default_target(directory)
        try:
            _atomic_write(target, content, force=False)
        except FileExistsError:
            continue
        return target
    raise FileExistsError("could not allocate a unique handoff filename")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path = publish_handoff(
            sys.stdin.buffer.read(), args.output, force=args.force
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"publish_handoff: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
