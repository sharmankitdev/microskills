---
name: verify-finding
description: Use when a code-review pipeline holds a single finding and the unified diff it cites, and you need to adversarially test whether that finding actually holds. Locates the cited hunk, builds the strongest case both for and against the finding using only the diff evidence, weighs them, and renders a judgment. Produces a single JSON object with finding_id, verdict (confirmed, refuted, or needs_human), rationale, adjusted_severity, and false_positive.
---

# Verify Finding

## Purpose

Given one code-review finding and the unified diff it cites, adversarially weigh the case for and against it on the diff evidence and return a single verdict object.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| finding | yes | object | One code-review finding produced upstream (e.g. by review-dimension or collect-findings): carries its id, the diff location it cites (file plus hunk/lines), a description of the claimed issue, and its claimed severity. | — |
| diff | yes | string | The unified diff the finding refers to. All evidence is drawn from this text; the skill never reads files. | — |

## Steps

1. **Locate cited hunk** — Locate the cited hunk and the referenced lines for the finding within the diff.
2. **Build counter-case** — Build the strongest counter-case that the finding is wrong, a false positive, or already handled elsewhere in the diff.
3. **Build supporting case** — Build the strongest supporting case that the finding is real and reachable on the cited evidence.
4. **Weigh the cases** — Weigh the counter-case against the supporting case using only the cited diff evidence.
5. **Decide verdict** — Decide the verdict (confirmed, refuted, or needs_human), set adjusted_severity to the severity the evidence warrants, and mark the false_positive boolean.
6. **Emit result** — Emit the single result JSON object.

## Output

A single JSON object returned as the skill's result (not written to a file), carrying finding_id (string, echoed from the input finding), verdict (one of confirmed, refuted, needs_human), rationale (string explaining how the two cases were weighed on the diff evidence), adjusted_severity (string or null), and false_positive (boolean). Exactly one object per call.

## Failure modes

- **Missing required input** — finding or diff is absent; stop, name the missing input, do not proceed.
- **finding missing its id or cited location** — the object lacks the fields needed to ground a verdict; stop, name the missing field, do not proceed.
- **Cited location absent from the diff** — the finding points at a hunk or lines not present in the supplied diff; return verdict needs_human with adjusted_severity null and a rationale naming the missing location, rather than fabricating evidence.
