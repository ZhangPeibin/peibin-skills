# Code Review v2 Release Process

## Purpose and readiness

This report records the independent hardening, remediation, scoring, and final
release qualification required by GitHub Issue #6. The independent scorer
evaluated implementation digest
`sha256:2de513032031337d5a8ee9bd7cf99e2bf38a5ba84e73649e763fb871898adcaa`
at 95/100, confirmed all 10 safety invariants, found no unresolved Critical or
High issue, and marked the implementation `release_ready`.

The process report is outside the Code Review skill implementation digest. The
post-score release launcher below is the final repository-level confirmation.

## Dependency chain

| Ticket | Commit | Delivered capability |
| --- | --- | --- |
| #2 | `7ec7c07` | Scope-safe paired review baseline |
| #3 | `e31c872`, `0a75e90` | Integration fixture and feature-graph hardening |
| #4 | `aeeeba6` | Resilient lifecycle and review lenses |
| #5 | `09835b9` | Executable release gates |
| #6 | This release commit, `fix(code-review): complete v2 release hardening` | Independent hardening, remediation, scoring, and release decision |

## Agents and coverage

- **Agent A:** verified and deduplicated reviewer findings, remediated the one
  confirmed High finding, added focused regressions, and ran the prescribed
  validation sequence.
- **Review lane 1:** correctness, evidence quality, and v1 behavioral
  compatibility.
- **Review lane 2:** safety, trust boundaries, sensitive-content handling, and
  containment.
- **Review lane 3:** scalability and budgets, extensibility and schema
  evolution, testability, and release-gate behavior.
- **Independent scorer:** reviewed the remediated tree and frozen executable
  evidence without implementing the change, then produced the final rubric
  scores, safety attestation, residual findings, and release decision.

Three independently scoped lane passes were accepted, with no iterative
expansion. A platform lifetime thread limit affected reviewer orchestration:
three lane passes completed, but the safety replacement reused an existing
isolated standard-reviewer context in a new turn because the fresh child result
was not deliverable. The replacement received the safety lane brief only; no
peer conclusions were supplied.

## Verified finding and remediation

One High finding was confirmed. `_likely_secret_path()` classified likely
secrets from leaf names but did not recognize sensitive parent-directory
components. Consequently, an untracked path such as
`secrets/production.json` could reach the contained-file reader, and the same
path could reach repository snapshot content hashing.

The shared classifier now normalizes parent components and treats exact
`secret`, `secrets`, `credential`, and `credentials` components as sensitive
before either scope collection or snapshot hashing can open file content.
Focused regressions cover both `secrets/production.json` and the nested
`config/secrets/production.json`, verifying their scope exclusion and proving
that changed tracked variants are not opened or hashed by snapshot traversal.

## Residual risks

- **Medium:** Independent evidence artifact schema stores only `severity` and
  `title`.
- **Medium:** Invalid resource ceilings are accepted.
- **Low:** Rename plus mode change is classified `rename_only`.

These risks were recorded rather than remediated under the fixed convergence
policy. There are no unresolved Critical or High findings.

## Verification evidence

- Focused secret-directory regressions: 2/2 passed.
- Skill quick validation: passed with `Skill is valid!`.
- Current-digest v1 semantic catalog: 15/15 passed.
- Prior executable release probes from Issue #5: 43/43 passed.
- Pre-score release launcher: repository validation passed, then the unit suite
  ran 210 tests and correctly stopped with one setup error because the
  independent evaluation still referenced the previous implementation digest.
- First post-score release launcher: repository validation passed, then the
  unit suite ran 210 tests and correctly rejected the unstaged independent
  evaluation artifact because it could not bind the artifact to its Git blob.
- Final staged-artifact release launcher: all 372 repository tests and all 43
  release probes passed; the executable gate confirmed the 95/100 score, all
  10 safety invariants, no unresolved Critical or High finding, and
  `release_ready`.
- Independent scorer digest:
  `sha256:2de513032031337d5a8ee9bd7cf99e2bf38a5ba84e73649e763fb871898adcaa`.
- Independent score: 95/100 and `release_ready`.

| Rubric category | Score |
| --- | ---: |
| Correctness and evidence | 24/25 |
| Safety | 20/20 |
| Scalability | 18/20 |
| Extensibility | 15/15 |
| v1 compatibility | 10/10 |
| Testability | 8/10 |
| **Total** | **95/100** |

All 10/10 safety invariants are true: report-only behavior, exact scope,
disclosure controls, path containment, no mutation, complete ownership,
reviewer isolation, honest full-pair claims, snapshot consistency, and honest
coverage gaps.

## Commands

Focused regressions:

```bash
python3 -I skills/personal/code-review/scripts/run_unittest.py run skills/personal/code-review/tests test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_common_secret_names_are_excluded_before_open
python3 -I skills/personal/code-review/scripts/run_unittest.py run skills/personal/code-review/tests test_v2_quality_gate.V2ReviewerHardeningRegressionTest.test_repository_snapshot_never_opens_tracked_likely_secrets
```

Skill validation:

```bash
UV_OFFLINE=1 uv run --isolated --no-project --no-config --no-env-file --with PyYAML==6.0.2 -- python3 /Users/peibin/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/personal/code-review
```

Release launcher, invoked once in each of the pre-score, post-score unstaged,
and final staged-artifact lifecycle states:

```bash
UV_OFFLINE=1 uv run --isolated --no-project --no-config --no-env-file --with PyYAML==6.0.2 -- python3 -I skills/personal/code-review/scripts/run_release_check.py
```

## Final gate result

- Final staged-artifact launcher: passed on its single authorized invocation.
- Repository validation: 19 plugin skills, 11 personal skills, and 23 Markdown
  files validated.
- Unit suites: Code Review 217/217 in 274.619 seconds, Handoff 88/88 in 0.413
  seconds, and Tickets 67/67 in 26.323 seconds; 372/372 total.
- Behavioral probes: 43/43 passed.
- Score and safety gate: 95/100, safety pass rate 1.0 (10/10 invariants), no
  unresolved Critical or High finding, and status `release_ready`.
- Release decision: Code Review v2 is ready to ship.
