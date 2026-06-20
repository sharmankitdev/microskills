---
name: synthesize-review
base: true
description: >-
  Use when you have verified code-review findings and need them consolidated into
  one report. Joins per-finding verdicts to their findings by id, drops
  refuted or false-positive findings, merges duplicates that share file, line,
  and root cause, re-ranks survivors by severity with blockers first, and maps
  the surviving severities to an overall verdict. Produces a single JSON object
  carrying the verdict, blocker count, a one-line summary, a severity-grouped
  markdown report, and the merged surviving findings.
---

# Synthesize Review

## Purpose

Given a change summary, dimension-tagged findings, and per-finding verifications, join, filter, deduplicate, and severity-rank the findings to produce one review report object carrying an overall verdict, blocker count, and rendered markdown.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| change_summary | yes | object | The diff summary produced by summarize-diff; sources the report's change overview. | — |
| findings | yes | array | The collected dimension-tagged findings produced by collect-findings. | — |
| verifications | yes | array | The per-finding verdicts produced by verify-finding, each carrying a finding_id (the matching finding's id), verdict, and false_positive flag. | — |

## Steps

1. **Join verdicts** — Join each verification to its finding by id, matching the verification's finding_id to the finding's id, attaching that verification's verdict and false_positive flag onto the matching finding.
2. **Drop refuted** — Drop findings whose attached verdict is refuted or whose false_positive flag is set, keeping the survivors.
3. **Merge duplicates** — Merge surviving findings that share the same file, line, and root cause into a single consolidated entry.
4. **Re-rank survivors** — Re-rank the merged survivors by adjusted severity, ordering blocker-severity entries first.
5. **Tally blockers** — Tally the count of blocker-severity survivors.
6. **Select verdict** — Select the overall verdict by applying the active severity-to-verdict mapping: {{verdict_mapping}}
7. **Render report** — Render the markdown report — a change overview drawn from change_summary, then the survivors grouped by severity with their file, line, and suggestion — delivering it per the active report target: {{report_target}}
8. **Assemble output** — Assemble and return the JSON object matching the active profile's output schema — the verdict, the counts, a one-line outcome summary, the merged surviving findings, and the report delivered per the report target above.

## Output

A single JSON object returned as the microskill's structured result, shaped by the active profile's output schema: the verdict (approve | comment | request_changes — per {{verdict_mapping}}), the count fields, a one-line summary, the merged surviving findings, and the rendered severity-grouped report delivered per {{report_target}}.

## Failure modes

- **Missing required input** — change_summary, findings, or verifications is absent; stop, name the input, do not proceed.
- **Unjoinable verification** — a verification's finding_id matches no finding's id; stop, quote the orphan id, do not proceed.
- **Finding missing id or severity** — cannot join or rank it; stop, quote the bad entry, do not proceed.
