# Phase 3 — Evaluator

The `workflow-evaluator` agent runs programmatic and semantic validation on the staged workflow, returning a findings JSON the orchestrator uses to decide whether to finalize or loop.

## Input handed to the agent

- `staging_paths` — the two files the implementer always writes: `<name>/WORKFLOW.yaml` and `<name>/profiles/base.yaml`.
- `requirement` — the original user requirement (verbatim).
- `name` — the workflow name (the subdir under the staging base).
- `staging_base` — the `--defs-root` for `compile-workflow` (so `<staging_base>/<name>/WORKFLOW.yaml` resolves).

## What the evaluator does

### 1. Programmatic validation (run first — both scripts)

```
.claude/scripts/validate-workflow <name>/WORKFLOW.yaml <name>/profiles/base.yaml
.claude/scripts/compile-workflow <name> --defs-root <staging_base>
```

Parse each JSON output. Transcribe every `validate-workflow` `block` issue verbatim (preserve `location`). For `compile-workflow`: a non-zero exit is always a `block` — emit one issue per `schema_errors` / `error` at `location: compilation`. If a script fails to start (non-zero exit with no JSON / empty stdout), emit a `block` at `location: environment`; do not proceed to semantics until both have been attempted.

`validate-workflow` checks: schema conformance; unique node ids; `depends_on` completeness for every `${id.output}` ref; cycles; gate/loop/output anchoring; `for_each` requires `as`; `as` is a safe identifier; no `for_each` node inside `loop.body`. `compile-workflow` additionally resolves every `use:` node's profile, classifies delegation, and partitions segments — catching missing profiles and segmentation defects the schema alone cannot.

### 2. Semantic validation (run second)

Read the staged files; compare against `requirement`. Look for:

- **Intent fidelity** — does the node graph fulfil the requirement? Quote the node and the conflicting part of the requirement.
- **Output-schema coherence** — every `${<id>.output.<field>}` referenced anywhere (`inputs`/`prompt`/`when`/`for_each`/`carry`) has a matching property in `<id>`'s `output_schema`. Missing → `block`.
- **Microskill output contract** — for each `use:` node, run `resolve-microskill <name> [--profile <p>]` and read its declared `output_schema`. (1) If the microskill declares an `output_schema`, the node's `output_schema` must be compatible with it (same required keys, compatible types) — mismatch → `block`. (2) If the microskill declares NO `output_schema` but the node imposes one, that is a `block`: the microskill emits free-form output, so the imposed schema coerces unreliably (it silently produces garbage). Recommend declaring `output_schema` in the microskill's `base.yaml` so its result is structured.
- **when/for_each consistency** — a node and its conditionally-skipped dependencies share guards (a node that runs while a `when`-guarded dependency may be null → `block`); `for_each` targets a collection.
- **Loop convergence** — `loop.while` references at least one output produced inside `loop.body`. A `while` over only static `workflow.inputs.*` can never converge → `block`.
- **Delegation correctness** — any node whose prompt implies `AskUserQuestion`, filesystem side effects, or a nested workflow must be `delegation: orchestrator`. An `agent:`/`use:` node implying interaction → `block` (the compiler would run it autonomously and it would silently fabricate).
- **Ordering** — no orchestrator node sits between two `loop.body` nodes (would split the loop segment).
- **output.from** — points at the terminal node, not an intermediate one.

For semantic issues, prefer `warn` unless the issue makes the workflow fail to compile or misbehave at runtime.

## Output contract

Emit a single fenced JSON block, nothing before or after:

```json
{
  "pass": true,
  "issues": [
    { "severity": "block", "location": "nodes/<id>", "message": "<one sentence: what is wrong and what should change>" }
  ]
}
```

`location` is one of: `file`, `environment`, `schema:<path>`, `nodes/<id>` (optionally suffixed `/depends_on`, `/inputs`, `/prompt`, `/output_schema`, `/when`, `/for_each`), `nodes`, `gates/<id>`, `loop`, `loop/body`, `output`, `config`, `compilation`, `semantics`, `structure`.

`pass` is `true` only when there are zero `block` issues. Warnings do not block but should be listed for the implementer's next round.

## Hard rules

- Always run both scripts. Never skip `compile-workflow` even when `validate-workflow` passes.
- Do not modify the staged files.
- Emit valid JSON. If you cannot, the orchestrator treats your output as `pass: false`.
