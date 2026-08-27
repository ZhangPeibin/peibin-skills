# Structured Output Contract

Every handled invocation writes one compact JSON object to stdout. Local Markdown responses remain schema version 2. GitHub responses use schema version 3. Reject unsupported versions and do not infer state from prose.

## Envelope

All responses contain exactly `"schema_version"`, `"command"`, `"outcome"`, `"ticket"`, `"changed"`, `"data"`, `"next"`, and `"error"`.

`outcome` is semantic, `ticket` is the selected id or null, and `changed` means authoritative ticket state changed. `data` is command-specific and `{}` on error. `next` is one recommended `{command, reason}` or null, never automatic authorization. `error` is null on success.

## Outcomes And Data

Successful `data` objects contain exactly these keys in schema version 2:

| Command | Outcomes | Exact `data` keys |
|---|---|---|
| `status` | `ACTIVE`, `READY`, `COMPLETE`, `DEADLOCK` | `active`, `frontier`, `resumable`, `roadmap`, `by_state`, `blocked_by`, `waits_for`, `handoffs`, `completion_methods` |
| `next` | `WORK_PACKET` | `roadmap`, `work_packet` |
| `pause` | `PAUSED` | `reason`, `waits_for`, `handoff`, `roadmap`, `frontier` |
| `done` | `DONE` | `completion`, `roadmap`, `frontier`, `resumable` |

Roadmap data contains `state`, `changed`, and `created`, plus `error` only when synchronization fails.

## Work Packet

The Work Packet contains only `source`, `ticket`, `objective`, `acceptance_criteria`, `boundaries`, and `history`.

- `ticket`: id, title, current state, and published blockers.
- `objective` and ordered `acceptance_criteria`.
- `boundaries`: implementation and control-plane limits.
- `history`: last pause, last confirmation, and bound handoff path; values may be null.

Schema 2 local `source` contains exactly `feature_root`, `repository_root`, and `ticket_path`.

Schema 3 GitHub `source` contains exactly `tracker`, `feature_root`, `repository_root`, `repository`, `feature_issue`, and `ticket_url`. `tracker` is `github`; the feature root contains only derived roadmap and handoff data. Do not represent an Issue URL as a filesystem path or add aliases such as `objective` plus `what_to_build`.

After `next`, the recommendation is `$implement`; the controller does not invoke it. `done` stores `completion = {method: "explicit-done", note}` and makes no verification claim.

## Errors

Errors exit nonzero with `outcome = ERROR`, empty `data`, null `next`, and `error = {code, message, details}`. The code is stable for automation and details is always an object.

Invalid arguments, missing required arguments, and unknown commands use `error.code = USAGE_ERROR`; they are emitted on stdout in this same envelope rather than as argparse prose on stderr.

Expected OS, encoding, or process failures use `ENVIRONMENT_ERROR`. If authoritative ticket state was written but the controller could not reload the Feature, it uses `POST_COMMIT_ERROR`, sets `changed` to true, and identifies the ticket.

For GitHub, any successful remote step followed by a later assignment, comment, cleanup, close, reload, or concurrency failure uses `POST_COMMIT_ERROR`. A conflicting claim is rolled back when possible and reports the recovery result in `details`. Authentication, network, and unavailable `gh` failures before any write use `ENVIRONMENT_ERROR`; they never fall back to local tickets.

Invalid ticket transitions use stable codes including `TICKET_NOT_ACTIVE`, `TICKET_PAUSED`, and `TICKET_NOT_READY`; details include the current state needed for recovery.
