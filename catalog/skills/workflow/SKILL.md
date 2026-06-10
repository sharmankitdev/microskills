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
3. **Compile** — run via Bash: `.claude/scripts/compile-workflow <name> [--profile "<profile>"]
   [--override <k>=<v> ...]`. The compile summary and the written manifest always carry
   `manifest_hash` (the resume check in step 5 needs it); `--explain` is optional diagnostics only.
   Non-zero exit → stop and surface the JSON `error` / `schema_errors`. (Never pass `--plan` here —
   that is a dry-run that writes nothing.)
4. **Read the manifest** — read the `manifest_path` from the compile summary
   (`.claude/workflow-defs/<name>/.compiled/manifest.json`). Note `manifest.manifest_hash`.
5. **Resume check** — all runtime state is namespaced per run under
   `.claude/workflow-defs/<name>/.compiled/runs/<run-id>/` (dispatcher runtime state, NOT a compiled
   artifact; the compiler's stale-clean only globs `seg-*.js` and `resolved/*.json` and never touches
   `runs/`). Run via Bash:
   `.claude/scripts/run-journal latest --runs-dir '.claude/workflow-defs/<name>/.compiled/runs' --manifest-hash '<manifest.manifest_hash>' --steps <manifest.steps.length>`
   It scans `runs/*/run-state.json` for the newest unfinished run (greatest run id) whose stored
   `manifest_hash` equals the current one — a mismatched hash means the workflow was recompiled /
   changed, so stored node outputs no longer line up and that run is simply not offered. Parse the JSON:
   - `found: true` → **offer to resume** via `AskUserQuestion` ("Resume run {run_id} from step
     {step_index+1}, or start fresh?"). On resume: adopt that `run_dir` as this run's directory, seed
     `inputs` from the `inputs` map in `<run_dir>/run-config.json` (skip Setup steps 6-8 — the run's
     inputs were already gathered and recorded; re-using completed work avoids re-running expensive
     opus planner segments), seed `results` from the `results` map in `<run_dir>/run-state.json`, set
     the start position `i = step_index`, and journal the pickup:
     `.claude/scripts/run-journal append --run-dir '<run_dir>' --event resume --step-index <i>`.
   - `found: false`, or the user picks "start fresh" → continue with Setup step 6 (start at `i = 0`;
     any prior run dirs are left in place as provenance).
6. **Mint the run** — run via Bash:
   `.claude/scripts/run-journal init --runs-dir '.claude/workflow-defs/<name>/.compiled/runs' --manifest-hash '<manifest.manifest_hash>' [--profile '<profile>'] [--override '<k>=<v>' ...]`
   (pass `--profile` / each `--override` exactly as passed to compile in step 3, so the run's
   provenance is recorded verbatim). It mints a fresh `run_id`, creates
   `<run_dir>/` + `<run_dir>/run-inputs/`, writes `<run_dir>/run-config.json`
   (`{run_id, manifest_hash, profile_used, overrides, inputs}`), and opens `<run_dir>/journal.jsonl`
   with a `run_start` event. Parse the JSON output and note `run_dir` — every runtime file below
   (run-state, run-inputs, journal) lives under it. (`run-journal report --run-dir '<run_dir>'`
   renders the journal human-readably for a post-mortem; `runs/` isolates *runtime* state only — two
   concurrent runs of the same def compiled with different profiles/overrides still race on the shared
   `.compiled/seg-*.js` + `manifest.json`, so never run those concurrently.)
7. **Gather inputs** — initialize `inputs = {}`. For each name in `manifest.required_inputs`: if the
   caller's prompt supplies a literal value, use it; otherwise gather via `AskUserQuestion`. Apply
   `manifest.input_defaults` for any non-required input not supplied. (Do not fall back to a default
   for a required input.)
   **Then normalize each large / multi-shape input by reference.** For every name in
   `manifest.materialize_inputs` that has a gathered value `v` (skip an optional one with no value),
   produce ONE canonical file and set `inputs[name]` to its **absolute** path — so only a short path
   ever rides in `args` (a large inline value would be silently truncated by the native engine,
   breaking `JSON.parse(args)`).
   **SECURITY — never put the value's CONTENT into a shell command.** A materialize value may be
   untrusted (a diff is attacker-controlled PR content; a requirement may come from an untrusted
   source). In a shell command even inside double quotes, `$(...)`, backticks, and `$var` still expand
   and a stray `"` breaks the quoting — so interpolating raw content (in `--value "<content>"`, a
   `printf`/`echo` pipe, or even a `test -e "<content>"` shape check) is a command-injection vector.
   Therefore decide the shape by the value's **provenance**, and never shell its bytes:
   - **`v` is a literal string / inline content** (a requirement, a pasted diff — e.g. the provision
     case) → use the **Write tool** (NOT Bash) to write `v` verbatim to `<run_dir>/run-inputs/<name>`;
     the content is a structured tool parameter, never a shell argument. That file's path is `<path>`.
   - **`v` is a filesystem path the caller supplied** (a file or directory) → that path is `<path>`
     (short and caller-chosen).
   Pass `<path>` to Bash in **single quotes** (`'<path>'`) — single quotes suppress `$(...)`, backtick,
   and `$var` expansion that double quotes do NOT — and first **reject any path containing a shell
   metacharacter** (`$`, backtick, `"`, `'`, `\`, or a newline): refuse it rather than interpolate, so
   even a trusted-but-odd path can never expand. (Dispatcher-written run-inputs paths never contain these.)
   Then run `.claude/scripts/normalize-input --value '<path>' --out '<run_dir>/run-inputs/<name>.cat'`
   via Bash (it `mkdir -p`s as needed). It receives only PATHS, never content: a **directory** → a
   byte-stable concatenation (codepoint-sorted relpaths under `=== <relpath> ===` headers, self-excluding
   `--out`); a **file** → its absolute path (pass-through, no copy). Parse the JSON `{path, shape, bytes,
   warning}`: set `inputs[name] = .path`; if `.warning` is non-null surface it (a file beyond the consuming
   node's context window needs upstream distillation — warn and proceed, never truncate). The file CONTENTS
   remain untrusted data for the consuming microskill — normalization only relocates them.
8. **Record the gathered inputs** — use the **Write tool** to write the final `inputs` object as JSON
   to `<run_dir>/inputs.tmp.json` (a structured tool parameter — input values never ride a shell
   command), then run via Bash:
   `.claude/scripts/run-journal record-inputs --run-dir '<run_dir>' --inputs-file inputs.tmp.json`.
   It folds the inputs into `run-config.json` (so the run's full provenance — profile, overrides, and
   the exact input set — is one record), journals an `inputs_recorded` event with per-input byte sizes,
   and consumes the scratch file.

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

**Persist run-state + journal after each step, then check the step's IO.** After a step completes
(and its output is stored into `results`), checkpoint in three moves:
1. Use the **Write tool** to write
   `{ "manifest_hash": manifest.manifest_hash, "step_index": <the index of the NEXT step to run>, "results": results }`
   to `<run_dir>/run-state.json.tmp` (a structured tool parameter — node outputs never ride a shell
   command).
2. Run ONE Bash call:
   `.claude/scripts/run-journal append --run-dir '<run_dir>' --event step_complete --step-index <i> --label '<the step header label>' --commit-state run-state.json.tmp`
   — for a gate step also pass `--gate '<gate.id>' --choice '<the recorded choice label>'`; for a
   skipped step (a false `when`, or a warn gate passed through) add `--outcome skipped`. The helper
   atomically promotes the tmp file to `<run_dir>/run-state.json` (tmp + `os.replace` — a crash never
   leaves a half-written state) and appends one machine-readable line to `<run_dir>/journal.jsonl`,
   recording its own timestamp and computing byte sizes by reading the committed state itself — content
   never rides argv.
3. **Post-step IO check** — run ONE Bash call:
   `.claude/scripts/check-step-io --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --run-state '<run_dir>/run-state.json' --step <i>`
   It receives only PATHS (node outputs never ride argv) and validates every value this step
   produced against the per-node `io` contract on the manifest step (`{schema, guarded, fan_out}`:
   the node's effective output_schema; `fan_out` → an array of that schema; a guarded node's `null`
   is a legal skip; an unguarded `null`/`{}` against a required-props schema = probable
   native-engine truncation or subagent fabrication). **Non-zero exit → STOP**: surface the JSON
   `errors` readably, journal a `run_error` (see below), and do not run the next step — never
   re-synthesize or repair the failed output yourself (re-running the producing step after a fix is
   the human's call). Exit 0 → continue; surface any `warnings` (e.g. an oversized result a later
   segment threads through `args` — the compiled args guard is the enforcement point there; the
   warning just names the producing node early).
This is the dispatcher's in-memory `results{}` checkpointed to disk so a run that dies mid-way can
resume (see the Setup resume check) instead of restarting from step 0 and re-running expensive segments.
It is dispatcher runtime state, quarantined from the compiled bytes — the compiler never reads `runs/`
and its stale-clean (which globs only `seg-*.js` and `resolved/*.json`) never deletes it, so determinism
is untouched. On a clean finish leave the run dir in place — it is the run's provenance record (the
`latest` scan skips finished runs via `--steps`). Do not let journaling block the run. On a stop
(segment error, failed IO check, abandoned gate, unresolvable input), record it before stopping:
`.claude/scripts/run-journal append --run-dir '<run_dir>' --event run_error --step-index <i> --outcome error --label '<short reason>'`.

### `kind: "segment"`
1. Build the `args` object the segment expects — **every needed key must be PRESENT; use `null`,
   never omit a key** (the compiled segment fail-louds on an absent needed key and on unparseable
   args; a `null` value is legal — presence, not truthiness):
   - for each `n` in `step.needs.wf_inputs` → `args["wf_" + n] = inputs[n]` (an optional input with
     no gathered value and no default → set `null` explicitly)
   - for each `id` in `step.needs.nodes` → `args[id] = results[id]` (the upstream node's output; a
     skipped node's stored `null` rides as `null`)
   - for each `v` in `step.needs.carry` → `args["carry_" + v] = null` (loops carry state internally
     across their own iterations; the seed is null)
2. Invoke the **Workflow tool** with `scriptPath` = `step.script` **resolved against the def dir**:
   the manifest stores it relative to the def dir (e.g. `.compiled/seg-1.js` — portable across
   checkouts), so the path to pass is `.claude/workflow-defs/<name>/<step.script>`. Pass
   `args = <the object above>`. The segment runs autonomously in the background on the native engine.
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
(`AskUserQuestion`) happen. Store its result into `results[node]`. **When the step's `io[<node>]`
carries a non-null `schema`, that is the node's declared RETURN CONTRACT**: store an object with
exactly those fields (the post-step `check-step-io` validates it) — never a prose summary in its
place.

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
   (skip the interactive gathering — the inputs are already provided by the parent; the child still
   mints its OWN run dir under its own def's `.compiled/runs/` and records them, Setup steps 6-8). **Still
   run the normalization pass (Setup step 7) over the child's `manifest.materialize_inputs`** before its
   first segment: a parent may pass a raw string into the child's `materialize: file` input (e.g.
   `provision` hands `microskill-create` a `requirement_path` whose value is the per-microskill
   requirement *string* from the plan), and that string must be written to a file so only a path reaches
   the child's segment args. A value that is already a path hits the file rule (pass-through). The
   compiler guaranteed depth ≤ 1, so the child contains no further nested call; recursion is bounded.
3. **Store the child's result** — its `manifest.output.from` node output — into `results[node]`, and
   emit a short recap (reuse the child's own wrap-up; don't replay its segments).
4. If the child fails or its evaluator never passes, **stop and surface the error** — do not claim
   success.

## Finish
Close the journal: `.claude/scripts/run-journal append --run-dir '<run_dir>' --event run_complete
--outcome ok`. If `manifest.output.from` is set, report `results[<that node>]` as the workflow's
result. Then print a one-line wrap-up: workflow name + final outcome and where the output landed
(mention `run-journal report --run-dir '<run_dir>'` as the run's timeline). The per-segment recaps
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
- **Step IO check failed** — `check-step-io` exits non-zero after a step (schema violation, missing
  result, or the probable-truncation/fabrication signature). Stop, surface its `errors`, never
  synthesize a replacement output.
- **Required input unresolved** — stop, name the input.
- **Gate abandoned** — stop cleanly, report partial state.
