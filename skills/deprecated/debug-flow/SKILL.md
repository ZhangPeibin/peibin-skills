---
name: debug-flow
description: "This skill guides the users through a structured bug-fix workflow that uses rubber-duck to think the problem through and to-commits to write the final commit message. Use when the user has a bug and wants to walk the full path: understand it, fix it, then commit it."
disable-model-invocation: true
---

# Debug Flow

## Process
1. **Understand - Rubber-duck the bug.**
   - Enter this step when the user describes a bug but has not yet identified the root cause
   - Follow the rubber-duck skill. Do not invent your own debugging style.
   - Move to Step 2 when the user says they have identified the cause
2. **Wait - Let the user do the fix**
   - Enter this step when the user says they have identified the cause but has not yet fixed the bug.
   - Say `Good Luck, tell me if you have finished`
   - Do not write or suggest code changes for the user. The fix is the user's work.
   - Move to Step 3 when the user says they wants to start commit

3. **To Commits - Write commit messages**
   - Enter this step when the user wants to commit their fix.
   - Follow the to-commits skill. Do not invent your own commit style.
   - Flow ends when to-commits has produces a commit message for the user.


## Rules
1. Skip any step whose goal the user has already completed.
2. Don't make decisions for users; follow the process.
