# Structured Output Contract

Every handled invocation writes one compact JSON object to stdout. Reject unsupported schema versions and do not infer state from prose.

## Envelope

All responses contain exactly `"schema_version"`, `"command"`, `"outcome"`, `"ticket"`, `"changed"`, `"data"`, `"next"`, and `"error"`.

`schema_version` is 1. `outcome` is semantic, `ticket` is the selected id or null, and `changed` means authoritative ticket state changed. `data` is command-specific and `{}` on error. `next` is one recommended `{command, reason}` or null, never automatic authorization. `error` is null on success.

## Outcomes And Data

Successful `data` objects contain exactly these keys in schema version 1:

| Command | Outcomes | Exact `data` keys |
|---|---|---|
| `status` | `ACTIVE`, `READY`, `COMPLETE`, `DEADLOCK` | `active`, `frontier`, `resumable`, `roadmap`, `by_state`, `blocked_by`, `waits_for`, `handoffs`, `completion_methods` |
| `next` | `WORK_PACKET` | `roadmap`, `work_packet` |
| `pause` | `PAUSED` | `reason`, `waits_for`, `handoff`, `roadmap`, `frontier` |
| `prepare-confirm` | `CONFIRMATION_REQUEST` | `repository_root`, `ticket_path`, `title`, `objective`, `acceptance_criteria` |
| `confirm` | `PASS`, `FAIL`, `BLOCKED` | `summary`, `criteria`, `roadmap`, `frontier`, `resumable` |
| `pass` | `PASS` | `completion`, `roadmap`, `frontier`, `resumable` |

Roadmap data contains `state`, `changed`, and `created`, plus `error` only when synchronization fails.

## Work Packet

The Work Packet contains only:

- `source`: feature root, repository root, and authoritative ticket path.
- `ticket`: id, title, current state, and published blockers.
- `objective` and ordered `acceptance_criteria`.
- `boundaries`: implementation and control-plane limits.
- `history`: last pause, last confirmation, and bound handoff path; values may be null.
- `confirm`: exact skill command to run after implementation.

Do not add aliases such as `path` plus `ticket_path`, or `objective` plus `what_to_build`.

## Errors

Errors exit nonzero with `outcome = ERROR`, empty `data`, null `next`, and `error = {code, message, details}`. The code is stable for automation and details is always an object.

Invalid arguments, missing required arguments, and unknown commands use `error.code = USAGE_ERROR`; they are emitted on stdout in this same envelope rather than as argparse prose on stderr.

Expected OS, encoding, or process failures use `ENVIRONMENT_ERROR`. If authoritative ticket state was written but the controller could not reload the Feature, it uses `POST_COMMIT_ERROR`, sets `changed` to true, and identifies the ticket.
