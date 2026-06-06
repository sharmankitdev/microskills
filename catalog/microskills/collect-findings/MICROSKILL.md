---
name: collect-findings
description: "Use after a code-review workflow's per-dimension reviews complete, when you have up to four optional dimension result objects (correctness, security, performance, style), each shaped {dimension, findings}, and need them fanned in. Concatenates the supplied findings arrays into one flat list, stamps each finding with its source dimension, assigns each a workflow-wide global_id, and drops exact duplicates sharing file, line, and title. Produces a single JSON object {findings, count}."
---

# Collect Findings

## Purpose

Given up to four optional per-dimension review results, concatenate their findings into one flat list, stamp each finding with its source dimension, assign workflow-wide global_ids, drop exact (file, line, title) duplicates, count the survivors, and return {findings, count}.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| correctness | no | object | Optional correctness-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| security | no | object | Optional security-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| performance | no | object | Optional performance-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |
| style | no | object | Optional style-dimension review result, shaped {dimension, findings} as produced by review-dimension; its findings are merged into the combined list. | — |

## Steps

1. **Concatenate findings** — Concatenate the findings arrays from every supplied dimension input (correctness, security, performance, style) into one combined flat list.
2. **Stamp dimension** — Stamp every finding with the dimension name taken from its source result's dimension field.
3. **Assign global ids** — Assign every finding a workflow-wide sequential global_id over the combined list.
4. **Drop duplicates** — Drop exact duplicate findings that share the same file, line, and title, keeping the first occurrence.
5. **Count survivors** — Count the surviving findings.
6. **Return result** — Return a single JSON object carrying the findings array and the integer count.

## Output

A single JSON object {findings, count} returned as the structured result (nothing is written to disk). findings is the flat, dimension-stamped, globally-identified, deduplicated list of finding objects; count is the integer number of surviving findings. Consumable directly by a downstream workflow node.

## Failure modes

- **No dimension input supplied** — all four dimension inputs are absent; stop, state that at least one dimension result is required, do not proceed.
- **Dimension input not an object with findings** — a supplied dimension input is not an object carrying a findings array; stop, name the offending input, do not proceed.
- **Findings value not an array** — a supplied dimension result's findings value is not an array; stop, name the offending dimension, do not proceed.
