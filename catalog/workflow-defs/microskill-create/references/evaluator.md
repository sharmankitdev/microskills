# Phase 3 — Evaluator

The `microskill-evaluator` agent runs programmatic and semantic validation, returning a structured findings JSON the orchestrator uses to decide whether to finalize or loop.

## Input handed to the agent

- `staging_paths` — the two paths the implementer always writes: `MICROSKILL.md` and `profiles/base.yaml`. Both files are unconditional outputs; treat the list as fixed-length two.
- `requirement` — the original user requirement (verbatim).
- `plan_path` — path to the approved plan YAML; **Read it**.

## What the evaluator does

### 1. Programmatic validation (run first)

Invoke the validator script. Always pass both `staging_paths` files — the config arg is **not** optional, because the implementer always writes `profiles/base.yaml`:

```
.claude/scripts/validate-microskill <MICROSKILL.md> <profiles/base.yaml>
```

Parse its JSON output. Every `severity: "block"` issue from the script must appear verbatim in the evaluator's final output (with `location` preserved). If the script fails to start (non-zero exit with no JSON on stdout, or stdout empty), emit a `block`-severity issue with `location: environment` and `message: validate-microskill script failed to execute — <stderr or exit code>`; do not proceed with semantic checks until programmatic validation has been attempted.

The script checks:
- Frontmatter present + parseable YAML with `name` and `description`.
- `name` matches `^[a-z][a-z0-9-]*$`.
- `name + description` word count ≤ 100.
- All required template sections present.
- Numbered steps are present (at least one).
- Config file (if present) validates against `config-schema.json`.
- **Required-input fidelity** — any input marked `Required=yes` in the MICROSKILL.md table is
  declared `inputs.<name>.required: true` (or supplied via `inject_from`) in `base.yaml`; otherwise
  the resolver never gathers it (emitted as a `block` at `location: config`).

### 2. Semantic validation (run second)

Read the staged files and the plan at `plan_path`. Compare the staged files against `requirement` and that plan. Look for:

- **Intent fidelity** — does the MICROSKILL.md actually do what the requirement asked for? When flagging a mismatch, quote the offending line from the MICROSKILL.md and the part of the requirement it conflicts with.
- **Description ends with what the microskill produces** — the last clause of the frontmatter `description` must name the artifact or output (e.g., "…produces a classified EARS requirements document", "…outputs a JSON findings payload"), not the process. A description that ends mid-process or mid-trigger fails this check; flag as `block`.
- **Linearity** — do the steps form a single straight path from entry to exit? Flag any step that introduces a conditional branch ("if … then …", "depending on", "either … or …", "go to step N"), a parallel track, or a loop. A step may have a single failure-exit (→ stop), but not two forward paths.
- **Single entry point** — is there exactly one place a caller can start? Flag any step that implies the caller must make a choice before the first step executes.
- **Single exit point** — does every execution path converge on the same declared output? Flag if two steps produce different artifacts or if the output section describes conditional results ("if X then output A, else output B").
- **Each step advances toward the exit** — does every intermediate step move the work closer to the final output, with no detours or parallel concerns? Flag steps that could be removed without breaking the forward progression.
- **Atomicity** — does it do exactly one task? Flag any "and also" patterns or steps that look like a separate concern.
- **Input ↔ step coherence** — every declared input should be referenced in at least one step; every step that needs data should name the input it consumes.
- **Required-input enforcement** — every input the table marks `Required=yes` must be enforced in `base.yaml` (`inputs.<name>.required: true`, or an `inject_from` source). A table that declares an input required while `base.yaml` stays silent is a runtime-fidelity defect: the resolver will not gather it. Flag as `block` (the script also catches this; surface it either way).
- **Verb-led atomic steps** — each step starts with a verb and is a single action, not a paragraph.
- **Failure-mode coverage** — failure modes cover at least: missing required input, malformed input, and any domain-specific risk implied by the requirement.
- **Output contract (composability)** — if the Output section describes structured data a caller or a workflow `use:` node would consume but `base.yaml` declares no `output_schema`, `warn` (suggest declaring one so the skill composes). If `output_schema` IS declared, check it is coherent with the Output section — its fields should match what Output describes; mismatch → `warn`. These are composability nudges, not blocks (the JSON Schema fragment's own validity is already enforced by `config-schema.json`).

For semantic issues, prefer `severity: "warn"` unless the issue would make the microskill misbehave at runtime.

**Linearity carve-out.** Branches, conditionals, loops, parallel tracks, and any forward-path multiplicity in the Steps section are always `severity: "block"`, regardless of whether the LLM judges them runtime-visible. The compliance contract requires a single straight-line path; "the conditional only fires in an edge case" is not an acceptable rationale for a warning. The same rule applies to the Output section if it describes conditional results ("if X then output A, else output B").

## Output contract

Emit a single fenced JSON block, nothing before or after:

```json
{
  "pass": true,
  "issues": [
    {
      "severity": "block",
      "location": "frontmatter",
      "message": "<one sentence describing the issue and what should change>"
    }
  ]
}
```

`location` is one of: `frontmatter`, `inputs`, `steps`, `output`, `failure_modes`, `config`, `structure`, `semantics`, `file`, `environment`.

`pass` is `true` only when there are zero `block` issues. Warnings do not block but should be listed so the implementer can address them on the next round if iterations remain.

## Hard rules

- Always run the script. Never skip programmatic validation.
- Do not modify the staged files.
- Emit valid JSON. If you cannot, the orchestrator treats your output as `pass: false` with a `block` severity meta-issue.
