---
name: init-flutter-app
description: Scaffold a new Flutter app from flutter-template (GitHub or local). Guides through source selection, collects project parameters, then runs the full setup — copy, rename, dependencies, and git init.
disable-model-invocation: true
---

# Init Flutter App

Your goal is to create a new Flutter app from the flutter-template by collecting the required
parameters, then executing the full setup sequence without error.

Do not proceed to the next step until the current one succeeds.

## Step 0 — Pre-flight Check

Before asking anything, verify the required tools are available:

```bash
git --version
dart --version
flutter --version
```

If any tool is missing, stop and tell the user exactly which ones are missing and how to fix it:
- `git`: https://git-scm.com/downloads
- `dart` / `flutter`: https://docs.flutter.dev/get-started/install

Also check that `rsync` is available if the user might choose local source (macOS/Linux have it by default; Windows may not).

## Step 1 — Collect Parameters

**1a.** Ask the user to choose the template source:
- `1` GitHub — provide a repo URL
- `2` Local — provide an absolute path to the template directory

Wait for the answer before continuing.

**1b.** Based on the answer, ask for:
- GitHub → "What is the GitHub repo URL?"
- Local → "What is the absolute path to the template directory?"

**1c.** Then ask for the remaining parameters in a single message:
- **project-name**: Dart package name, e.g. `my_app` (lowercase, underscores only)
- **display-name**: App name shown on launcher, e.g. `"My App"`
- **android-package**: e.g. `com.acme.my_app`
- **ios-bundle-id** _(optional — defaults to android-package with `_` → `-`)_

After the user replies, validate:
- `project-name` matches `^[a-z][a-z0-9_]*$`
- `android-package` has at least two dot-separated segments, e.g. `com.acme.my_app`
- Target directory `<cwd>/<project-name>` must not already exist

If any validation fails, tell the user what is wrong and ask them to correct it before proceeding.

## Step 2 — Copy Template

Run the appropriate command based on the source chosen in Step 1a.

**GitHub:**
```bash
git clone <url> <project-name> && rm -rf <project-name>/.git
```

**Local:**
```bash
rsync -a --exclude .git <template-path>/ <project-name>/
```

All subsequent commands run inside the project directory. Use `<project-name>` as a path prefix
or `cd` into it before running each command:
```bash
cd <project-name>
```

If either command fails, stop and show the error to the user. Do not proceed.

If any step after this point fails, tell the user to remove the partially created directory before retrying:
> Something went wrong. To retry from scratch, first run:
> ```bash
> rm -rf <project-name>
> ```

## Step 3 — Rename Project

**Without** `ios-bundle-id`:
```bash
dart run tool/setup_project.dart \
  --project-name <project-name> \
  --display-name "<display-name>" \
  --android-package <android-package>
```

**With** `ios-bundle-id`:
```bash
dart run tool/setup_project.dart \
  --project-name <project-name> \
  --display-name "<display-name>" \
  --android-package <android-package> \
  --ios-bundle-id <ios-bundle-id>
```

If the command fails, show the full error output and stop. Do not proceed.

## Step 4 — Install Dependencies

Run:
```bash
flutter clean
flutter pub get
```

If `flutter` is not found, stop and tell the user:
> `flutter` command not found. Make sure Flutter is installed and added to PATH.

If either command fails, show the full error output and stop.

## Step 5 — Initialize Git

Run:
```bash
dart run tool/init_git.dart --commit --message "Initial commit"
```

If the command fails, show the full error output and stop.

## Step 6 — Install Git Hooks

Check if lefthook is installed:
```bash
lefthook --version
```

If not found, ask the user:
> `lefthook` is not installed. It runs `flutter analyze` before every commit and `flutter test` before every push. Install it now via Homebrew? (y/n)

- If **yes** → run `brew install lefthook`, then continue
- If **no** → skip this step and tell the user they can run `make hooks` manually later

If installed (or just installed), run:
```bash
make hooks
```

If the command fails, show the full error output. This step is non-critical — tell the user they can retry manually with `make hooks`.

## Step 7 — Done

Tell the user:

> ✅ **<display-name>** is ready at `./<project-name>/`
>
> **Next steps:**
> - Open the project root in your IDE (not `android/` subfolder)
> - Replace the theme in `lib/app/theme/` with your brand colors
> - Update `API_BASE_URL` in `config/dev.json` to point to your backend
> - Remove `todo_demo` when you no longer need the API demo
> - Add your own features under `lib/features/`
> - When ready for production: copy `config/prod.example.json` → `config/prod.json` and fill in your values
