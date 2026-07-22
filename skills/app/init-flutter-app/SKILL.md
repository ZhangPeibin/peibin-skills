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

Do not check `rsync` yet — only check it in Step 1 if the user picks Local source.

## Step 1 — Collect Parameters

**1a.** Ask the user to choose the template source:
- `1` GitHub — provide a repo URL
- `2` Local — provide an absolute path to the template directory

Wait for the answer before continuing.

**1b.** Based on the answer, ask for:
- GitHub → "What is the GitHub repo URL?"
- Local → "What is the absolute path to the template directory?"

  If the user picks **Local**, also verify `rsync` is available before continuing:
  ```bash
  rsync --version
  ```
  If missing, stop and tell the user: "`rsync` is required for local source but was not found. Install it or choose the GitHub source instead."

  Also validate the provided path is absolute, the directory exists, and is readable:
  ```bash
  test -d "<template-path>" && test -r "<template-path>"
  ```
  If either check fails, stop and tell the user which check failed before proceeding.

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

## Step 1.5 — Template Contract Check

Before copying, verify the template contains the required files. For GitHub, clone into a temp path first; for Local, check in-place.

**GitHub** — run a shallow clone to a temp path to verify:
```bash
git clone --depth 1 <url> <project-name>-tmp
```
Then check:
```bash
test -f <project-name>-tmp/pubspec.yaml && \
test -f <project-name>-tmp/tool/setup_project.dart && \
test -f <project-name>-tmp/tool/init_git.dart
```

**Local** — check in the provided path:
```bash
test -f <template-path>/pubspec.yaml && \
test -f <template-path>/tool/setup_project.dart && \
test -f <template-path>/tool/init_git.dart
```

If any file is missing, stop immediately and tell the user which file is missing and that the source directory does not appear to be a valid flutter-template. Clean up the temp clone if one was created:
```bash
rm -rf <project-name>-tmp
```

Do not proceed to Step 2 until all three files are confirmed present.

## Step 2 — Copy Template

Run the appropriate command based on the source chosen in Step 1a.

**GitHub:**

The template was already cloned to `<project-name>-tmp` in Step 1.5. Rename it to the final directory:
```bash
mv <project-name>-tmp <project-name>
```

Then remove the `.git` directory (this is the only `.git` that should be deleted — the one just created by the clone above):
```bash
rm -rf <project-name>/.git
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

If any step after this point fails, tell the user to remove the partially created directory before retrying. Only suggest this command if the `<project-name>` directory was created during this session and did not exist before Step 2:
> Something went wrong. To retry from scratch, first remove the directory that was just created:
> ```bash
> rm -rf <project-name>
> ```
> Verify this is the right directory before running. If you are unsure, check `ls` first.

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
>
> Note: this will run `brew install lefthook` and modify your system environment.

- If **yes** → ask the user to run it themselves (do not run it without explicit confirmation):
  ```bash
  brew install lefthook
  ```
  Wait for the user to confirm it completed successfully before continuing.
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
