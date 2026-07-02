---
name: to-commit
description:  Turn the user's current uncommitted changes into a clear Conventional Commits message.
---

# to-commit

Turn the current chnage into one clean, Conventional Commits message.

## Process 

1. **See what changed.**  If the user hasn't shared it. ask them to paste `git diff` (staged or unstaged, whichever they intent to commit)
2. **Pick the type.** Exactly one of: 
   - `feat`: new user-visible capability
   - `fix`: bug fix
   - `refactor`: internal restructure, no behavior change
   - `docs`: documentation only
   - `test`: adding or fixing tests
   - `chore`: tooling, deps, config, non-code
3. **Write the subject.** Format: `<type>: <imperative verb> <what>.` Under 72 characters. Imperative mood.
4. **Add a body only if the "why" is not obvious from the diff.** The body explains **why**, not **what**. Wrap at ~72 columns.
5. **One logical change per commit.** If the diff spans unrelated changes, stop and suggest splitting before writing the message.

## Good / Bad examples
- Good: `fix: prevent race condition in login flow`
- Bad: `fixed login bug` — past tense, missing type
- Good: `refactor: extract auth guard into middleware`
- Bad: `feat: added a lot of stuff` — vague, past tense
- Good: `chore: bump eslint to 9.0`
- Bad: `update dependencies` — missing type, unclear which


## Output
Present the final message in a fenced code block so the user can copy-paste it directly, then one sentence explaining your choice of type.

## Boundaries

1. **Do not run any state-modifying git command.** This includes `git add`, `git commit`, `git push`, `git reset`, `git checkout`, `git stash`, `git rebase`, `git merge`.
2. **Do run read-only git commands to gather context:** `git diff`, `git status`, `git log`, `git show`. These are safe because they do not modify the repository.
3. **Output is text, not action.** The output describes the commit; it never performs one.
4. **Never create, modify, or delete any file.** This includes CHANGELOG.md, README updates, or anything outside git's tracked state.

## Rules
- **Imperative mood only.** `fix`, not `fixes` or `fixed`.
- **No emoji, no trailing period on the subject line.**
- **Never invent facts.** If you can't tell what a hunk does, ask before guessing.