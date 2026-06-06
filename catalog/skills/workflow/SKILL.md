---
name: workflow
description: >
  Use to execute a registered workflow. Triggered via `/workflow <name> [profile] [args]`,
  or implicitly via auto-generated per-workflow slash shims under `.claude/commands/<name>.md`
  that delegate here. Reads `.claude/workflow-defs/<name>/WORKFLOW.yaml`, compiles it via
  `.claude/scripts/compile-workflow`, then orchestrates the resulting background segments and
  human checkpoints per the run manifest.
---

# workflow

Orchestrator Skill (not a microskill). A workflow is a declarative DAG of microskill/agent nodes.
`compile-workflow` partitions it into **autonomous background segments** (compiled Claude Code
Workflow-JS) separated by **orchestrator checkpoints** (human-approval gates and orchestrator-native
nodes). This skill is the conductor: it runs each segment on the native Workflow engine, runs each
checkpoint in the main loop (where `AskUserQuestion` works), and threads outputs forward via `args`.

**The autonomous engine cannot pause for a human** — so all interaction lives here, between segments.

## Setup

1. **Name** — read `<name>` from invocation args (position 1 for `/workflow <name>`, or the first
   token from a shim). If no `.claude/workflow-defs/<name>/WORKFLOW.yaml` exists, stop and report.
2. **Profile / overrides** — detect `<profile>` (slash position 2 or `"with <profile> profile"`) and
   any `override workflow-config: <field>=<value>` clauses.
3. **Compile** — run via Bash: `.claude/scripts/compile-workflow <name> --explain [--profile "<profile>"]
   [--override <k>=<v> ...]`. Pass `--explain` so the compile summary and the written manifest carry
   `manifest_hash` (the resume check in step 6 needs it). Non-zero exit → stop and surface the JSON
   `error` / `schema_errors`. (Never pass `--plan` here — that is a dry-run that writes nothing.)
4. **Read the manifest** — read the `manifest_path` from the compile summary
   (`.claude/workflow-defs/<name>/.compiled/manifest.json`). Note `manifest.manifest_hash`.
5. **Gather inputs** — initialize `inputs = {}`. For each name in `manifest.required_inputs`: if the
   caller's prompt supplies a literal value, use it; otherwise gather via `AskUserQuestion`. Apply
   `manifest.input_defaults` for any non-required input not supplied. (Do not fall back to a default
   for a required input.)
6. **Resume check** — look for `.claude/workflow-defs/<name>/.compiled/.run-state.json` (the dispatcher's
   own runtime state, written by "Execute the manifest" below — NOT a compiled artifact; the compiler's
   stale-clean only globs `seg-*.js` and never deletes it). If it exists AND its `manifest_hash` equals
   `manifest.manifest_hash`, the stored run targets the same compiled workflow: **offer to resume** via
   `AskUserQuestion` ("Resume from step {step_index+1}, or start fresh?"). On resume, seed `results` from
   the stored `results` map and set the start position `i = step_index` (re-using completed work — this
   avoids re-running expensive opus planner segments). On "start fresh", begin at `i = 0`. If the file is
   absent, or its `manifest_hash` DIFFERS from `manifest.manifest_hash` (the workflow was recompiled /
   changed, so stored node outputs no longer line up), **ignore the stale run-state** and start fresh at
   `i = 0`.

## Execute the manifest

Maintain `results = {}` (node id → that node's returned output object). Let `M = manifest.steps.length`
and track the 0-based position `i` as you walk. **Before running each step, print a one-line header**
`Step {i+1}/{M}: {label}`, synthesizing `{label}` from the step (the manifest has no label field):
- segment → its node ids as an action — `plan` → "Plan", `implement, evaluate` (a loop) → "Implement & evaluate (loop)".
- gate → "Approval: {gate.id}".
- orchestrator_node → its node id as an action — `finalize` → "Finalize", `provision` → "Provision missing microskills".
- nested_workflow → the child workflow as an action — `build` (running `build-workflow-from-plan`) → "Build (nested workflow)".
A skipped step (a `warn` gate, or an orchestrator node whose `when` is false) still gets a header, marked skipped.
Walk `manifest.steps` in order (starting at the resume position `i` from the Setup resume check, default 0):

**Persist run-state after each step.** After a step completes (and its output is stored into `results`),
write `.claude/workflow-defs/<name>/.compiled/.run-state.json` as
`{ "manifest_hash": manifest.manifest_hash, "step_index": <the index of the NEXT step to run>, "results": results }`.
This is purely the dispatcher's in-memory `results{}` checkpointed to disk so a run that dies mid-way can
resume (see the Setup resume check) instead of restarting from step 0 and re-running expensive segments.
It is dispatcher runtime state, quarantined from the compiled bytes — the compiler never reads it and its
stale-clean (which globs only `seg-*.js`) never deletes it, so determinism is untouched. On a clean finish
the file may be left in place (a later run with a matching `manifest_hash` whose `step_index` is past the
last step simply has nothing to resume) or removed — do not let writing it block the run.

### `kind: "segment"`
1. Build the `args` object the segment expects:
   - for each `n` in `step.needs.wf_inputs` → `args["wf_" + n] = inputs[n]`
   - for each `id` in `step.needs.nodes` → `args[id] = results[id]` (the upstream node's output)
   - for each `v` in `step.needs.carry` → `args["carry_" + v] = null` (loops carry state internally
     across their own iterations; the seed is null)
2. Invoke the **Workflow tool** with `scriptPath = step.script` (the manifest stores an absolute path)
   and `args = <the object above>`. The segment runs autonomously in the background on the native engine.
3. When it completes, its return value is an object keyed by the node ids in `step.produces`. Store
   each into `results` (e.g. `results.plan = <returned>.plan`).
4. **Emit a recap** (2-4 lines, human-readable, no raw JSON) of what the segment produced, before the
   next step. Build it from `step.produces` + the stored `results`, using the **output rubric** in the
   gate block below:
   - Non-loop segment → one clause per produced node — e.g. `Planned <name> — drafted a WORKFLOW.yaml (~N nodes).`
     or `Implemented — wrote K staged files.` Never paste plan file contents or object arrays here.
   - Loop segment (`step.is_loop`) → summarize the outcome, not each round: state the round count from
     `<returned>.__rounds` (the compiled loop returns it) and the evaluator's final verdict — e.g.
     `Implement/evaluate loop done in 2 round(s) — verdict PASS, K staged files.` or
     `Implement/evaluate loop done in 3 round(s) — verdict FAIL at the round cap; N issues open.`
   - A produced node that is `null` (a guarded/skipped node) → say so in one clause; don't invent output.
5. If the segment errors (a fail-loud node), stop and surface the error in readable form — do not
   fabricate a result (skip the recap; report the failure instead).

### `kind: "checkpoint"`, `checkpoint_type: "gate"`
A human-approval / hard gate. First **render `results[gate.after]` as readable markdown** — it is an
`output_schema`-shaped object. **Never show raw JSON.** Match the shape:
- **plan** (`{plan_path, name, ...}`) → **Read the file at `plan_path`** and show its contents in a fenced ```yaml
  block, prefixed by `name`. If long, show the node graph + key decisions, not every line.
- **verdict** (`{pass, issues[]}`) → bold **PASS** / **FAIL**, then `issues` as a bullet list (summarize
  each issue's salient fields, e.g. severity + message — not the raw object). Empty issues → "PASS, no issues."
- **staging paths** (`{staging_paths[]}`) → a bullet list of the file paths.
- **scope advisory** (`{kind, reason, recommendation}` / `missing_microskills[]`) → the advisory in prose;
  list each missing microskill's `name` + one-line `requirement`.
- **default** → summarize key fields in 2-5 lines of prose; omit internal/verbose fields. Prefer shorter.
Then ask via `AskUserQuestion`: `gate.prompt` is the question, `gate.options` the choices (default
`confirm / stop`). **Give each option a one-line `description` of its consequence** — `approve`/`confirm`
→ "Continue to the next step."; `revise` → "Re-run this segment with your notes."; `abandon`/`stop` →
"Stop the run and clean up staging." (map other labels to the nearest).

**Record the human's pick — `results[gate.id] = { choice: <selected option> }`** (the chosen option's
label, verbatim). A gate id is a legal `${...}` ref target: a later node/segment whose `when` or `inputs`
reads `${<gate-id>.output.choice}` resolves against this stored object (the gate-choice branching feature
validate-workflow accepts). This is in addition to — not instead of — the approve/revise/abandon handling
below; always store the pick, then act on it. Then act:
- An `approve`/`confirm` choice → continue to the next step.
- A `revise`-style choice → **ask the user what to change** (a follow-up `AskUserQuestion` or their
  free-text note), then re-run the segment that produced the gate's `after` node: re-invoke that
  segment's script (no re-compile) with fresh `args` that fold the revision notes into the relevant
  input (e.g. append them to the `requirement` arg). Then re-render the output and re-present the gate
  (re-recording `results[gate.id]` on the re-presented choice).
- An `abandon`/`stop` choice → stop the run cleanly (clean up any staging the segments created).
For `severity: warn` gates, render the output + emit the prompt, then continue without pausing — still
record `results[gate.id] = { choice: <selected option> }` so a downstream branch on the warn-gate pick resolves.

### `kind: "checkpoint"`, `checkpoint_type: "orchestrator_node"`
An orchestrator-native step (a node with neither `use` nor `agent`, or `delegation: orchestrator`).
Execute its `prompt` here in the main loop, resolving `${...}` references against `inputs` and
`results` (e.g. `${workflow.inputs.output_dir}` → `inputs.output_dir`, `${evaluate.output.pass}` →
`results.evaluate.pass`). This is where filesystem side effects and interactive decisions
(`AskUserQuestion`) happen. Store its result into `results[node]`.

Before executing, honor the conditional / fan-out fields if the step carries them
(segment-internal `when`/`for_each` are already compiled — these apply only to orchestrator nodes):
- **`when`** — resolve the condition (an inner-of-`${...}` JS expression) against `inputs`/`results`.
  If it is false, **skip** the node: store `results[node] = null` and continue to the next step.
- **`for_each`** — resolve the expression to a collection. Run the node's `prompt` once per item,
  binding `${<as>}` to the current item (`as` is the step's loop-variable name). Collect the
  per-item results into an array and store it as `results[node]`. An empty/absent collection → `[]`.
- A step with both: evaluate `when` first; if false, skip without iterating.

The main loop is also the only place a node may invoke a **nested workflow** (e.g. a `provision`
node running `microskill-create` with the autonomous profile, once per missing microskill via
`for_each`). Background segments cannot — their subagents have no orchestration context.

### `kind: "checkpoint"`, `checkpoint_type: "nested_workflow"`
A first-class nested-workflow call — a `workflow: <name>` node. The child runs here in the main loop,
never in a segment. Honor `when`/`for_each` exactly as for an orchestrator node above (a false `when`
→ store `results[node] = null` and skip; `for_each` → run the child once per item, binding `${<as>}`,
collecting an array into `results[node]`). Then:
1. **Resolve the step's `inputs` map** — for each `key: value`, resolve any `${...}` references in
   `value` against `inputs`/`results` (same rules as an orchestrator node). The resolved map is the
   child's input set.
2. **Re-enter this same `workflow` skill for `step.workflow`** — compile
   `.claude/workflow-defs/${step.workflow}/WORKFLOW.yaml`, passing **`--profile ${step.profile}` when the
   checkpoint carries a `profile`** (a `customize: {profile}` on the `workflow:` node — e.g. `provision`
   runs `microskill-create` with the `autonomous` profile so its plan gate never pauses; omit the flag
   when absent), and run its manifest, supplying the resolved map as the child's gathered `inputs`
   (skip the interactive input-gathering step — the inputs are already provided by the parent). The
   compiler guaranteed depth ≤ 1, so the child contains no further nested call; recursion is bounded.
3. **Store the child's result** — its `manifest.output.from` node output — into `results[node]`, and
   emit a short recap (reuse the child's own wrap-up; don't replay its segments).
4. If the child fails or its evaluator never passes, **stop and surface the error** — do not claim
   success.

## Finish
If `manifest.output.from` is set, report `results[<that node>]` as the workflow's result. Then print a
one-line wrap-up: workflow name + final outcome and where the output landed. The per-segment recaps
already covered the play-by-play — don't re-summarize each segment here.

## Gate / delegation semantics (authoritative)
- A node runs in a **background segment** unless it is an orchestrator checkpoint. The compiler already
  made this split from the delegation governance — do not second-guess it at runtime.
- **Never** run an interactive step inside a segment: the Workflow tool's subagents have no
  `AskUserQuestion` and will silently fabricate. All human interaction happens at checkpoints, here.

## Failure modes
- **Unknown workflow** — no `WORKFLOW.yaml` at `.claude/workflow-defs/<name>/`. Stop.
- **Compile error** — `compile-workflow` non-zero exit. Surface `error` / `schema_errors`, stop.
- **Segment error** — a segment returns an error (fail-loud node). Stop, surface it, do not proceed.
- **Required input unresolved** — stop, name the input.
- **Gate abandoned** — stop cleanly, report partial state.
