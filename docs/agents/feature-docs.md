# Feature Docs

Pre-PRD requirements discussions live at:

`docs/feature/<release>/<feature-slug>/discussion.md`

Formal PRDs and implementation tickets live in the issue tracker described by `docs/agents/issue-tracker.md`.

## Consumer rules

- `requirements-analysis` creates or resumes `discussion.md`.
- The user chooses `<release>`.
- Derive `<feature-slug>` from the feature name.
- Preserve existing decisions and session history.
- Store structured analysis, not a verbatim transcript.
- Set status to `ready-for-prd` only after explicit confirmation.
- Do not create `prd.md` here; `$to-spec` publishes the formal PRD.
