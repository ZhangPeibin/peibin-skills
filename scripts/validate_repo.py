#!/usr/bin/env python3
"""Validate repository-level skill, plugin, documentation, and asset contracts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    import yaml
except ImportError as exc:
    raise SystemExit(
        "PyYAML is required; run `python3 -m pip install -r requirements-dev.txt`"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / ".claude-plugin" / "plugin.json"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")


class ValidationErrors:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, message: str) -> None:
        self.messages.append(message)

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.add(message)

    def exit_if_any(self) -> None:
        if not self.messages:
            return
        for message in self.messages:
            print(f"ERROR: {message}", file=sys.stderr)
        raise SystemExit(1)


def _load_yaml(path: Path, text: str | None = None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if text is None else text
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise TypeError("expected a YAML object")
    return value


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("missing YAML frontmatter")
    return _load_yaml(path, match.group(1))


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _validate_skill(skill_root: Path, errors: ValidationErrors) -> None:
    skill_path = skill_root / "SKILL.md"
    agent_path = skill_root / "agents" / "openai.yaml"
    is_personal = skill_root.is_relative_to(ROOT / "skills" / "personal")
    try:
        frontmatter = _frontmatter(skill_path)
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.add(f"{_relative(skill_path)}: {exc}")
        return

    name = frontmatter.get("name")
    errors.require(
        name == skill_root.name,
        f"{_relative(skill_path)}: name must match directory {skill_root.name!r}",
    )
    errors.require(
        isinstance(frontmatter.get("description"), str)
        and bool(frontmatter["description"].strip()),
        f"{_relative(skill_path)}: description must be non-empty",
    )
    if not agent_path.is_file():
        errors.add(f"{_relative(skill_root)}: missing agents/openai.yaml")
        return

    try:
        agent = _load_yaml(agent_path)
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        errors.add(f"{_relative(agent_path)}: {exc}")
        return
    interface = agent.get("interface")
    if not isinstance(interface, dict):
        errors.add(f"{_relative(agent_path)}: interface must be an object")
        return
    required_fields = ["display_name", "short_description"]
    if is_personal:
        required_fields.append("default_prompt")
    for field in required_fields:
        errors.require(
            isinstance(interface.get(field), str) and bool(interface[field].strip()),
            f"{_relative(agent_path)}: interface.{field} must be non-empty",
        )
    short_description = interface.get("short_description")
    if isinstance(short_description, str):
        errors.require(
            25 <= len(short_description) <= 64,
            f"{_relative(agent_path)}: short_description must contain 25-64 characters",
        )
    default_prompt = interface.get("default_prompt")
    if isinstance(name, str) and isinstance(default_prompt, str):
        errors.require(
            f"${name}" in default_prompt,
            f"{_relative(agent_path)}: default_prompt must mention ${name}",
        )
    if frontmatter.get("disable-model-invocation") is True:
        policy = agent.get("policy")
        errors.require(
            isinstance(policy, dict)
            and policy.get("allow_implicit_invocation") is False,
            f"{_relative(agent_path)}: explicit-only skill must disable implicit invocation",
        )


def _local_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    elif " " in target:
        target = target.split(" ", 1)[0]
    if not target or target.startswith("#"):
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return unquote(parsed.path)


def _validate_links(path: Path, errors: ValidationErrors) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.add(f"{_relative(path)}: cannot read Markdown: {exc}")
        return
    for raw_target in MARKDOWN_LINK_RE.findall(text):
        target = _local_target(raw_target)
        if target is None:
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            errors.add(f"{_relative(path)}: missing local link target {target!r}")


def _duplicate_registered_skill_names(
    plugin_names: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {
        name: paths
        for name, paths in sorted(plugin_names.items())
        if len(paths) > 1
    }


def main() -> int:
    errors = ValidationErrors()
    try:
        plugin = json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.add(f"{_relative(PLUGIN_PATH)}: {exc}")
        errors.exit_if_any()
        return 1

    raw_paths = plugin.get("skills")
    if not isinstance(raw_paths, list) or any(
        not isinstance(value, str) for value in raw_paths
    ):
        errors.add(".claude-plugin/plugin.json: skills must be an array of paths")
        errors.exit_if_any()
        return 1
    errors.require(
        len(raw_paths) == len(set(raw_paths)),
        ".claude-plugin/plugin.json: skill paths must be unique",
    )

    plugin_roots: list[Path] = []
    plugin_names: dict[str, list[str]] = {}
    for raw_path in raw_paths:
        skill_root = (ROOT / raw_path).resolve()
        if not skill_root.is_dir():
            errors.add(
                f".claude-plugin/plugin.json: missing skill directory {raw_path!r}"
            )
            continue
        plugin_roots.append(skill_root)
        _validate_skill(skill_root, errors)
        try:
            registered_name = _frontmatter(skill_root / "SKILL.md").get("name")
        except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError):
            continue
        if isinstance(registered_name, str):
            plugin_names.setdefault(registered_name, []).append(raw_path)

    for registered_name, registered_paths in _duplicate_registered_skill_names(
        plugin_names
    ).items():
        errors.add(
            ".claude-plugin/plugin.json: skill name "
            f"{registered_name!r} is registered more than once: "
            + ", ".join(registered_paths),
        )

    personal_roots = sorted(
        path.parent for path in (ROOT / "skills" / "personal").glob("**/SKILL.md")
    )
    missing_personal = set(personal_roots) - set(plugin_roots)
    for skill_root in sorted(missing_personal):
        errors.add(
            f"{_relative(skill_root)}: personal skill is not registered in plugin"
        )

    markdown_paths = {ROOT / "README.md", ROOT / "README.zh-CN.md"}
    for skill_root in personal_roots:
        markdown_paths.add(skill_root / "SKILL.md")
        references = skill_root / "references"
        if references.is_dir():
            markdown_paths.update(references.glob("**/*.md"))
    for path in sorted(markdown_paths):
        errors.require(path.is_file(), f"{_relative(path)}: file is missing")
        if path.is_file():
            _validate_links(path, errors)

    workflow_svg = ROOT / "docs" / "diagrams" / "skill-workflow.svg"
    workflow_png = ROOT / "docs" / "diagrams" / "skill-workflow.png"
    workflow_source = ROOT / "docs" / "diagrams" / "skill-workflow.excalidraw"
    for path in (workflow_svg, workflow_png, workflow_source):
        errors.require(
            path.is_file() and path.stat().st_size > 0,
            f"{_relative(path)}: asset is missing or empty",
        )
    if workflow_svg.is_file():
        errors.require(
            "tickets" in workflow_svg.read_text(encoding="utf-8"),
            f"{_relative(workflow_svg)}: workflow must include tickets",
        )
    if workflow_source.is_file():
        try:
            source = json.loads(workflow_source.read_text(encoding="utf-8"))
            element_ids = {
                element.get("id")
                for element in source.get("elements", [])
                if isinstance(element, dict)
            }
            errors.require(
                "tickets" in element_ids,
                f"{_relative(workflow_source)}: workflow must include tickets",
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.add(f"{_relative(workflow_source)}: {exc}")

    errors.exit_if_any()
    print(
        f"Validated {len(plugin_roots)} plugin skills, "
        f"{len(personal_roots)} personal skills, and {len(markdown_paths)} Markdown files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
