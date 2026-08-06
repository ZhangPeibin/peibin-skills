---
name: to-mermaid
description: Write Mermaid diagrams that render correctly in Markdown. Use when producing flowchart, sequence, or other Mermaid blocks — especially labels with file paths, URLs, special characters, or Mermaid reserved words.
---

# To-Mermaid

Write Mermaid diagrams that **render without parse errors**. The most common failure is unquoted node labels that contain `/`, `*`, or other characters Mermaid treats as syntax.

## Core rule

**Quote any node or message label that contains special characters.**

Use double quotes inside the brackets:

```
id["label text here"]
```

When in doubt, quote display labels. Quoting display text does not make an unsafe node or participant identifier valid.

## Characters that require quoting

Quote the label if it contains any of:

| Character | Why |
| --------- | --- |
| `/` | Triggers parallelogram shape `[/text/]`; leading `/` is especially dangerous |
| `*` | Wildcard; confuses some renderers |
| `.` | Dots in paths like `service.go` |
| `(` `)` | Conflicts with shape syntax like `[(database)]` |
| `[` `]` | Nested bracket ambiguity |
| `#` `;` | Statement / comment delimiters in some parsers |
| `@` | Used in some Mermaid directives |

Plain words with spaces are usually fine unquoted: `A[Core logic]`. File paths, URLs, and API routes are **not** — always quote those.

## Flowchart shapes with quoted labels

| Shape | Syntax | Example |
| ----- | ------ | ------- |
| Rectangle | `id["text"]` | `Handler["internal/handler/earn.go"]` |
| Rounded | `id("text")` | `API("POST /users")` — quote if `/` present: `API["POST /users"]` |
| Database / cylinder | `id[("text")]` | `DB[("earn_* tables")]` |
| Stadium | `id(["text"])` | `Cache(["redis:6379"])` |

**Parallelogram trap:** `[/text/]` is a shape, not a quoted path. Never write:

```
BAD:  InternalAPI[/internal/earn/settlements/run]
GOOD: InternalAPI["/internal/earn/settlements/run"]
```

## Sequence diagrams

Quote message text that contains `/`, `*`, or other special characters:

```mermaid
sequenceDiagram
  Caller->>Service: "POST /internal/earn/settlements/run"
  Service->>DB: "read earn_positions"
```

Arrow labels with only plain words need no quotes: `Caller->>Service: request`.

### Participant identifiers

Use simple technical identifiers for participants and actors: start with a letter, then use only letters, digits, or underscores. Keep display names separate with `as` when needed.

Do not use Mermaid sequence statement or control words as identifiers. They are parsed case-insensitively, so an identifier such as `Loop` is read as the `loop` statement instead of an actor. Avoid words such as `participant`, `actor`, `loop`, `alt`, `else`, `opt`, `par`, `and`, `critical`, `option`, `break`, `rect`, `end`, `note`, `activate`, `deactivate`, `autonumber`, `box`, `create`, and `destroy`.

**Bad** — `Loop` is parsed as a control keyword after the arrow:

```mermaid
sequenceDiagram
  Ledger-->>Loop: terminal events
```

**Good** — use a safe identifier and preserve the desired display name with an alias:

```mermaid
sequenceDiagram
  participant SettlementCycle as Loop
  Ledger-->>SettlementCycle: terminal events
```

## Good / bad examples

**Bad** — parse error (parallelogram + unclosed `/`):

```mermaid
flowchart TD
  Admin[Admin / Script] --> InternalAPI[/internal/earn/settlements/run]
```

**Good:**

```mermaid
flowchart TD
  Admin["Admin / Script"] --> InternalAPI["/internal/earn/settlements/run"]
```

**Bad** — `*` and `/` in unquoted labels:

```mermaid
flowchart TD
  Repo --> DB[(earn_* tables)]
  Scripts[scripts/earn_*.sh] --> DB
```

**Good:**

```mermaid
flowchart TD
  Repo --> DB[("earn_* tables")]
  Scripts["scripts/earn_*.sh"] --> DB
```

## Before finishing

1. Scan every node label for `/`, `*`, `.`, `(`, `)`. Quote any that have them.
2. Check no label starts with `[/` unless you intentionally want a parallelogram **and** close it with `/]`.
3. In sequence diagrams, check every participant identifier against Mermaid statement and control words case-insensitively; replace unsafe identifiers and retain their display names with `as`.
4. When the repository provides a Mermaid parser or renderer, run every diagram through it before finishing.
5. Keep diagrams small — a newcomer should grasp them at a glance.
