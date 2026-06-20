---
name: collect-findings
base: true
description: "Use after a review workflow's per-dimension reviews complete, when you have optional dimension result objects (base names six code-review dimensions; other profiles declare their own input names), each shaped {dimension, findings}, and need them fanned in. Concatenates the supplied findings arrays into one flat list — flattening any input that is itself an array of result objects into its elements first — stamps each finding with its source dimension, assigns workflow-wide global_ids, and drops exact duplicates sharing file, line, and title. Produces a single JSON object {findings, count}."
---

# Collect Findings

## Purpose

Given the supplied optional per-dimension review results, concatenate their findings into one flat list, stamp each finding with its source dimension, assign workflow-wide global_ids, drop exact (file, line, title) duplicates, count the survivors, and return {findings, count}.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| correctness | no | object | Optional correctness-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| security | no | object | Optional security-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| performance | no | object | Optional performance-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| style | no | object | Optional style-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| documentation | no | object | Optional documentation-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| test_coverage | no | object | Optional test-coverage-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |

## Steps

1. **Concatenate findings** — Concatenate the findings arrays from every supplied optional dimension result object into one combined flat list, flattening any supplied input that is itself an array of result objects (a fan-out's per-item results) into its elements before concatenation.
2. **Stamp dimension** — Stamp every finding with the dimension name taken from its source result's dimension field.
3. **Assign global ids** — Assign every finding a workflow-wide sequential global_id over the combined list.
4. **Drop duplicates** — Drop exact duplicate findings that share the same file, line, and title, keeping the first occurrence.
5. **Count survivors** — Count the surviving findings.
6. **Return result** — Return a single JSON object carrying the findings array and the integer count.

## Output

A single JSON object {findings, count} returned as the structured result (nothing is written to disk). findings is the flat, dimension-stamped, globally-identified, deduplicated list of finding objects; count is the integer number of surviving findings. Consumable directly by a downstream workflow node.

## Failure modes

- **No dimension input supplied** — every declared dimension input is absent; stop, state that at least one dimension result is required, do not proceed.
- **Dimension input not an object with findings** — a supplied dimension input is not an object carrying a findings array; stop, name the offending input, do not proceed.
- **Findings value not an array** — a supplied dimension result's findings value is not an array; stop, name the offending dimension, do not proceed.
