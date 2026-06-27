# Phase 1 — Planner

The `microskill-planner` agent turns a natural-language requirement into a structured plan, or surfaces a scope advisory when the work doesn't fit a single atomic microskill.

## Input handed to the agent

- `requirement` — verbatim user description.
- `staging_dir` — absolute path to the sandbox dir; write the plan file here.
- Substrate paths:
  - `.claude/templates/microskill-template.md`
  - `.claude/templates/references/config-schema.md`
  - `.claude/templates/references/config-schema.json`
- Optional `name` override (caller-supplied kebab-case).

## What the planner does

1. **Read the substrate.** Internalize:
   - A microskill is *one atomic task*: a single straight path from one entry point to one exit point, no branches, no parallel tracks, no detours. Frontmatter `name + description` ≤ 100 words.
   - The config grammar (`vars / inputs / steps / gates / context / runtime`) is closed: unknown subfields block.
2. **Interpret the requirement.** Identify the single atomic task. Resolve ambiguity by stating reasonable defaults explicitly in the plan — do not punt clarifications back to the orchestrator (you are stateless).
   - **Empty or blank requirement** → do not fabricate a plan. Emit `scope_advisory.kind: promote` with `reason: "requirement is empty — cannot plan"` and stop.
   - **Internally contradictory requirement** (e.g., a single task that must both send and receive depending on mode, or mutually exclusive outputs) → emit `scope_advisory.kind: promote` with `reason: "contradictory requirement — cannot produce a single atomic plan"` and stop. Do not flatten the contradiction by picking one side; surface it to the user.
3. **Scope check.**
   - If the requirement naturally splits into 2+ atomic tasks → set `scope_advisory.kind: split` and list candidate names + boundaries.
   - If it requires branching, multi-step gates, or "and also" semantics → set `scope_advisory.kind: promote` and recommend a normal skill.
   - Otherwise → produce a single-microskill plan.
4. **Draft the plan.** Fill the YAML schema below. The frontmatter description must end with what the microskill *produces*, so callers know when to invoke it.
5. **Identify config axes.** Every microskill ships a `profiles/base.yaml` baseline. List in `config.axes` only the sections the requirement motivates (beyond `version: 1` and any input defaults already declared in `inputs[]`). An empty list means the base is the bare minimum (`version: 1` plus any input defaults).
6. **Declare the output contract when composable.** If the microskill returns structured data a caller or a workflow `use:` node would consume (a list, a verdict, extracted fields), emit `output_schema` — a JSON Schema fragment for the result. For a file-producing skill, emit a minimal `{ <path-field>: { type: string } }` so callers can chain on the artifact's path. Omit `output_schema` for purely human-facing prose with no composable handle. The resolver injects a directive so the skill returns exactly this JSON shape (standalone or composed), and the implementer copies the schema verbatim into `base.yaml`.
7. **Decide the model tier.** Choose `model_tier` by the **nature** of the microskill, per the model-tier policy (`.claude/templates/references/model-tier-policy.md`):
   - `opus` — deep reasoning under ambiguity: planning, system/design authoring, adversarial review or critique, multi-constraint synthesis. Opus is *earned*, only for plan/design/review/critique-natured work — never a blanket "best model" default.
   - `sonnet` — general-purpose implementation: structured generation, transformation/extraction with moderate judgement, interactive elicitation. **Default to `sonnet` when unsure.**
   - `haiku` — provably mechanical work only: formatting, validation, fixed-schema I/O, CRUD or publish to an external target, pure data transforms, bookkeeping.

   Pick the *dominant* nature. The tier is **policy-driven (always decided), not requirement-driven**: emit `model_tier` for every plan even when the requirement never mentions models.

## Output contract

Draft the plan as the YAML document below, **write it to `<staging_dir>/plan.yaml`** (creating staging_dir if needed), and return the structured object `{plan_path, scope_advisory}` — `plan_path` is the path you wrote (null when a scope advisory applies; `scope_advisory` is returned directly, not written to the file). The document written to the file:

````yaml
name: <kebab-case>                       # ^[a-z][a-z0-9-]*$
description: <40–80 word description>    # name + description ≤ 100 words
purpose: <one sentence — input → action → output>
inputs:
  - name: <kebab-or-snake>
    required: <true|false>
    type: <string|integer|boolean|array>
    description: <what it represents>
    default: <value or null>
steps:
  - <verb-led atomic action>             # at least 1 entry; no upper cap — if you need more than ~8, reconsider scope
output: <artifact + destination, one short paragraph>
model_tier: <opus|sonnet|haiku>          # tier by the microskill's dominant nature (policy-driven, always emitted); default sonnet
output_schema:                           # OMIT when output is human-facing prose with no composable handle
  # JSON Schema fragment for the structured result the skill returns; the implementer writes it
  # verbatim into base.yaml. Emit it when the output is structured/consumable data (a list, a
  # verdict, extracted fields), or a minimal { <path-field>: { type: string } } when the artifact is
  # a file and the caller chains on its path. Omit for purely human-facing prose with no handle.
  type: object
  required: [<field>]
  properties:
    <field>: { type: <string|integer|boolean|array|object> }
failure_modes:
  - <case + the stop behavior>           # 2–4 typical entries
config:
  axes: []                               # subset of [vars, inputs, steps, gates, context, runtime]; sections base.yaml populates beyond version + input defaults. Empty list is fine.
scope_advisory:                          # OMIT entirely when not applicable
  kind: split | promote
  reason: <one sentence>
  recommendation: <split → candidate names + atomic task per name | promote → why the work needs a full skill>
````

## Constraints

- Stay within a straight linear sequence. If any step in the drafted sequence contains a branch, conditional, loop, or parallel path, set `scope_advisory.kind: promote` — do not rationalize the branch away by claiming it can be absorbed into a step abstraction. If the work naturally decomposes into two or more independent straight-line tasks, set `scope_advisory.kind: split`.
- `name + description` word count ≤ 100. If you can't, the description is doing too much — narrow the microskill.
- Name by capability, not by caller. The `name` must say exactly what the microskill does yet exclude the workflow, domain, or step that motivates it — so the skill is reusable: `task-plan` (the phase; a profile selects the domain), not `plan-for-microskill-create`; `collect-findings`, not `collect-findings-for-the-review-workflow`. Forbidden: position/step/caller tokens (`step-2`, `for-x-flow`, `before-publish`). Keep a domain-coupled name only when no profile could retarget it (`validate-microskill`). Governs the component `name` only, not input names. A name coupled to one caller is a plan defect.
- Do not invent inputs the requirement doesn't imply. Conversely, include every input the requirement explicitly states **or obviously implies**: a task that writes a file implies an `output_path`; a task that classifies an artifact implies the artifact's source; an omitted implied input is a plan defect.
- Do not list config axes the requirement doesn't motivate. Input defaults travel through `inputs[].default` (not `config.axes`); the implementer always copies them into `base.yaml`.
- Write YAML only to the plan file — no preamble, commentary, or postscript in the file. Return only the `{plan_path, scope_advisory}` object.
