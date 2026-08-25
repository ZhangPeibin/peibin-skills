# Confirmation Result Contract

Read this reference only when producing a confirmation result.

## Confirmation result

`prepare-confirm` returns the exact ordered acceptance array. Return one entry per criterion in the same order:

```json
{
  "version": 1,
  "ticket": "01",
  "summary": "Concise independent verification summary.",
  "criteria": [
    {
      "criterion": "Exact criterion text from prepare-confirm.",
      "result": "PASS",
      "evidence": "Current command, artifact, or observation supporting the result.",
      "subchecks": [
        {
          "check": "One independently failing condition.",
          "result": "PASS",
          "evidence": "Current direct evidence for this condition."
        }
      ]
    }
  ]
}
```

Criterion and optional subcheck results are `PASS`, `FAIL`, or `BLOCKED`. Evidence is required; subcheck names are unique within their criterion, and the criterion result must equal their aggregate.

The result, criterion, and subcheck objects accept only the fields shown above. The complete UTF-8 result file must not exceed 64 KiB; summary is limited to 1,000 characters, each evidence value to 4,000 characters, each subcheck name to 200 characters, and each criterion to 20 subchecks. Whitespace-trimmed canonical fields are used for persistence and the idempotency digest.

Aggregation is `BLOCKED > FAIL > PASS`, first within subchecks and then across criteria. All PASS means verified completion; otherwise the ticket remains In Progress.

Evidence must be current, direct, and criterion-specific. Claims, old output, checklist marks, file existence alone, or test count are insufficient. Use BLOCKED when decisive evidence requires unavailable, independent, secret, approved, or unsafe access.

Confirmation input is temporary; only its canonical projection and completion state persist.
