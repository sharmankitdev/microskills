---
name: synthesize-review
description: >-
  Use when you have verified code-review findings and need them consolidated into
  one report. Joins per-finding verdicts to their findings by global id, drops
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
| verifications | yes | array | The per-finding verdicts produced by verify-finding, each carrying a global id, verdict, and false_positive flag. | — |

## Steps

1. **Join verdicts** — Join each verification to its finding by global id, attaching that verification's verdict and false_positive flag onto the matching finding.
2. **Drop refuted** — Drop findings whose attached verdict is refuted or whose false_positive flag is set, keeping the survivors.
3. **Merge duplicates** — Merge surviving findings that share the same file, line, and root cause into a single consolidated entry.
4. **Re-rank survivors** — Re-rank the merged survivors by adjusted severity, ordering blocker-severity entries first.
5. **Tally blockers** — Tally the count of blocker-severity survivors.
6. **Select verdict** — Select the overall verdict by applying the fixed severity-to-verdict mapping (a surviving blocker gives request_changes, only non-blocker survivors give comment, no survivors gives approve).
7. **Render report** — Render the markdown report — a change overview drawn from change_summary, then the survivors grouped by severity with their file, line, and suggestion.
8. **Assemble output** — Assemble and return the JSON object — verdict, blocker_count, a one-line outcome summary, report_markdown, and the merged surviving findings.

## Output

A single JSON object returned as the microskill's structured result (not written to disk): verdict (approve | comment | request_changes), blocker_count (integer), summary (a one-line outcome string), report_markdown (the rendered severity-grouped markdown report), and findings (the array of merged surviving findings).

## Failure modes

- **Missing required input** — change_summary, findings, or verifications is absent; stop, name the input, do not proceed.
- **Unjoinable verification** — a verification's global id matches no finding; stop, quote the orphan id, do not proceed.
- **Finding missing global id or severity** — cannot join or rank it; stop, quote the bad entry, do not proceed.
