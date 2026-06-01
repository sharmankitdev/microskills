# Phase 2 — Implementer

The `microskill-implementer` agent fleshes out the bare template using the approved plan and writes a `profiles/base.yaml` baseline config. On remediation rounds, it addresses every evaluator finding without regressing accepted fields.

## Input handed to the agent

- `plan_path` — path to the approved plan YAML; **Read it** to get the plan.
- Substrate paths:
  - `.claude/templates/microskill-template.md`
  - `.claude/templates/references/config-schema.md`
  - `.claude/templates/references/config-schema.json`
- `staging_dir` — absolute path where files must land.
- For any remediation pass (round >= 1, when the orchestrator threads in `last_findings`):
  - `staging_paths` — previous draft (read these).
  - `last_findings` — issues from the previous evaluator pass; address each one.

## What the implementer does

1. **Read the substrate.**
   - `.claude/templates/microskill-template.md` defines the frontmatter plus five markdown sections: Purpose, Inputs (table), Steps (numbered, linear), Output, Failure modes. Never emit a `## Setup` section — the `microskill` dispatcher Skill at `.claude/skills/microskill/SKILL.md` owns the runtime contract.
   - `.claude/templates/references/config-schema.json` is the closed grammar `profiles/base.yaml` must validate against.
2. **Write `<staging_dir>/MICROSKILL.md`.** Replace every placeholder in the template with values from the plan:
   - `name` and `description` go in the frontmatter.
   - Purpose, Inputs table, Steps, Output, Failure modes come straight from the plan.
   - Number the steps `1.`, `2.`, `3.` … Each step must be a single verb-led action on the straight path to the output. Do not write steps that branch or introduce conditional forward paths.
   - Every Default cell is literally `—`. Do not write input defaults here — defaults live in `profiles/base.yaml` under `inputs.<name>.default`. The resolver rewrites the Default column at render time.
3. **Write `<staging_dir>/profiles/base.yaml`.** This is unconditional — every microskill ships a base profile. Create the `profiles/` subdirectory under `staging_dir`. Include only the sections corresponding to `plan.config.axes`. **Note on axis derivability**: `inputs` (via `plan.inputs[].default`) and the top-level `output_schema` (via `plan.output_schema`) are fully derivable from plan data. All other axes (`vars / steps / gates / context / runtime`) require explicit plan-level fields that the planner must have emitted alongside `config.axes`. If an axis appears in `config.axes` without corresponding plan content to populate it, do not invent content — instead, halt the implementation pass and surface a block-severity finding (`location: config`, `message: "plan.config.axes lists <axis> but plan provides no <axis>-related content to populate it"`). The file must validate against `config-schema.json`:
   - Always start with `version: 1`.
   - For each input in `plan.inputs` that declares a non-null `default`, emit `inputs.<name>.default: "<value>"` (string only) in `base.yaml`.
   - For `inputs.<name>` overrides — the name must match an input declared in the plan.
   - For `inputs.<name>.inject_from` — exactly one source key (`git_config | env | file | command`).
   - For `gates.<id>.severity` — `warn` or `hard` only.
   - The optional top-level `profile.default: <name>` field (only valid in base.yaml) names a default overlay profile. Only set it when the plan explicitly calls for one.
   - The optional top-level `output_schema` — if `plan.output_schema` is present, copy it verbatim as a top-level `output_schema` key. The resolver injects a structured-return directive from it so the skill emits this JSON shape (standalone or composed in a workflow `use:` node).
4. **Remediate findings (any remediation pass — i.e. when `last_findings` is supplied).** For each issue in `last_findings.issues[]`, locate the offending section in the prior draft (`staging_paths`) and fix it. Modify only the sections named in `last_findings.issues[].location`. Do not edit sections that no issue in the current round references — the evaluator did not flag them, so any change to them risks regressing a previously-accepted field.

## Output

The agent's final message lists:
- The absolute paths written (under `staging_dir`) — always `MICROSKILL.md` and `profiles/base.yaml`.
- On remediation rounds: one line per finding addressed, naming the finding and what was changed.

## Hard rules

- Never write outside `staging_dir`.
- Steps must form a linear sequence: one entry, one exit, no branching language ("if … then", "depending on", "either … or", "go to step N").
- Frontmatter `name + description` word count ≤ 100.
- Always write `profiles/base.yaml`. A microskill without base is rejected by the resolver at runtime.
- Do not modify the substrate template or schema — they are read-only inputs.
