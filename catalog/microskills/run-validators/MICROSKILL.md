---
name: run-validators
description: Use as the deterministic-floor node of a create pipeline — run the programmatic validators named by the active floor contract over a staged artifact (validate-microskill for a microskill draft, or validate-workflow plus compile-workflow for a workflow draft) and map their results to floor findings. Produces a JSON object with dimension deterministic-floor and a findings array, with no pass verdict.
---

# Run Validators

## Purpose

Given staged artifact paths and the floor contract for the active domain, run the named deterministic validators and return their results mapped to a single deterministic-floor findings object.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| staging_paths | yes | array | Absolute paths to the staged files to validate (a microskill draft's MICROSKILL.md + profiles/base.yaml, or a workflow draft's WORKFLOW.yaml + profiles/base.yaml). | — |
| name | no | string | Artifact name; the workflow floor contract passes it to compile-workflow as the workflow subdir. | — |
| staging_base | no | string | Staging root directory; the workflow floor contract passes it to validate-workflow and compile-workflow as --defs-root. | — |

## Steps

1. **Read floor contract** — Review the deterministic-floor contract supplied as reference context; it names the exact validator command(s) for the active domain and the rules for mapping their output to floor findings.
2. **Run validators** — Run the validator command(s) the floor contract specifies over the files in `staging_paths`, passing `name` and `staging_base` where the contract names them.
3. **Parse results** — Parse each command's stdout JSON and its exit code.
4. **Map findings** — Map every validator issue and every compilation error to one floor finding `{id, severity, location, message, source}` per the floor contract's mapping rules, assigning sequential ids floor-1, floor-2, … in command-output order.
5. **Return result** — Return the JSON object `{dimension: "deterministic-floor", findings}`.

## Output

A single JSON object `{dimension, findings}` returned as the structured result. `dimension` is the constant `deterministic-floor`. `findings` is an array; each finding carries `id` (floor-<n>), `severity` (blocker or warn), `location` (copied from the validator), `message` (copied from the validator), and `source` (the validator that produced it). There is no `pass` field — the floor reports findings; the caller decides.

## Failure modes

- **Missing required input** — staging_paths is absent; stop, name the missing input, do not proceed.
- **Malformed staging_paths** — staging_paths is not a non-empty list of file paths; stop, quote the bad value, do not proceed.
- **Validator failed to start** — a named validator exits non-zero with empty or non-JSON stdout; emit a single blocker floor finding at location environment naming the failing validator, rather than fabricating a clean floor.
- **Missing workflow pass-through input** — the workflow floor contract needs name or staging_base but it is absent; stop, name the missing input, do not proceed.
