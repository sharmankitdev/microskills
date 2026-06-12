---
name: synthesize-panel
description: >-
  Use when a panel of judge scorecards has scored a shared candidates list and you
  need their verdicts fused into one winner pick. Computes rank-sum across the
  supplied judge lenses over the candidates order, breaks ties by earliest position
  in that order, derives graft notes from the runner-up candidates' strengths, and
  writes the full scorecard set to a file. Produces a JSON object naming the winning
  candidate, its document path, graft notes, explicit candidate and judge tallies,
  the written file path, and a one-line summary.
---

# Synthesize Panel

## Purpose

Given a candidates list, a panel of optional judge scorecards, and an output path, deterministically rank-sum the judges, pick the winner, derive graft notes from the runners-up, and write the scorecard set to the file at judges_path.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| candidates | yes | array | Ordered list of candidate objects, each {label, document_path}. The list order is the panel's static fan-out order and is the pinned tie-break key. | — |
| judge_1 | no | object | A judge-artifacts scorecard (per-candidate scores + a best-first ranking) for the first judge lens, or null. A null judge contributes nothing and is counted as absent. Base declares three judge slots; the number of judges is profile-set. | — |
| judge_2 | no | object | The second judge's judge-artifacts scorecard, or null (counted absent when null). | — |
| judge_3 | no | object | The third judge's judge-artifacts scorecard, or null (counted absent when null). | — |
| judges_path | yes | string | Absolute path to the file the full scorecard set is written to. This is the exact file to write; it is also echoed back in the result. | — |

## Steps

1. **Read panel** — Read the candidates list and each supplied judge scorecard, ignoring null judges (counted as absent).
2. **Rank candidates per judge** — Assign each candidate its 1-based rank position within each present judge's best-first ranking.
3. **Sum ranks** — Sum each candidate's ranks across all present judges into a rank-sum tally.
4. **Pick winner** — Order candidates by ascending rank-sum, settling equal sums by earliest position in the candidates order, and take the head as the winner.
5. **Derive graft notes** — Derive graft_notes by collecting the runner-up candidates' recorded strengths from the judge scorecards into one note string.
6. **Tally coverage** — Count candidates_considered (length of the candidates list) and judges_heard (present, non-null judges).
7. **Write scorecards** — Write the full scorecard set to the file at judges_path.
8. **Return result** — Return the result object naming the winner, its path, graft_notes, the two tallies, judges_path, and a one-line summary.

## Output

The full scorecard set is written to the file at judges_path. The returned JSON object carries winner_label and winner_path (the winning candidate and its document_path), graft_notes (a string composed from the runner-up candidates' strengths), candidates_considered and judges_heard (explicit integer tallies so coverage is visible and never silently capped), judges_path (the written file path echoed back), and a one-line summary.

## Failure modes

- **Missing required input** — candidates or judges_path is absent; stop, name the input, do not proceed.
- **candidates malformed** — candidates is empty or not an array of {label, document_path} entries; stop, quote the bad value, do not proceed.
- **No judge heard** — all judges are null (judges_heard would be 0); stop, report that no judge was heard, do not fabricate a winner.
