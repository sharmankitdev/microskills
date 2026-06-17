---
name: workflow
description: >
  Use to execute a registered workflow. Triggered via `/workflow <name> [profile] [args]`,
  or implicitly via auto-generated per-workflow slash shims under `.claude/commands/<name>.md`
  that delegate here. Reads `.claude/workflow-defs/<name>/WORKFLOW.yaml`, compiles it via
  `.claude/scripts/compile-workflow`, then orchestrates the resulting background segments and
  human checkpoints per the run manifest. Also handles `/workflow <name> [profile] --plan`
  (preflight: render steps/gates/inputs/executors from the dry-run compile summary, no execution)
  and `/workflow <name> rerun [--from <step|node>]` (deterministic partial re-run of a recorded
  run on its frozen recorded inputs) and `/workflow <name> pickup [--run <run-id>]` (interactive
  continuation of a PARKED gate-mode=auto run from its committed step).
---

# workflow

Orchestrator Skill (not a microskill). A workflow is a declarative DAG of microskill/agent nodes.
`compile-workflow` partitions it into **autonomous background segments** (compiled Claude Code
Workflow-JS) separated by **orchestrator checkpoints** (human-approval gates and orchestrator-native
nodes). This skill is the conductor: it runs each segment on the native Workflow engine, runs each
checkpoint in the main loop (where `AskUserQuestion` works), and threads outputs forward via `args`.

**The autonomous engine cannot pause for a human** — so all interaction lives here, between segments.

**Four invocation modes, routed on the args before anything runs:**
- **execute** (default) — Setup → Execute the manifest → Finish, below.
- **preflight** — `--plan` anywhere in the args → the **Preflight** section: render the compiled
  plan (steps, gates, inputs, executors) and STOP before input gathering. Writes nothing, mints
  no run, asks no questions.
- **rerun** — the literal token `rerun` directly after the name → the **Rerun** section:
  re-execute a recorded run from a chosen step on its FROZEN recorded inputs.
- **pickup** — the literal token `pickup` directly after the name → the **Pickup** section:
  continue a PARKED gate-mode=auto run interactively from its committed step, in the same run dir.

## Setup

**Conductor voice (applies to ALL conductor output, every mode).** You are the conductor;
the **bookkeeper** subagent runs the plumbing and its tool calls never reach this
transcript. Your prose must never contain: run-ids, manifest hashes; `run_dir` / `.tmp` /
`.cat` / `.compiled` / `seg-N.js` paths; CLI command names or argv (`run-step`,
`check-step-io`, `run-journal …`, `--commit-state`, `--mark-failed-step`); process phrases
("Committing state", "IO check passed", "Minting the run", "Recording inputs", "Building
the segment args", "Resolving … against committed run-state"); raw JSON, byte counts, or
schema field names; an internal node id when the step carries a `label`. **Do** emit: plain
outcomes ("Saved.", "Ready.", "Done."); the `▶ Step g/T · <label>` cursor; artifact
references by purpose **with the user-facing product path** they'd open (a `/tmp/...`
output path is fine — it is the user's artifact, not runtime plumbing); recaps that
synthesize. A bookkeeper digest with `ok:false` → surface its meaning in plain language and
stop (don't paste the JSON).

1. **Name / profile / overrides** — parse `<name>` (position 1), `<profile>` (slash
   position 2 or "with <profile> profile"), `override workflow-config:` clauses, and the
   args headless signal (`--gate-mode auto` / `--headless`). No `WORKFLOW.yaml` for `<name>`
   → stop and report.
2. **Open the run (bookkeeper).** Dispatch the bookkeeper with
   `{op:"open", name, profile, overrides, headless_from_args}`. It compiles, reads the
   manifest, and scans for a resumable run. Note the digest's `manifest_hash`, `gate_mode`
   (authoritative for the whole run — auto means no `AskUserQuestion` anywhere; the Pickup
   exception below still applies), the roadmap fields, and `resume`. `ok:false` → surface
   `error` and stop.
3. **Resume offer.** `resume.found` and not auto mode → offer via `AskUserQuestion` in the
   conductor's voice ("Looks like a previous run stopped at step {step_index+1}…"; if
   `failed_step` is non-null, say that step's last attempt failed and resuming re-runs it).
   On resume → dispatch `{op:"resume", name, run_dir: resume.run_dir, mode:"resume"}`, set
   `i = step_index`, skip steps 4–5, go to Execute. Under auto mode never offer — start
   fresh.

   **Announce the run (conductor opening):**

   The manifest is loaded — this is the main loop, never a segment, so it is
   the one place to set the scene. Print one opening beat — the only place a watcher learns what the
   whole run will do:
   - **Title:** `🛠  <name> — <manifest.description>`. If `manifest.gate_mode == "auto"`, append
     ` (headless — gates take their declared defaults)`.
   - **Roadmap:** one compact line per `manifest.steps[i]`, numbered `1..M`, each showing the step's
     label (resolved per the cursor rule in **Execute the manifest** below), tagging human gates with
     `⏸ you decide` and nested workflows with `▸ nested`. A scannable list, not prose.
   - **Inputs:** if inputs will be gathered, name them in one line.

   Ephemeral: printed once, never journaled, never written to run-state. (You MAY mirror the
   Preflight render logic in the **Preflight** section for consistency, but do not merge the modes —
   Preflight halts before gathering; this opening proceeds to gather inputs and run.)
4. **Gather inputs** — `inputs = {}`; for each `required_inputs` name use the caller's
   literal value or `AskUserQuestion`; apply `input_defaults` for unsupplied non-required.
   Auto mode + a missing required input → stop immediately with a clear plain-language error
   naming the missing input (no run has been minted yet, so there is nothing to journal — do
   not dispatch `op:fail`).
   Build the `materialize` list: for each `materialize_inputs` name with a value, an entry
   `{name, provenance:"inline"|"path", value}` (inline = a literal string/pasted content;
   path = a filesystem path the caller gave).
5. **Record the run (bookkeeper).** Dispatch `{op:"record", name, manifest_hash, profile,
   overrides, gate_mode, inputs, materialize}`. It mints the run, materializes/normalizes,
   and records inputs (seeding run-state). Note `run_dir` and the returned `inputs` (with
   materialized paths). `ok:false` → surface `error` and stop.

## Preflight — `/workflow <name> [profile] --plan`

A dry-run renderer: show what a run WOULD do — steps, gates, inputs, executors — without writing
artifacts, minting a run, or gathering a single input. Run Setup steps 1-2 (name, profile /
overrides, headless signal), then:

1. **Compile the plan (bookkeeper).** Dispatch `{op:"preflight", name, profile, overrides,
   headless_from_args}`. It dry-run compiles and returns `{ok, summary}` carrying the full
   plan; a preflight that fails to compile IS the useful answer, so `ok:false` → surface its
   `error` in plain language and stop.
2. **Render EXCLUSIVELY from the digest's `summary`.** It embeds the FULL manifest object under
   `manifest` plus the executor entries under `classification` — render only from this digest,
   never from any on-disk manifest (the dry-run compile wrote nothing; anything on disk is from
   an earlier compile and may not match these flags). Render, in order:
   - **Header** — name, `profile_used`, `manifest_hash`, and the run mode:
     `manifest.gate_mode == "auto"` → "headless — gates take their declared defaults", else
     "interactive".
   - **Inputs** — each `manifest.required_inputs` name (marked required — gathered from the caller
     or interactively at run time), each `manifest.input_defaults` entry with its default, each
     `manifest.materialize_inputs` name marked "by reference (`materialize: file`)".
   - **Steps** — walk `manifest.steps` printing `Step {i+1}/{M}: {label}` headers (the same label
     synthesis as the execution walk), each with detail lines:
     - segment → the node ids in order, one line per node from the matching
       `classification[].executor`: `<id> — agent <agent> (model <model>, profile <profile>)`,
       rendering only non-null fields (an `agent:` node shows the agent type alone — its model
       rides the agent definition). `is_loop` → append "(loop)" to the header.
     - gate → severity (`hard` pauses; `warn` renders + continues), the `prompt`, the options
       (declared `options`, or the implicit `confirm / stop` pair), `conditional (when)` for a
       step-level `when` (the compiler-emitted `loop_exhaust` exhaustion gate — note it was not
       authored: "fires only when the loop exits unconverged"), and the headless behavior:
       the declared **`default` is the auto-mode choice** — render `auto mode takes '<default>'`;
       `on_headless: fail` → `auto mode STOPS here (declared hand-off)`; a pausing gate with
       neither → `interactive-only (an auto compile refuses this gate)`.
     - orchestrator_node → `main loop: <node id>`, noting `conditional (when)` and a `for_each`
       fan-out when declared.
     - nested_workflow → `nested workflow: <workflow>` plus `(profile <profile>)` when carried.
   - **Output** — `manifest.output.from`, when set.
3. **STOP.** A preflight ends here, before input gathering: no run dir, no `AskUserQuestion`, no
   filesystem writes, no segments. To execute, re-invoke without `--plan`.

## Rerun — `/workflow <name> rerun [--from <step|node>] [--run <run-id>]`

Deterministic partial re-execution of a RECORDED run from a chosen step, on the run's FROZEN
recorded inputs. **Scope honesty: rerun is NOT the fail→fix→re-review loop** — it replays the
recorded input set verbatim. Re-reviewing a *changed* artifact (a fixed diff, an edited
requirement) needs a changed input, which is a NEW run, never a rerun.

1. **Locate the recorded run (bookkeeper).** Dispatch `{op:"rerun-locate", name, run?}` (`run`
   is the `--run <run-id>` value when the caller gave one, else omitted — it picks the newest
   run with a committed run-state, FINISHED runs included, since a finished run is rerun's normal
   case). `found:false` → stop: nothing recorded to rerun. Otherwise note the digest's `run_id`,
   `manifest_hash`, `profile_used`, `overrides`, and `gate_mode`. A non-null `failed_step` caps
   the from-point: that step failed its IO contract, so the seeder (step 3) refuses any `--from`
   beyond it — tell the user up front. `ok:false` → surface `error` and stop.
2. **Recompile with the RECORDED provenance (bookkeeper).** Dispatch `{op:"open", name,
   profile: <profile_used>, overrides: <recorded overrides>, headless_from_args: <gate_mode ==
   "auto">}` — the recorded provenance, never this invocation's own flags (a rerun reproduces
   the recorded compile). If the digest's `manifest_hash` differs from the recorded one → **STOP:
   rerun REQUIRES manifest_hash equality.** The def, registry, or profile changed since the
   recorded run, so its stored node outputs no longer line up — start a fresh run instead. Never
   improvise a partial reuse, and never retry the compile with different flags to chase the hash.
3. **Seed the rerun (bookkeeper).** Dispatch `{op:"rerun-seed", name, source_run: <run_id>,
   from?}` (`from` is the `--from <step|node>` value when given). It re-checks hash equality (the
   authoritative gate), resolves the from-point — an integer is a 0-based manifest step index; a
   name matches a gate id, a checkpoint node id, or a node INSIDE a segment, which **snaps to the
   segment start** (segments are atomic: the whole segment re-runs) — requires the source run to
   have committed every step before it, and mints a NEW run dir seeded with the recorded `inputs`
   and every pre-from result (results at/after the from-point are dropped, so a stale later output
   can never leak forward; the source run dir is provenance — never modified). `ok:false` → stop
   and surface its `error`. Otherwise note `run_dir`, `from_step_index`, `snapped` (`true` → tell
   the user their node-level from-point widened to the whole segment), `replayed_gates`, and
   `confirm_steps`.
4. **Replay, never re-ask** — print one line per `replayed_gates` entry:
   `Gate <id>: replaying recorded choice '<choice>'`. Those gates sit BEFORE the from-point; their
   recorded `{choice}` results are already seeded into the run-state, so downstream branches read
   them — do not re-present them. A replayed `loop_exhaust` whose recorded choice was `extend` is a
   CHOICE replay only — never re-act on it (the seeded run-state already carries the
   post-extension `results.loop`/carry).
5. **Skip the Setup input-gathering steps entirely** — the inputs are FROZEN from the record and
   already seeded into the new run's state by `rerun-seed`, including materialized paths that
   point into the SOURCE run's dir (this is why finished run dirs are retained). Adopt `run_dir`
   as the opaque token (never printed) and set `i = from_step_index`; the seeded on-disk run-state
   is authoritative, so the execute walk's `prep`/`commit` ops read and advance it — the conductor
   holds no separate copy. The conductor opening for a rerun reflects *resuming partway*: announce
   that this is a rerun of an earlier run from step `{i+1}`, and render the roadmap with the
   pre-from steps marked as already-recorded (the `replayed_gates` lines from step 4 are part of
   that opening) — the cursor then starts at `Step {i+1}`, never at Step 1.
6. **Execute the manifest** from `i` exactly as a normal run (the Execute-the-manifest walk
   below — same `prep`/segment/gate/`commit` loop), with ONE addition: a step listed in
   `confirm_steps` (an orchestrator_node / nested_workflow checkpoint) **re-executes side
   effects** — filesystem writes, vendoring, nested child runs that already happened once in the
   source run. Before executing such a step, confirm via `AskUserQuestion` ("Step {i+1} re-runs
   '<node>', re-executing its side effects. Re-run it, or stop?"). A "stop" → dispatch
   `{op:"fail", name, run_dir, step:<i>, label:'rerun declined at <node>'}` and stop cleanly.
   Under auto mode there is no human: proceed WITHOUT the confirmation — the explicit `rerun`
   invocation is the consent — and let the step commit normally. Gates at/after the from-point
   re-present normally (fresh choices).

## Pickup — `/workflow <name> pickup [--run <run-id>]`

Interactive continuation of a PARKED gate-mode=auto run — one that stopped with committed
run-state at an `on_headless: fail` gate (the `loop_exhaust` exhaustion gate is the marquee case:
an overnight run parked unconverged, extended with morning guidance) or at an
interaction-requiring orchestrator node. Pickup is the second half of the declared "do the work
overnight, approve in the morning" pattern: the auto-committed prefix stays exactly as recorded;
only the NOT-YET-RUN suffix executes in this session, with interactive gate handling. Pickup
**requires a human**: under a headless invocation (auto gate mode / `MICROSKILLS_HEADLESS`),
refuse with a nonzero outcome — a headless pickup is a contradiction in terms.

1. **Locate the parked run (bookkeeper).** Dispatch `{op:"pickup-locate", name, run?}` (`run`
   is the `--run <run-id>` value when given, else omitted — it picks the newest committed run,
   ANY compile, since a parked auto run's hash never matches an interactive compile, which is
   exactly why the normal resume scan cannot see it). `found:false` → stop: nothing to pick up.
   Otherwise note the digest's `run_id`, `manifest_hash`, `profile_used`, `overrides`,
   `gate_mode`, `step_index`, and `failed_step`. `ok:false` → surface `error` and stop.
2. **Recompile with the RECORDED provenance (bookkeeper).** Same move as Rerun step 2: dispatch
   `{op:"open", name, profile: <profile_used>, overrides: <recorded overrides>,
   headless_from_args: <gate_mode == "auto">}` — the recorded provenance. The digest's
   `manifest_hash` must EQUAL the recorded one, else **STOP: the def, registry, or profile
   changed since the run parked** — its stored results no longer line up; start a fresh run.
   Never improvise a partial reuse.
3. **Sanity-check the park** — `step_index >= manifest.steps.length` → the newest run already
   finished: report that and suggest `--run` for an older parked run. The recorded `gate_mode`
   not `"auto"` → this is an ordinary interrupted interactive run: hand off to the normal resume
   offer instead. A non-null `failed_step` equal to `step_index` → surface it (picking up re-runs
   that step). **`step_index > failed_step` (a POISONED record — the `--run` path bypasses the
   latest-scan filter that screens these) → REFUSE**: no valid protocol commits past a failed IO
   check, so the stored results beyond it are untrustworthy; continuing would thread forward
   exactly the values the IO gate quarantined. Use `rerun --from <failed_step>` (or earlier), or
   start fresh.
4. **Adopt the run dir IN PLACE (bookkeeper).** Pickup continues the SAME run (no new run dir —
   unlike rerun, nothing is replayed): dispatch `{op:"resume", name, run_dir, mode:"pickup"}`,
   which journals the pickup mode-transition event and returns `step_index`/`gate_mode`. Set
   `i = step_index` and adopt `run_dir` as the opaque token; the on-disk run-state stays
   authoritative, so the execute walk's `prep`/`commit` ops read and advance it. The conductor
   opening for a pickup reflects *resuming partway*: announce that you're picking up a parked run
   at step `{i+1}` (the parking gate), now interactive, and render the roadmap with the
   already-committed prefix marked done — the cursor starts at `Step {i+1}`, never at Step 1.
5. **Execute the manifest** from `i` with ONE override: **gate handling is interactive for this
   session.** `manifest.gate_mode: "auto"` stays stamped (the manifest — and its hash — are
   untouched), but the auto-mode gate rules are SUSPENDED: every remaining gate, starting with the
   parking gate at step `i`, renders its evidence and asks via `AskUserQuestion`; an
   interaction-requiring orchestrator node runs normally. A remaining `nested_workflow` checkpoint
   compiles its child WITHOUT `--gate-mode auto` (the session-interactive override propagates,
   exactly like auto inheritance does — see the nested checkpoint section). The full extend
   protocol is available at a picked-up `loop_exhaust` gate. Inputs are already recorded — never
   re-gather. **Scope honesty: pickup continues the run it is invoked on.** A park INSIDE a nested
   child (the child run stopped at its own gate, the parent errored at the nested step) is not
   resumable THROUGH the parent — picking the parent up re-enters the child afresh (now
   interactive, so it completes, at the cost of re-running the child); the child's own parked run
   dir stays as provenance. Picking up the CHILD def directly completes the child's run, but the
   parent cannot adopt that result.
6. **Finish normally** (`run_complete`). The journal's `pickup` event records the mode
   transition; the committed prefix is never recomputed.

**Why this is sound despite "interactive run-state never resumes into a headless run":** that
rail prevents mode-mixing across DIFFERENT compiles — `gate_mode` rides inside `manifest_hash`
precisely so two compiles with diverging gate behavior never share state. Pickup recompiles the
IDENTICAL auto manifest (hash-equal by step 2), so topology, semantics, and every committed
result line up; only the runtime gate handling of steps that have NOT yet run changes, on an
explicit human invocation. Committed history is never recomputed — with one sanctioned exception:
an `extend` choice at the picked-up `loop_exhaust` gate re-runs the loop segment and re-commits
its results through the extend protocol, exactly as it would in any interactive session.

## Execute the manifest

Maintain `results = {}` (node id → that node's returned output object). Let `M = manifest.steps.length`
and let `T` be the total step count of the journey announced at the opening (`T = M` for a top-level
run; under nesting it is the parent's total — see the nested-workflow section). Track the 0-based
position `i` as you walk.

**Before running each step, print the cursor:** `▶ Step {g}/{T} · {label}`, advancing the user along
the announced roadmap, where `{g}/{T}` is the GLOBAL position in the journey (`{g} = i+1` for a
top-level run; under nesting it threads the parent ordinal — see the nested-workflow section).
Choose ONE cursor form at the opening — the global `{g}/{T}` counter or the dotted `{parent}.{k}`
breadcrumb — and keep it for the whole journey, top level and nested alike; never switch schemes
mid-run, so the user always reads one consistent "where am I".

Resolve `{label}` (the manifest now stamps one on every step — the compiler always stamps an authored
`name` or a humanized id):
1. the step record's `label` field — use it.
2. if absent, fall back to the kind→label synthesis: segment → its node ids as an action (`plan` →
   "Plan", a loop → "Implement & evaluate (loop)"); gate → its label; orchestrator_node → its node
   id as an action (`finalize` → "Finalize", `provision` → "Provision missing microskills");
   nested_workflow → the child workflow as an action (`build` → "Build (nested workflow)").

For a multi-node segment whose stamped `label` is a long ` & `-joined string, you MAY use the
concise kind→label synthesis for the cursor header and save the per-dimension names (the step's
`node_labels`) for the recap. A skipped step (a `warn` gate, an orchestrator node whose `when` is
false, or the conditional `loop_exhaust` gate after a converged loop) still gets a cursor line,
marked `(skipped)`.
Walk the steps in order, starting at the resume position `i` from Setup (default 0). You hold
`run_dir` as an opaque token (never printed) and pass it to every bookkeeper op. After Setup (or a
resume), reach the first step with one initial `{op:"prep", name, run_dir, step:i}`.

**After a step's main-loop work, persist via the bookkeeper.** Dispatch
`{op:"commit", name, run_dir, step:<i>, results:<the produced node(s) verbatim>, gate?, outcome?, label}`
— `results` is the fresh node output(s) this step produced (the bookkeeper merges them into the
on-disk results map; you do not relay the whole accumulated map), `gate:{id,choice}` only at a gate,
`outcome:"skipped"` only for a skipped step, `label` the resolved step label.
- **SUCCESS (`{ok:true}`) → print NOTHING.** The recap and the cursor advancing to the next step are
  the signal that the step landed; the bookkeeping is plumbing the user never sees.
- **`{ok:false}` → the step's output failed its contract.** Say so in plain language — "Step {i+1}'s
  output didn't meet its contract — stopping; it'll re-run on resume." — and STOP. Do NOT continue,
  do NOT repair or re-synthesize the output (re-running the producing step after a fix is the human's
  call; the bookkeeper has already stamped the failed step so a later resume re-runs it, never
  threading the corrupt value forward). Loud failures stay in the conductor.

Then, for the NEXT step, dispatch `{op:"prep", name, run_dir, step:<i+1>}` and branch on the digest's
`kind` — `segment` / `gate` / `orchestrator_node` / `nested_workflow` (the subsections below), or
`{kind:"done"}` (no more steps → go to **Finish**). A `prep` digest with `ok:false` → surface its
meaning in plain language and stop.

This in-memory `results` is checkpointed to disk by each `commit` so a run that dies mid-way can
resume (see the Setup resume offer) instead of restarting from step 0 and re-running expensive
segments. It is runtime state the bookkeeper quarantines from the compiled bytes — determinism is
untouched. On a clean finish the run dir stays in place: it is the run's provenance record and a
rerun's seed, so retention costs nothing.

### `kind: "segment"`
1. The `prep` digest gave `{script, args, label, node_labels, produces, is_loop}`. The args are
   already built against the committed run-state — pass them through verbatim; never hand-assemble,
   trim, or re-inline anything.
2. **Print the cursor** (the `▶ Step {g}/{T} · {label}` line) and a one-line "working…" intent, then
   invoke the **Workflow tool** with `scriptPath = .claude/workflow-defs/<name>/<script>` and
   `args` **verbatim** from the digest. The segment runs autonomously in the background on the native
   engine.
3. On return, its value is an object keyed by the node ids in `produces`. **Recap the segment as a
   conductor** — brief the user on what it produced and what it means next (the rubric below). A real
   briefing, not a status line: say what was accomplished and surface the judgment calls / what's
   worth attention, matching the result's shape — a plan's name + shape; a verdict's PASS/FAIL +
   issues; the staged files and what they are; a loop's verdict + how it got there + open issues. For
   a review/fan-out segment, name the dimensions from the digest's `node_labels` (e.g. "across
   User-value completeness, Edge-case coverage, NFRs, Testability") and summarize the findings.
   - **Loop segments** (`is_loop`) → summarize the journey, not each round: the round count
     (`<returned>.loop.rounds` when the step declares `on_exhaust: escalate`, else
     `<returned>.__rounds` — same number, one source of truth per mode) and the evaluator's final
     verdict — e.g. `Implement/evaluate loop done in 2 round(s) — verdict PASS, K staged files.` or
     `Implement/evaluate loop done in 3 round(s) — verdict FAIL at the round cap; N issues open.` An
     extend re-run names the extension ("extension 1: 3 more round(s) — …").
   - **Guardrails (unchanged):** never paste raw JSON, plan-file contents, or object arrays —
     synthesize them. A `null` produced node (a guarded/skipped node) → say so in one clause, don't
     invent output. On a fail-loud error, skip the recap and report the failure (see move 5).

   Ephemeral voice — printed, never recorded.
4. Dispatch `{op:"commit", name, run_dir, step:<i>, results:<the returned object, verbatim>, label}`.
   A loop step's `produces` includes `loop` (the pseudo-result `{converged, rounds, carry}`) — it
   rides in the returned object, so pass it through too; the conditional `loop_exhaust` gate and any
   extend re-entry read it from the committed state. Then advance per the commit-dispatch framing
   above (`{ok:true}` → silent; `{ok:false}` → the step failed its contract, stop).
5. A **`Workflow` error** (a fail-loud node) → skip the recap, surface the error in readable form,
   and stop — do not fabricate a result, do not commit.

### `kind: "checkpoint"`, `checkpoint_type: "gate"`
A human-approval / hard gate. The `prep` digest gave `{gate, when, skipped, evidence[], gate_mode}` —
the evidence is **already resolved** (the bookkeeper read every `gate.present` entry, including any
`{read_file}` it Read), so you only RENDER it; you never re-resolve, reorder, or substitute it.

**Conditional skip first** — `skipped:true` (a converged `loop_exhaust` gate; authored gates never
carry a `when`) → dispatch
`{op:"commit", name, run_dir, step:<i>, results:{<gate.id>:null}, outcome:"skipped", label}` (the
guarded-null convention — a later `${loop_exhaust.output.choice}` branch must sit behind a converged
check, e.g. `${loop.output.converged || loop_exhaust.output.choice == 'accept'}`), print the cursor
line marked `(skipped)`, and continue — the gate never renders. `skipped:false` → render and ask
below.

A gate is two layers — conductor framing AROUND a deterministic evidence core. Keep them distinct.

**(a) Conductor framing (ephemeral, may vary run to run):** open with a brief intro that names the
gate by its `gate.label` — "We've reached the *{gate.label}* gate. Here's what was produced and what
I'm asking you to decide." — then render the evidence core (b) UNCHANGED, then close with a one-line
framing of the choice ("So: continue with this plan, send it back for revision, or stop?"). This
framing layer NEVER alters, reorders, summarizes, or substitutes the evidence below, and never
changes the recorded choice.

**(b) Evidence core (VERBATIM — approval-integrity invariant).** Print each `evidence[]` entry in the
digest, in order — never reorder, synthesize, or substitute (resolution happened in the bookkeeper;
you only render). By the entry's `kind`:
- `scalar` → `**<label>**: <value>`.
- `json` → the `value` in a fenced ```json block.
- `file` → the `contents` in a fenced block, language from `lang` (show it in full — present is the
  author's explicit ask). A `scalar` with value `(not produced)` prints as-is — never invent a value.

**Auto mode (`gate_mode == "auto"`, except a Pickup session — there every gate asks) — never
`AskUserQuestion` at a gate:**
- `gate.on_headless == "fail"` → dispatch `{op:"fail", name, run_dir, step:<i>, label:'gate <id>
  on_headless:fail'}` and **STOP with a nonzero outcome, naming the gate**. The committed run-state is
  resumable interactively later — that is the declared "do the work, then hand off to a human" pattern.
- otherwise take **`gate.default`** (compile guarantees a pausing gate declares one under auto) — the
  author-declared label **VERBATIM**, never a re-phrasing. Print one line `Gate <id>: auto — taking
  declared default '<default>'`, dispatch `{op:"commit", name, run_dir, step:<i>,
  results:{<gate.id>:{choice:<gate.default>}}, gate:{id:<gate.id>, choice:<gate.default>}, label}`,
  then act on that recorded choice per the mapping below — with one exception: a `revise`/`extend`
  default cannot run headless (revise can't gather notes; an `extend` default would re-enter the loop
  unboundedly — compile refuses `on_exhaust.default: extend`, so only a hand-edited manifest yields
  one) → dispatch `{op:"fail", …}` and stop, naming the gate.
- a pausing gate with NO `default` (a hand-edited manifest — compile never emits this) → dispatch
  `{op:"fail", …}` and stop. **Never pick an option yourself.**

**Interactive mode** — ask via `AskUserQuestion`: `gate.prompt` is the question, `gate.options` the
choices (default `confirm / stop`). **Give each option a one-line `description` of its consequence**
— `approve`/`confirm` → "Continue to the next step."; `revise` → "Re-run this segment with your
notes."; `abandon`/`stop` → "Stop the run and clean up staging." (map other labels to the nearest).

**Record the human's pick** via `{op:"commit", name, run_dir, step:<i>,
results:{<gate.id>:{choice:<pick>}}, gate:{id:<gate.id>, choice:<pick>}, label}` — the chosen option's
label, verbatim. A gate id is a legal `${...}` ref target: a later node/segment whose `when` or
`inputs` reads `${<gate-id>.output.choice}` resolves against this stored object (the gate-choice
branching feature validate-workflow accepts). Always record the pick, then act:
- An `approve`/`confirm` choice → continue to the next step.
- A `revise`-style choice → **ask the user what to change** (a follow-up `AskUserQuestion` or their
  free-text note), then re-run the segment that produced the gate's `after` node: dispatch
  `{op:"prep", name, run_dir, step:<that segment's i>}` to rebuild its args, **fold the revision
  notes into the relevant `args` value conductor-side** (e.g. append them to the `requirement` arg),
  re-invoke the segment's script with the folded args, recap, dispatch the segment's
  `{op:"commit", …}` for the refreshed result, then re-present this gate (re-recording the choice on
  the re-presented pick).
- An `extend` choice (the `loop_exhaust` gate) → re-run the LOOP segment with the committed carry and
  a fresh round budget, in this order (every fold lands on disk first):
  1. **Fold guidance first** — when the loop step declares `on_exhaust.notes_input`, ask the user (a
     follow-up `AskUserQuestion` or their free-text note) and dispatch
     `{op:"fold-guidance", name, run_dir, notes_input:<that input name>, notes, extension_n:<N>}`
     (the bookkeeper appends the notes to the materialized file or string input and commits the
     inputs-only state, no step advance).
  2. **Rebuild args with the extend flag** — dispatch `{op:"prep", name, run_dir, step:<loop i>,
     extend:true}`: the kernel seeds every declared `carry_<v>` from the committed `results.loop.carry`
     only under extend.
  3. Re-invoke the loop segment's script, recap (cumulative extensions are bookkeeping: say
     "extension N"), then dispatch `{op:"commit", name, run_dir, step:<loop i>, results:<the refreshed
     returned object incl. loop, verbatim>, label}` for the loop step's refreshed results.
  4. **Exit by verdict** — still unconverged → re-present this gate. Converged → the human's real pick
     stays recorded (choice stays `extend`, never overwritten with a skip-null), and continue to the
     next step.
- An `abandon`/`stop` choice → stop the run cleanly (clean up any staging the segments created).
For `severity: warn` gates (any mode), render the evidence + emit the prompt, then continue without
pausing — dispatch `{op:"commit", name, run_dir, step:<i>, results:{<gate.id>:{choice:<gate.default>}},
gate:{id:<gate.id>, choice:<gate.default>}, outcome:"skipped", label}` when the gate declares a
`default` (the author-declared label, verbatim), else `results:{<gate.id>:{choice:null}}` with no
`gate` key. **Never record an option nobody selected**: the recorded choice is always author-declared
or null (validate forces a `default` on any warn gate whose choice is branched on, so a downstream
branch always resolves against a real label).

### `kind: "checkpoint"`, `checkpoint_type: "orchestrator_node"`
An orchestrator-native step (a node with neither `use` nor `agent`, or `delegation: orchestrator`).
The `prep` digest gave `{node, prompt|iterations, skipped, io_schema, gate_mode}` — the step's
`when`/`for_each`/`${...}` refs are **already resolved** (the bookkeeper evaluated them; never
evaluate an expression or substitute a ref in your head).
- `skipped:true` (a false `when`) → dispatch `{op:"commit", name, run_dir, step:<i>,
  results:{<node>:null}, outcome:"skipped", label}`, print the cursor line marked `(skipped)`, and
  continue — do not execute the prompt.
- Else **execute the resolved `prompt` here in the main loop.** This is the node's WORK — filesystem
  side effects and interactive decisions (`AskUserQuestion`) as the node needs; these tool calls are
  legitimate work, not plumbing, and DO show. `for_each` → `iterations` is the resolved fan-out:
  execute each `iterations[k].prompt` once, in order, and collect the per-item results into an array
  (empty `items` → `[]`, never null). When `io_schema` is non-null it is the node's declared RETURN
  CONTRACT: the result must be an object with exactly those fields (the commit's IO check validates
  it) — never a prose summary in its place.
- Then dispatch `{op:"commit", name, run_dir, step:<i>, results:{<node>:<result>}, label}` and
  advance per the commit-dispatch framing.

**Auto mode (`gate_mode == "auto"`): `AskUserQuestion` is unavailable — there is no human.** A prompt
that REQUIRES asking the user (it instructs an interactive loop, or a decision only a human can make)
→ dispatch `{op:"fail", name, run_dir, step:<i>, label:'<node> needs a human'}` and STOP with a
nonzero outcome, naming the node; never answer on the user's behalf (a fabricated answer is worse than
a stop). A prompt that needs no human input executes normally. (Example: `refine-requirements`' base
`clarify` node is NOT headless-able — its `autonomous` profile, which rewrites the prompt to an
unattended single pass, is the supported unattended path.)

The main loop is also the only place a node may invoke a **nested workflow** (e.g. a `provision`
node running `microskill-create` with the autonomous profile, once per missing microskill via
`for_each`). Background segments cannot — their subagents have no orchestration context.

### `kind: "checkpoint"`, `checkpoint_type: "nested_workflow"`
A first-class nested-workflow call — a `workflow: <name>` node. The child runs here in the main loop,
never in a segment. The `prep` digest gave `{node, workflow, profile, child_inputs|iterations, skipped}`
— the declared `inputs` map is **already resolved and cross-checked against the child's required
inputs** (the bookkeeper did this; an uncovered required child input would have surfaced as
`ok:false`).
1. `skipped:true` (a false `when`) → dispatch `{op:"commit", name, run_dir, step:<i>,
   results:{<node>:null}, outcome:"skipped", label}`, print the cursor line marked `(skipped)`, and
   continue — never enter the child.
2. **Re-enter this same `workflow` skill for `workflow`**, passing **`--profile <profile>` when the
   checkpoint carries one** (a `customize: {profile}` on the `workflow:` node — e.g. `provision` runs
   `microskill-create` with the `autonomous` profile so its plan gate never pauses; omit when absent),
   and **(auto mode, non-Pickup) `--gate-mode auto`** so the child inherits headless mode — except
   under a Pickup session, where the human is present and the child's gates ask inline, so the flag is
   NOT passed. Supply `child_inputs` as the child's gathered inputs (skip the interactive gathering —
   they're already provided by the parent). The child runs its OWN conductor+bookkeeper pair: it mints
   its own run dir under its own def, and its `record` op materializes any raw-string child
   `materialize: file` inputs (e.g. `provision` hands `microskill-create` a `requirement_path` whose
   value is the per-microskill requirement *string* from the plan — written to a file so only a path
   reaches the child's segment args; a value already a path passes through). Depth ≤ 1 is enforced at
   compile time, so the child contains no further nested call; recursion is bounded. `for_each` →
   `iterations` carries one resolved `child_inputs` per item: run the child once per entry, in order,
   collecting the per-child results into an array (empty `items` → `[]`).

   **Thread the cursor through the child (display only).** So the user reads ONE continuous journey,
   not a child that restarts at "Step 1", pass a display-only cursor context into the child re-entry:
   the current global ordinal (`{g}`) and the parent's total (`{T}`), or a `{parent}.{child}`
   breadcrumb. The child's per-step cursor then continues the parent's count — render `▶ Step {g}/{T}`
   advancing across the child's steps, or a dotted `▶ Step {parent}.{k} · {label}` form — instead of
   restarting at `Step 1/{child M}`. The child's opening (the Announce-the-run beat) becomes an
   "entering nested workflow *{child}*" beat UNDER the parent roadmap — a sub-heading on the parent's
   journey, not a fresh top-level announcement. This cursor is **ephemeral display state only**: it is
   passed in memory for rendering and must NOT touch the child's run-state, `manifest_hash`, or any
   committed bytes — the child still mints its OWN run dir and records its own inputs exactly as above.
3. **Take the child's result** — its `output.from` node output — recap as a conductor (the
   segment-recap rubric above): brief the user on what the child produced and what it means for the
   parent journey, reusing the child's own wrap-up — don't replay its segments. Then dispatch
   `{op:"commit", name, run_dir, step:<i>, results:{<node>:<child result>}, label}` (a `for_each`
   step commits the collected array) and advance per the commit-dispatch framing.
4. If the child fails or its evaluator never passes, **stop and surface the error** — do not claim
   success, do not commit.

## Finish
When the next `prep` returns `{kind:"done"}`, dispatch `{op:"finish", name, run_dir}` to close the
run (plumbing — print nothing for it). If `output.from` is set, report that node's result as the
workflow's result. Then sign off as the conductor — one beat that CLOSES the journey announced at the
opening: the workflow name, the outcome, where the result landed, and that all N steps are complete
(e.g. "🛠  {name} — done. All {N} steps complete; the result is at <where>."). For a full timeline,
you MAY add an optional aside ("for the step-by-step, ask me for the run journal"). The per-segment
recaps already covered the play-by-play — don't re-summarize each segment here.

## Gate / delegation semantics (authoritative)
- A node runs in a **background segment** unless it is an orchestrator checkpoint. The compiler already
  made this split from the delegation governance — do not second-guess it at runtime.
- **Never** run an interactive step inside a segment: the Workflow tool's subagents have no
  `AskUserQuestion` and will silently fabricate. All human interaction happens at checkpoints, here.

## Failure modes
Every CLI named below runs inside the **bookkeeper**, never the conductor: the bookkeeper hits
the non-zero exit, journals where the op specifies, and returns an `{ok:false, …}` digest carrying
its reason/errors. The conductor's job at each is the same — surface that digest's meaning in plain
language and STOP, never repairing, retrying, or fabricating a result. The taxonomy and stop
semantics are unchanged from when the conductor ran these directly; only WHO runs the CLI moved.
- **Unknown workflow** — no `WORKFLOW.yaml` for `<name>` (the `open`/`preflight` compile fails to
  resolve it). Stop.
- **Compile error** — the bookkeeper's `compile-workflow` (in `open` / `preflight`) exits non-zero;
  the digest is `{ok:false, error}` carrying `error` / `schema_errors`. Surface it, stop.
- **Segment error** — a segment returns an error (fail-loud node). This surfaces in the conductor
  (the Workflow tool call is the conductor's own). Stop, surface it, do not proceed.
- **Loop exhausted (`on_exhaust: fail`)** — the loop segment's post-cap throw is a segment error;
  surface it naming the loop and its round count. (`on_exhaust: escalate` is not a failure: the
  `loop_exhaust` gate handles it; extend declined / abandoned maps onto "Gate abandoned" below.)
- **Step IO check failed** — the bookkeeper's `commit` op runs `check-step-io` on the candidate
  state and it exits non-zero (schema violation, missing result, or the probable-truncation/
  fabrication signature). The bookkeeper has already journaled `run_error` with the failed step
  marked and returns `{ok:false, reason, errors}` WITHOUT committing. Surface the reason in plain
  language, never synthesize a replacement output, stop. The committed run-state still points at
  the failed step, so a later resume re-runs it — the corrupt value can never thread forward.
- **Prep/args failed** — the bookkeeper's `prep` op runs `run-step args`/`run-step eval` and it
  exits non-zero (missing recorded result, ungathered required input, oversized args payload, a
  throwing expression, an uncovered nested-child required input, or a missing `node` binary),
  returning `{ok:false, error}`. Surface it, stop. Never substitute your own args assembly or
  expression evaluation — the kernel's is authoritative.
- **Required input unresolved** — stop, name the input.
- **Gate abandoned** — stop cleanly, report partial state.
- **No recorded run (rerun)** — the bookkeeper's `rerun-locate` source scan found nothing committed
  and returns `{ok:true, found:false}`. Stop: there is nothing to rerun.
- **Rerun hash mismatch** — the `open` recompile (with the recorded profile/overrides/gate_mode
  the bookkeeper read from the run's config) returns a `manifest_hash` differing from the recorded
  run's. Stop — rerun requires equality; a changed def/registry/profile means the recorded outputs
  no longer line up. Start a fresh run.
- **Rerun seed failed** — the bookkeeper's `rerun-seed` op (`run-journal rerun`) exits non-zero
  (unknown `--from` selector, from-point beyond the recorded progress or past a recorded
  `failed_step`, a missing recorded result, a pre-shape run-state) and returns `{ok:false, error}`.
  Surface it, stop — never hand-assemble the seed.
- **Rerun re-execution declined** — the human declined a `confirm_steps` re-execution. Dispatch
  `{op:"fail", … label:'rerun declined at <node>'}` (the bookkeeper journals it), stop cleanly.
- **Headless gate stop** — auto mode reached a gate with `on_headless: fail` (or a pausing gate
  with no usable `default` in a hand-edited manifest). Dispatch `{op:"fail", …}` naming the gate
  (the bookkeeper journals `run_error`), stop with a nonzero outcome; a TOP-LEVEL park continues
  later via `/workflow <name> pickup` (a park inside a nested child is out of pickup's scope
  through the parent — see Pickup step 5).
- **Headless interaction required** — auto mode reached an orchestrator node whose prompt requires
  `AskUserQuestion`: dispatch `{op:"fail", …}` naming the node (the bookkeeper journals it), stop
  with a nonzero outcome — pickup continues it interactively. A MISSING REQUIRED INPUT also stops
  the run, but there is nothing to pick up: inputs are gathered before any step commits (no run is
  minted yet), so re-invoke fresh with the input supplied. Never fabricate the human's side.
- **Pickup hash mismatch** — the `open` provenance recompile returns a `manifest_hash` differing
  from the parked run's recorded one (def/registry/profile changed since the park). Stop: the
  parked state cannot continue under a changed compile; start a fresh run. Never improvise a
  partial reuse.
- **Pickup without a human** — `pickup` invoked under auto/headless. Refuse with a nonzero
  outcome: the entire point of pickup is the human's interactive verdict.
