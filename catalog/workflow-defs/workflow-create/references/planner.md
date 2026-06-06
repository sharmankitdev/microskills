# Phase 1 — Planner

The `workflow-planner` agent turns a natural-language requirement into a structured workflow plan, or surfaces a scope advisory when the work should not be a single workflow.

## Input handed to the agent

- `requirement` — verbatim user description.
- `name_override` — optional caller-supplied kebab-case name.
- `staging_dir` — absolute path to the sandbox dir; write the plan file here.
- Substrate: `.claude/templates/workflow-template.yaml`, `.claude/templates/references/workflow-schema.json`.
- Registries to enumerate: `.claude/microskills/` and `.claude/workflow-defs/`.

## What the planner does

1. **Read the substrate and enumerate the registries.** For each microskill: its `name`, `description`, and the profiles already under `profiles/`. For each workflow: `name`, `description`, `inputs`.
2. **Scope check.**
   - Single atomic task (no multi-node orchestration needed) → `scope_advisory.kind: promote`, recommend `microskill-create`.
   - Two or more independent workflows → `scope_advisory.kind: split`, list candidates.
   - An existing workflow already covers this → `scope_advisory.kind: adapt`, name it.
   - Otherwise → design the workflow.
3. **Design the node graph.** For each step pick the dispatch form (`use:` / `agent:` / `delegation: orchestrator`), wire `depends_on` + `inputs` + `output_schema`, place gates, and use `when` / `for_each` / `loop` where the requirement calls for branching, fan-out, or iteration. Keep loop-body nodes contiguous and orchestrator nodes out of the loop body.
4. **Decide reuse + profile fit.** For each `use:` node, choose the profile: `base`, an existing overlay, or a NEW profile. Record new profiles in `_new_profiles`.
5. **List missing microskills.** Any microskill the graph needs that does not exist yet goes in `missing_microskills` (name + crisp requirement); the pipeline provisions it via `microskill-create` before implement.

## Output contract

Write the target WORKFLOW.yaml design — the `plan_yaml` document shown below (including the reserved `_new_profiles` / `_reuse` annotation keys) — to `<staging_dir>/plan.yaml`. Then return the plan object `{plan_path, name, scope_advisory, missing_microskills}`, where `plan_path` is the path you wrote (null on a scope advisory). The document you write to that path follows this shape (the `plan_yaml:` key below labels the file body — it is NOT a returned field):

````yaml
name: <kebab-case>                  # ^[a-z][a-z0-9-]*$ (or echo name_override)

missing_microskills:                # [] when none are missing
  - name: <kebab-case>
    requirement: <one-line requirement for microskill-create>

scope_advisory:                     # null/omitted unless applicable
  kind: split | promote | adapt
  reason: <one sentence>
  recommendation: <split → candidate workflows | promote → use microskill-create | adapt → existing workflow name + delta>

plan_yaml: |                        # ← WRITE this document to <staging_dir>/plan.yaml; return its path as plan_path (do NOT return it inline)
  version: 1
  name: <name>
  description: <one-line>
  inputs: { … }
  nodes:
    - id: <node>
      use|agent|delegation: …
      depends_on: [ … ]
      when: ${…}                    # optional
      for_each: ${…}                # optional (+ as:)
      inputs: { <field>: ${…} }
      prompt: > …                   # agent nodes
      output_schema: { … }
      customize: { profile: <name> }  # use: nodes
  gates: [ … ]
  loop: { while: ${…}, max_iters: N, body: [ … ], carry: { … } }
  output: { from: <node> }
  # …plus two RESERVED annotation keys the implementer strips before writing:
  _new_profiles:                    # profiles to mint for reused microskills
    - microskill: <ms-name>
      profile: <profile-name>
      contents: { version: 1, … }   # the overlay body
  _reuse:                           # optional per-node provenance
    - node: <node-id>
      kind: microskill | agent | workflow_inline_expansion
      origin: <name>
      notes: <what was adapted>
````

When `scope_advisory` is set, no plan file is written (`plan_path` is `null`) and `missing_microskills` is `[]`.

## Constraints

- Never invent a `workflow:` node type. Reuse a whole workflow only by inline-expanding its nodes (rename ids, rewire refs, annotate under `_reuse`) or by flagging `scope_advisory.kind: adapt`.
- Every `${<id>.output...}` ref a node uses must be in its `depends_on`; every `${workflow.inputs.x}` must be declared in top-level `inputs`.
- Name by capability, not by occasion. Both the workflow `name` and every `missing_microskills[].name` (which becomes the permanent registry name via `name_override`) must say exactly what the artifact does while excluding the domain, caller, or step that motivated it: `task-evaluate` (the phase; a profile binds the domain), not `evaluate-microskill-for-create`; `extract-pr-links`, not `extract-links-for-the-release-workflow`. Forbidden: position/step/caller tokens. Scope: registry names only — not node ids, gate ids, input names, or profile names (profiles are where domain-coupling belongs). A name coupled to this one workflow is a plan defect.
- `_new_profiles` / `_reuse` are plan-only — they are not valid WORKFLOW.yaml keys and the implementer removes them.
- Emit YAML only. No preamble, no commentary, no postscript.
