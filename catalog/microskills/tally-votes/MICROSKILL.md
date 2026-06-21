---
name: tally-votes
base: true
description: >-
  Use after a panel of adversarial verdicts has scored each assigned seat, when you have
  the per-seat verdicts plus the original assignment object and need them tallied. Joins
  each verdict back to its assignment seat by finding_id, groups by item-identity × lens,
  and applies a fixed majority rule (majority non-refute → upheld; majority refute →
  refuted; ties, shortfalls, and undecidables → needs_human). Produces a JSON tally report
  carrying a derived approve/comment/request_changes verdict, the grouped finding lists,
  counts, and the written report path.
---

# Tally Votes

## Purpose

Given per-seat verdicts and the originating assignment object, deterministically join and group them, apply the majority rule per group, and produce a written tally report plus a derived overall verdict.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| verdicts | yes | array | Per-seat adversarial verdict objects, each echoing its finding_id (and its seat when the producer stamps one — a verdict without a seat still counts once toward its group: the majority rule needs group membership, not seat identity, and shortfall detection rests on verdicts_received vs pair_count). Each verdict records one seat's judgment for one item-identity × lens pairing. | — |
| assignment | yes | object | The expand-assignments whole output object — the originating set of seat assignments (item-identity × lens pairs, joined by the item id or its composite `<item id>:<lens>` form) the verdicts are joined back to. Supplies pair_count and the expected seats per pair so shortfalls can be detected. | — |
| report_path | yes | string | Filesystem path where the full tally report is written. | — |

## Steps

1. **Read inputs** — Read the verdicts array, the assignment object, and report_path.
2. **Join and group** — Join each verdict to its assignment seat by its echoed finding_id key — matched against the pair's item id directly (a lens-less assignment, e.g. closure claims) and against the composite `<item id>:<lens>` form (a lensed assignment, e.g. story × criterion) — grouping the joined seats by item-identity × lens.
3. **Tally each group** — Tally each group under the majority rule — majority non-refute → upheld, majority refute → refuted; treat each missing or null verdict (verdicts_received below the group's pair_count) as a counted shortfall and route ties, shortfalls, and undecidables → needs_human.
4. **Partition and count** — Partition the groups into upheld, refuted, and needs_human lists, compute the counts (upheld_count, refuted_count, needs_human_count, attention_count, blocker_count, group_count, pair_count, verdicts_received), and assemble the seats list as the distinct seat numbers carried by the assignment object's pairs.
5. **Derive verdict** — Derive the overall verdict from the refuted and needs_human groups' severities — blocker or major → request_changes, minor only → comment, none → approve; a group whose verdicts carry no severity (e.g. closure-claim votes) counts as major.
6. **Write report** — Write the full tally report to the file at report_path.
7. **Return tally** — Return the structured tally object, including report_path and a one-line summary.

## Output

A JSON tally object written to the file at report_path and returned to the caller. It carries the derived overall verdict (approve|comment|request_changes), the upheld / refuted / needs_human group lists, the seven count fields plus blocker_count, the group/pair/received tallies, the seats list, the echoed report_path, and a one-line summary.

## Failure modes

- **Missing required input** — verdicts, assignment, or report_path is absent; stop, name the input, do not proceed.
- **Verdict missing or unmatched finding_id** — a verdict echoes no finding_id or one absent from assignment; do not silently drop it; count it as a shortfall against its group.
- **assignment malformed** — assignment is not the expected expand-assignments object (no resolvable pairs/pair_count); stop, quote the offending shape, do not proceed.
