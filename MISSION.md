# Mission: Maintain a reliable AI Agent skill library

## Purpose

Build a curated collection of predictable, reusable Skills for day-to-day software engineering. Personal Skills are maintained here; vendor Skills retain clear provenance and are enabled only when they add value to the active workflow.

## Principles

- Keep Skill instructions focused, explicit about permissions, and grounded in repository facts.
- Keep personal and vendor ownership boundaries visible.
- Treat the plugin manifest, catalog, and UI metadata as one synchronized public interface.
- Use tests and deterministic scripts when behavior is stateful, destructive, or otherwise fragile.
- Prefer a small, deliberate active Skill set over enabling every collected Skill.

## Success looks like

- Every personal Skill has valid frontmatter and consistent OpenAI UI metadata.
- Every registered plugin path exists, and the README accurately describes the enabled workflow.
- Vendor sources record their upstream revision and license.
- Generated files do not dirty the working tree, and obsolete project-level guidance is removed promptly.
