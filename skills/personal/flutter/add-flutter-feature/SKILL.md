---
name: add-flutter-feature
description: Add a new feature to a project scaffolded from flutter-template the right way — scaffold the layered structure with the repo's generator, fill in domain/data/providers/presentation per the Agent Playbook, wire the route and i18n, and finish green on `make check`.
disable-model-invocation: true
---

# Add Flutter Feature

Your goal is to add one new feature to a project **scaffolded from
flutter-template** so that it is correct on the first pass: layered,
framework-boundary-clean, localized, and passing `make check`.

The package may have any name after `setup_project.dart` renamed it — this skill
identifies the project by its **template tooling**, not by its name.

The defining constraint: **the repository is the source of truth, not your
memory.** Conventions come from `docs/agent/agent-playbook.md` and are enforced
by `tool/architecture_check.dart`. Read the playbook before writing code, and
match the existing features' patterns rather than inventing new ones.

Do not proceed to the next step until the current one succeeds. Never edit
`*.g.dart` files by hand, and never weaken a checker, lint, or test to make code
pass.

## Step 0 — Preflight

Confirm the working directory is a project scaffolded from flutter-template.
From the project root:
```bash
test -f pubspec.yaml && \
test -f tool/new_feature.dart && \
test -f docs/agent/agent-playbook.md && \
test -f tool/architecture_check.dart
```
If any file is missing, stop and tell the user this skill only works inside a
project scaffolded from flutter-template (its package name may differ; what
matters is that the template's tooling — `tool/new_feature.dart`,
`docs/agent/agent-playbook.md` — is present). Create one first with the
`init-flutter-app` skill.

Then read `docs/agent/agent-playbook.md` in full — it is the canonical "how to"
for this repo. Also skim one existing feature under `lib/features/` (e.g.
`todo_demo`) to match its style.

## Step 1 — Understand the feature

Ask the user for what you cannot infer, in a single message:

- **Name** — snake_case, e.g. `user_profile` (must match `^[a-z][a-z0-9_]*$`).
- **What it does** — the user-facing behaviour and the primary screen(s).
- **Data source** — remote API (needs data/domain layers) or UI-only (no data).
  If remote: the endpoint path, the request/response shape, and any field-name
  differences between the API and your domain model.
- **Route** — the path (e.g. `/profile`) and whether it must be public
  (reachable while signed out) or protected (the default).

Restate the plan in one short paragraph and get a nod before scaffolding.

## Step 2 — Scaffold

Use the repo's generator — do not hand-create the directory tree. It produces a
compile-ready, format-clean skeleton that already passes
`tool/architecture_check.dart`.

Full vertical slice (has an API/data source):
```bash
dart run tool/new_feature.dart <name>
```
UI-only (no data layer):
```bash
dart run tool/new_feature.dart <name> --ui-only
```

If the feature already exists the generator refuses — stop and confirm the name
with the user. The generator prints the exact route snippet for Step 6; keep it.

## Step 3 — Fill the domain

In `lib/features/<name>/domain/<name>.dart`, replace the placeholder `id` field
with the real, immutable entity fields. Keep this file framework-free: no
Flutter, Riverpod, Dio, or imports from `data/`, `presentation/`, or
`providers/` (the architecture check enforces this).

Skip this step for `--ui-only` features.

## Step 4 — Fill the data layer

Skip for `--ui-only`. Otherwise, per the playbook's "add an API client +
repository + DTO" recipe:

- **DTO** (`data/dto/<name>_dto.dart`): map the real JSON in `fromJson`. When
  API field names differ from your Dart names, use `json['api_name']` in the
  hand-written DTO (the DTO mirrors the wire format); rename to domain vocabulary
  in `toDomain()`.
- **API** (`data/api/<name>_api.dart`): set the real endpoint path. **Keep the
  generated error boundary** — convert `DioException` into `ApiException` here so
  transport errors never leak into providers/presentation.
- **Repository** (`data/repositories/<name>_repository.dart`): map DTO → domain
  so upper layers only see domain types.

## Step 5 — Fill providers

In `providers/<name>_providers.dart`, adjust the provider signatures/parameters
to the real repository call. Read shared dependencies (Dio, config, storage,
logger) through the existing `core/providers/*`; do not construct them inline or
add service locators / global mutable state.

## Step 6 — Build the presentation and wire the route

- Implement the UI in `presentation/<name>_page.dart`. Reuse `core/widgets/`
  (`AppScaffold`, `AppButton`, `AppAsyncView`, `AppEmptyView`, …) and theme
  tokens from `lib/app/theme/`. Presentation must not import `dio`,
  `shared_preferences`, or `flutter_secure_storage` directly.
- Wire the route in `lib/app/router/app_router.dart` using the snippet the
  generator printed. The route identifiers are camelCase (e.g. feature
  `user_profile` → `AppRoutePaths.userProfile` / `AppRouteNames.userProfile`):
  add entries to `AppRoutePaths`, `AppRouteNames`, and `routes`. Navigate by name
  (`context.pushNamed(AppRouteNames.<camelName>)`), never a hardcoded string. If
  the route must be reachable while signed out, add it to `_publicRoutePaths`;
  otherwise leave it protected.

## Step 7 — Localize

Move every user-visible string into **all** locale files
(`lib/i18n/en.i18n.json` and `lib/i18n/zh.i18n.json`), then regenerate:
```bash
make gen
```
Use the keys via `context.t.<feature>.<key>`. Never edit `strings.g.dart` by
hand. Platform-owned values and non-user-facing diagnostics may stay as literals.

## Step 8 — Test

Per the playbook's testing recipe, add tests under `test/`:

- Unit-test domain rules, DTO parsing, the repository, and providers.
- Widget-test the meaningful UI states (loading / empty / error / success) and
  key interactions.
- Tests must be deterministic: no real network, credentials, wall-clock timing,
  or execution-order dependencies.

## Step 9 — Validate

Run the full non-mutating check set (same as CI and the git hooks):
```bash
make check
```
Use `make fix` for mechanical format/lint/gen fixes, then review each change.

- If it passes, the feature is done.
- If it fails, show the output and fix the code. Common causes: missing trailing
  commas / formatting (`make fix`), stale generated i18n (`make gen`), or an
  architecture-boundary violation (move the offending import to the right layer —
  do not silence the checker).

Do not finish until `make check` is green. Report any check that could not run.

## Step 10 — Done

Summarize for the user:

> ✅ Feature **<name>** added.
>
> - Route: `<path>` (`AppRouteNames.<camelName>`), <public|protected>
> - Files under `lib/features/<name>/` and tests under `test/`
> - `make check` passing
>
> Next: navigate to it from a real entry point (button/menu), and replace any
> remaining `TODO(<name>)` placeholders.
