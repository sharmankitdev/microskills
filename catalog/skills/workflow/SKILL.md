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

1. **Name** — read `<name>` from invocation args (position 1 for `/workflow <name>`, or the first
   token from a shim). If no `.claude/workflow-defs/<name>/WORKFLOW.yaml` exists, stop and report.
2. **Profile / overrides** — detect `<profile>` (slash position 2 or `"with <profile> profile"`) and
   any `override workflow-config: <field>=<value>` clauses.
   **Headless signal** — the run is headless when the invocation args carry `--gate-mode auto` (or
   `--headless`), OR the environment sets `MICROSKILLS_HEADLESS=1` (check once via Bash:
   `echo "${MICROSKILLS_HEADLESS:-}"`). The signal only feeds the compile flag in step 3 — at run
   time the MANIFEST is authoritative.
3. **Compile** — run via Bash: `.claude/scripts/compile-workflow <name> [--profile "<profile>"]
   [--override <k>=<v> ...] [--gate-mode auto when the headless signal is set]`. The compile summary
   and the written manifest always carry `manifest_hash` (the resume check in step 5 needs it);
   `--explain` is optional diagnostics only. Non-zero exit → stop and surface the JSON `error` /
   `schema_errors` — under a headless signal this includes the fail-loud "hard gate declares no
   'default'" error: never retry without the flag to sneak past it. (Never pass `--plan` on this
   EXECUTION path — a dry-run writes nothing, so there would be no fresh manifest to run; `--plan`
   belongs to the Preflight mode only.)
   **`manifest.gate_mode == "auto"` is the authoritative runtime mode** for everything below (a
   def/profile may declare `gate_mode: auto` with no flag at all, and a profile-declared mode wins
   over the flag inside compile). Auto mode means: no human is present — `AskUserQuestion` must
   never be called anywhere in the run; every gate takes its author-declared `default` verbatim.
   **One exception: a Pickup session** (the Pickup mode below) — the human's explicit `pickup`
   invocation suspends the auto GATE rules for the remaining steps while the manifest stays
   stamped auto; every gate asks.
4. **Read the manifest** — read the `manifest_path` from the compile summary
   (`.claude/workflow-defs/<name>/.compiled/manifest.json`). Note `manifest.manifest_hash`.
5. **Resume check** — all runtime state is namespaced per run under
   `.claude/workflow-defs/<name>/.compiled/runs/<run-id>/` (dispatcher runtime state, NOT a compiled
   artifact; the compiler's stale-clean only globs `seg-*.js` and `resolved/*.json` and never touches
   `runs/`). Run via Bash:
   `.claude/scripts/run-journal latest --runs-dir '.claude/workflow-defs/<name>/.compiled/runs' --manifest-hash '<manifest.manifest_hash>' --steps <manifest.steps.length>`
   It scans `runs/*/run-state.json` for the newest unfinished run (greatest run id) whose stored
   `manifest_hash` equals the current one — a mismatched hash means the workflow was recompiled /
   changed, so stored node outputs no longer line up and that run is simply not offered. (A
   FINISHED run is never offered here — re-rolling part of one is the Rerun mode; a PARKED
   gate-mode=auto run is invisible to this scan by design — its hash never matches an interactive
   compile — and is continued via the **Pickup** mode instead.) **Under auto
   mode never ask: skip the offer and start fresh at step 6** (prior run dirs stay as provenance; an
   auto compile has a distinct manifest_hash anyway, so interactive state never matches). Parse the JSON:
   - `found: true` → **offer to resume** via `AskUserQuestion` ("Resume run {run_id} from step
     {step_index+1}, or start fresh?"). If the output carries a **non-null `failed_step`**, the
     offer must say so: the run's last attempt at that step FAILED its post-step IO check (its
     result was never committed), so resuming re-runs that step — e.g. "Resume run {run_id}? Its
     last attempt at step {failed_step+1} failed the IO check; resuming re-runs it." (The scan
     itself never offers a poisoned record whose progress advanced past a `failed_step`.) On
     resume: adopt that `run_dir` as this run's directory, seed
     `inputs` and `results` from the `inputs` / `results` maps in `<run_dir>/run-state.json` — the
     committed state carries both, and it is the record the `run-step` kernel reads (skip Setup
     steps 6-8 — the run's inputs were already gathered and recorded; re-using completed work avoids
     re-running expensive opus planner segments; `run-config.json` stays the provenance record), set
     the start position `i = step_index`, and journal the pickup:
     `.claude/scripts/run-journal append --run-dir '<run_dir>' --event resume --step-index <i>`.
   - `found: false`, or the user picks "start fresh" → continue with Setup step 6 (start at `i = 0`;
     any prior run dirs are left in place as provenance).
6. **Mint the run** — run via Bash:
   `.claude/scripts/run-journal init --runs-dir '.claude/workflow-defs/<name>/.compiled/runs' --manifest-hash '<manifest.manifest_hash>' [--profile '<profile>'] [--override '<k>=<v>' ...] [--gate-mode auto when manifest.gate_mode == "auto"]`
   (pass `--profile` / each `--override` exactly as passed to compile in step 3, and `--gate-mode
   auto` when the manifest carries `gate_mode: "auto"`, so the run's FULL compile provenance —
   gate mode included — is recorded verbatim). It mints a fresh `run_id`, creates
   `<run_dir>/` + `<run_dir>/run-inputs/`, writes `<run_dir>/run-config.json`
   (`{run_id, manifest_hash, profile_used, overrides, gate_mode, inputs}`), and opens `<run_dir>/journal.jsonl`
   with a `run_start` event. Parse the JSON output and note `run_dir` — every runtime file below
   (run-state, run-inputs, journal) lives under it. (`run-journal report --run-dir '<run_dir>'`
   renders the journal human-readably for a post-mortem; `runs/` isolates *runtime* state only — two
   concurrent runs of the same def compiled with different profiles/overrides still race on the shared
   `.compiled/seg-*.js` + `manifest.json`, so never run those concurrently.)
7. **Gather inputs** — initialize `inputs = {}`. For each name in `manifest.required_inputs`: if the
   caller's prompt supplies a literal value, use it; otherwise gather via `AskUserQuestion`. Apply
   `manifest.input_defaults` for any non-required input not supplied. (Do not fall back to a default
   for a required input.) **Under auto mode `AskUserQuestion` is unavailable**: a required input the
   caller did not supply → journal a `run_error` naming the input and stop with a nonzero outcome —
   never invent a value.
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
   the exact input set — is one record), **seeds `<run_dir>/run-state.json` with
   `{manifest_hash, step_index: 0, inputs, results: {}}`** (the `run-step` kernel below builds every
   segment's args — including the first — from this committed state, never from memory), journals an
   `inputs_recorded` event with per-input byte sizes, and consumes the scratch file.

## Preflight — `/workflow <name> [profile] --plan`

A dry-run renderer: show what a run WOULD do — steps, gates, inputs, executors — without writing
artifacts, minting a run, or gathering a single input. Run Setup steps 1-2 (name, profile /
overrides, headless signal), then:

1. **Compile the plan** — run via Bash: `.claude/scripts/compile-workflow <name> --plan --explain
   [--profile "<profile>"] [--override <k>=<v> ...] [--gate-mode auto when the headless signal is
   set]`. `--plan` computes the full plan but writes NOTHING; `--explain` adds the per-node
   `classification` carrying `executor: {profile, agent, model}`. Non-zero exit → surface the JSON
   `error` / `schema_errors` and stop — a preflight that fails to compile IS the useful answer.
2. **Render EXCLUSIVELY from the printed summary.** It embeds the FULL manifest object under
   `manifest` plus the executor entries under `classification` — never read
   `.claude/workflow-defs/<name>/.compiled/manifest.json` here (the `--plan` compile wrote nothing;
   anything on disk is from an earlier compile and may not match these flags). Render, in order:
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

1. **Locate the recorded run** — `--run <run-id>` names `runs/<run-id>` directly (read its
   `run-config.json` for the fields below); otherwise run via Bash:
   `.claude/scripts/run-journal latest --runs-dir '.claude/workflow-defs/<name>/.compiled/runs'`
   — no `--manifest-hash`, no `--steps`: the newest run with a committed run-state, FINISHED runs
   included (a finished run is rerun's normal case). `found: false` → stop: nothing recorded to
   rerun. Note its `run_id`, `manifest_hash`, `profile_used`, `overrides`, and `gate_mode`. A
   non-null `failed_step` caps the from-point: that step failed its IO contract, so the seeder
   (step 3) refuses any `--from` beyond it — tell the user up front.
2. **Recompile with the RECORDED provenance** — the Setup step 3 compile, but passing
   `--profile '<profile_used>'`, each recorded override verbatim, and `--gate-mode auto` when the
   recorded `gate_mode` is `"auto"` (never this invocation's own flags — a rerun reproduces the
   recorded compile, and `run-config.json` records every compile input including the gate mode).
   If the fresh summary's `manifest_hash` differs from the recorded one → **STOP: rerun REQUIRES
   manifest_hash equality.** The def, registry, or profile changed since the recorded run, so its
   stored node outputs no longer line up — start a fresh run instead. Never improvise a partial
   reuse, and never retry the compile with different flags to chase the hash.
3. **Seed the rerun in code** — run via Bash:
   `.claude/scripts/run-journal rerun --runs-dir '.claude/workflow-defs/<name>/.compiled/runs'
   --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --source-run '<run_id>'
   [--from '<step|node>']`. It re-checks hash equality (the authoritative gate), resolves the
   from-point — an integer is a 0-based manifest step index; a name matches a gate id, a
   checkpoint node id, or a node INSIDE a segment, which **snaps to the segment start** (segments
   are atomic: the whole segment re-runs) — requires the source run to have committed every step
   before it, and mints a NEW run dir seeded with the recorded `inputs` and every pre-from result
   (results at/after the from-point are dropped, so a stale later output can never leak forward;
   the source run dir is provenance — never modified). Non-zero exit → stop and surface its
   `error`. Parse the JSON: note `run_dir`, `from` (`step_index`; `snapped_to_segment: true` →
   tell the user their node-level from-point widened to the whole segment), `replayed_gates`, and
   `confirm_steps`.
4. **Replay, never re-ask** — print one line per `replayed_gates` entry:
   `Gate <id>: replaying recorded choice '<choice>'`. Those gates sit BEFORE the from-point; their
   recorded `{choice}` results are already seeded into the run-state, so downstream branches read
   them — do not re-present them. A replayed `loop_exhaust` whose recorded choice was `extend` is a
   CHOICE replay only — never re-act on it (the seeded run-state already carries the
   post-extension `results.loop`/carry).
5. **Skip Setup steps 5-8 entirely** — the inputs are FROZEN from the record and already seeded,
   including materialized `run-inputs/` paths that point into the SOURCE run's dir (this is why
   finished run dirs are retained). Adopt `run_dir`, seed in-memory `inputs` / `results` from
   `<run_dir>/run-state.json`, set `i = from.step_index`.
6. **Execute the manifest** from `i` exactly as a normal run, with ONE addition: a step listed in
   `confirm_steps` (an orchestrator_node / nested_workflow checkpoint) **re-executes side
   effects** — filesystem writes, vendoring, nested child runs that already happened once in the
   source run. Before executing such a step, confirm via `AskUserQuestion` ("Step {i+1} re-runs
   '<node>', re-executing its side effects. Re-run it, or stop?"). A "stop" → journal a
   `run_error` (`--label 'rerun declined at <node>'`) and stop cleanly. Under auto mode there is
   no human: proceed WITHOUT the confirmation — the explicit `rerun` invocation is the consent —
   and journal the step normally. Gates at/after the from-point re-present normally (fresh
   choices).

## Pickup — `/workflow <name> pickup [--run <run-id>]`

Interactive continuation of a PARKED gate-mode=auto run — one that stopped with committed
run-state at an `on_headless: fail` gate (the `loop_exhaust` exhaustion gate is the marquee case:
an overnight run parked unconverged, extended with morning guidance) or at an
interaction-requiring orchestrator node. Pickup is the second half of the declared "do the work
overnight, approve in the morning" pattern: the auto-committed prefix stays exactly as recorded;
only the NOT-YET-RUN suffix executes in this session, with interactive gate handling. Pickup
**requires a human**: under a headless invocation (auto gate mode / `MICROSKILLS_HEADLESS`),
refuse with a nonzero outcome — a headless pickup is a contradiction in terms.

1. **Locate the parked run** — `--run <run-id>` names `runs/<run-id>` directly: read BOTH its
   `run-config.json` (the provenance: `manifest_hash`, `profile_used`, `overrides`, `gate_mode`)
   AND its `run-state.json` (`step_index`, `failed_step` — these two live only in the state file).
   Otherwise run via Bash:
   `.claude/scripts/run-journal latest --runs-dir '.claude/workflow-defs/<name>/.compiled/runs'`
   — no `--manifest-hash`, no `--steps` (the newest committed run, ANY compile — a parked auto
   run's hash never matches an interactive compile, which is exactly why the normal resume scan
   cannot see it). `found: false` → stop: nothing to pick up. Note `run_id`, `manifest_hash`,
   `profile_used`, `overrides`, `gate_mode`, `step_index`, `failed_step`.
2. **Recompile with the RECORDED provenance** — same move as Rerun step 2: `--profile`, each
   recorded override verbatim, and `--gate-mode auto` when the recorded `gate_mode` is `"auto"`.
   Fresh `manifest_hash` must EQUAL the recorded one, else **STOP: the def, registry, or profile
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
4. **Adopt the run dir IN PLACE** — pickup continues the SAME run (no new run dir — unlike
   rerun, nothing is replayed): seed in-memory `inputs` / `results` from
   `<run_dir>/run-state.json`, set `i = step_index`, and journal the mode transition:
   `.claude/scripts/run-journal append --run-dir '<run_dir>' --event pickup --step-index <i> --label 'interactive pickup of parked auto run'`.
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
and track the 0-based position `i` as you walk. **Before running each step, print a one-line header**
`Step {i+1}/{M}: {label}`, synthesizing `{label}` from the step (the manifest has no label field):
- segment → its node ids as an action — `plan` → "Plan", `implement, evaluate` (a loop) → "Implement & evaluate (loop)".
- gate → "Approval: {gate.id}".
- orchestrator_node → its node id as an action — `finalize` → "Finalize", `provision` → "Provision missing microskills".
- nested_workflow → the child workflow as an action — `build` (running `build-workflow-from-plan`) → "Build (nested workflow)".
A skipped step (a `warn` gate, an orchestrator node whose `when` is false, or the conditional
`loop_exhaust` gate after a converged loop) still gets a header, marked skipped.
Walk `manifest.steps` in order (starting at the resume position `i` from the Setup resume check, default 0):

**Check the step's IO, then persist run-state + journal — the check gates the commit.** After a
step completes (and its output is stored into `results`), checkpoint in three moves:
1. Use the **Write tool** to write
   `{ "manifest_hash": manifest.manifest_hash, "step_index": <the index of the NEXT step to run>, "inputs": inputs, "results": results }`
   to `<run_dir>/run-state.json.tmp` (a structured tool parameter — node outputs never ride a shell
   command). All four keys are required — `run-step` reads args and checkpoint expressions from
   the committed state, so `inputs` must always ride with it. This is only the CANDIDATE state —
   it is not committed until move 3.
2. **Pre-commit IO check** — run ONE Bash call:
   `.claude/scripts/check-step-io --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --run-state '<run_dir>/run-state.json.tmp' --step <i>`
   It receives only PATHS (node outputs never ride argv) and validates every value this step
   produced — read from the CANDIDATE state — against the per-node `io` contract on the manifest
   step (`{schema, guarded, fan_out}`: the node's effective output_schema; `fan_out` → an array of
   that schema; a guarded node's `null` is a legal skip; an unguarded `null`/`{}` against a
   required-props schema = probable native-engine truncation or subagent fabrication).
   **Non-zero exit → STOP WITHOUT COMMITTING** — the failed output must never enter the committed,
   resumable record (the committed run-state still points at step `<i>`, so a later resume re-runs
   the failed step instead of threading the corrupt value forward). Surface the JSON `errors`
   readably, then journal the failure WITH the failure stamp, ONE Bash call:
   `.claude/scripts/run-journal append --run-dir '<run_dir>' --event run_error --step-index <i> --outcome error --label '<short reason>' --mark-failed-step <i>`
   (it stamps `failed_step: <i>` into the still-committed previous run-state — `latest` surfaces it
   in the resume offer and `rerun` refuses to seed past it — and never touches the tmp file: leave
   `run-state.json.tmp` in place as evidence). Do not run the next step, and never re-synthesize or
   repair the failed output yourself (re-running the producing step after a fix is the human's
   call). Exit 0 → continue; surface any `warnings` (e.g. an oversized result a later segment
   threads through `args` — the compiled args guard is the enforcement point there; the warning
   just names the producing node early).
3. **Commit + journal** — run ONE Bash call:
   `.claude/scripts/run-journal append --run-dir '<run_dir>' --event step_complete --step-index <i> --label '<the step header label>' --commit-state run-state.json.tmp`
   — for a gate step also pass `--gate '<gate.id>' --choice '<the recorded choice label>'`; for a
   skipped step (a false `when`, or a warn gate passed through) add `--outcome skipped`. The helper
   atomically promotes the tmp file to `<run_dir>/run-state.json` (tmp + `os.replace` — a crash never
   leaves a half-written state; a passing re-run of a previously failed step clears its `failed_step`
   stamp, because the four-key candidate replaces the state wholesale) and appends one
   machine-readable line to `<run_dir>/journal.jsonl`, recording its own timestamp and computing
   byte sizes by reading the committed state itself — content never rides argv.
This is the dispatcher's in-memory `results{}` checkpointed to disk so a run that dies mid-way can
resume (see the Setup resume check) instead of restarting from step 0 and re-running expensive segments.
It is dispatcher runtime state, quarantined from the compiled bytes — the compiler never reads `runs/`
and its stale-clean (which globs only `seg-*.js` and `resolved/*.json`) never deletes it, so determinism
is untouched. On a clean finish leave the run dir in place — it is the run's provenance record AND a
rerun's seed: a later `/workflow <name> rerun` freezes this run's recorded inputs, whose
materialized values reference this dir's `run-inputs/` files by absolute path — deleting a
finished run dir breaks reruns of it (the `latest` resume scan skips finished runs via `--steps`,
so retention costs nothing at resume time). Do not let journaling block the run. On a stop
(segment error, abandoned gate, unresolvable input), record it before stopping:
`.claude/scripts/run-journal append --run-dir '<run_dir>' --event run_error --step-index <i> --outcome error --label '<short reason>'`
(a failed IO check uses the move-2 form above, with `--mark-failed-step <i>`).

### `kind: "segment"`
1. **Build `args` in code — never assemble it in your head.** Run ONE Bash call:
   `.claude/scripts/run-step args --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --run-state '<run_dir>/run-state.json' --step <i>`
   It receives only PATHS (node outputs and input values never ride argv) and emits canonical
   sorted-key JSON `{args, args_bytes, script, errors}` from the step's `needs` against the
   committed run-state — every needed key PRESENT with the same presence-not-truthiness rule the
   compiled segment's fail-loud guard enforces (`wf_<n>` from the recorded inputs with manifest
   defaults applied, explicit `null` for an ungathered optional; `<node-id>` from results, a
   guarded skip's stored `null` riding as `null`; `carry_<v>: null` loop seeds — an extend
   re-entry of an `on_exhaust: escalate` loop step passes `--extend`, which seeds every declared
   carry var from the committed `results.loop.carry` instead; without the flag the seeds stay
   null, revise re-runs included). **Non-zero exit →
   STOP** and surface its JSON `errors` (journal a `run_error`): a missing recorded result, an
   ungathered required input, or an **oversized args payload** — past the budget the native engine
   truncates, run-step fails loud and never auto-spills (silently substituting a file path would
   corrupt the segment's `_args` derefs). Never hand-assemble, trim, or summarize args to sneak
   under the budget — an oversized payload means an upstream output must move to a file by
   declaration (`materialize: file` for a workflow input, `spill_outputs` on the producing node),
   which is the author's fix, not yours.
   **Declared spill rides automatically.** When a producing step's manifest carries a `spill` map
   (the node declared `spill_outputs`), run-step has already written each listed field's value to
   `<run_dir>/handoff/<node>.<field>` and substituted that file's **absolute path** into the
   emitted `args` (the `spilled` key in its output names every substitution) — the by-reference
   `*_path` convention; the downstream microskill reads the file. Pass the args verbatim as always:
   never re-inline a handoff file's contents, and never edit the stored `results` (they keep the
   full value — only the threaded view carries paths).
2. Invoke the **Workflow tool** with `scriptPath` = `step.script` **resolved against the def dir**:
   the manifest stores it relative to the def dir (e.g. `.compiled/seg-1.js` — portable across
   checkouts), so the path to pass is `.claude/workflow-defs/<name>/<step.script>`. Pass
   `args` = the `args` object from run-step **verbatim**. The segment runs autonomously in the
   background on the native engine.
3. When it completes, its return value is an object keyed by the node ids in `step.produces`. Store
   each into `results` (e.g. `results.plan = <returned>.plan`). An `on_exhaust: escalate` loop
   step's `produces` also lists `loop` — the pseudo-result `{converged, rounds, carry}`; store it
   like any node result (`results.loop = <returned>.loop` — the conditional `loop_exhaust` gate and
   any extend re-entry read it from the committed state).
4. **Emit a recap** (2-4 lines, human-readable, no raw JSON) of what the segment produced, before the
   next step. Build it from `step.produces` + the stored `results`, using the **output rubric** in the
   gate block below:
   - Non-loop segment → one clause per produced node — e.g. `Planned <name> — drafted a WORKFLOW.yaml (~N nodes).`
     or `Implemented — wrote K staged files.` Never paste plan file contents or object arrays here.
   - Loop segment (`step.is_loop`) → summarize the outcome, not each round: state the round count from
     `<returned>.loop.rounds` when the step declares `on_exhaust: escalate` (`<returned>.__rounds`
     otherwise — same number, one source of truth per mode) and the evaluator's final verdict — e.g.
     `Implement/evaluate loop done in 2 round(s) — verdict PASS, K staged files.` or
     `Implement/evaluate loop done in 3 round(s) — verdict FAIL at the round cap; N issues open.`
     An extend re-run's recap names the extension explicitly (`extension 1: 3 more round(s) — ...`).
   - A produced node that is `null` (a guarded/skipped node) → say so in one clause; don't invent output.
5. If the segment errors (a fail-loud node), stop and surface the error in readable form — do not
   fabricate a result (skip the recap; report the failure instead).

### `kind: "checkpoint"`, `checkpoint_type: "gate"`
A human-approval / hard gate.

**Conditional gate first** — a gate step carrying a step-level `when` (the compiler-emitted
`loop_exhaust` exhaustion gate; authored gates never have one) is evaluated in code BEFORE any
rendering: the same `run-step eval` call as an orchestrator node (it returns `gate`, `when`,
`skipped`). `skipped: true` (the loop converged) → store `results[gate.id] = null` (the guarded-null
convention — a later `${loop_exhaust.output.choice}` branch must sit behind a converged check, e.g.
`${loop.output.converged || loop_exhaust.output.choice == 'accept'}`), journal the step with
`--outcome skipped`, print the header marked skipped, and continue — the gate never renders.
`skipped: false` → the loop exhausted its cap unconverged; render and ask below.

Render the gate's evidence:

**`gate.present` declared → render it MECHANICALLY**, entries in declared order — no synthesis, so
the approver sees the same evidence every run:
- a string path `<id>.output[.<field>...]` → resolve against `results` and print `**<last path
  segment>**: <value>` (a scalar verbatim; an object/array as a fenced ```json block).
- `{read_file: <path>}` → resolve the path against `results` to a FILE PATH, **Read that file**, and
  show its contents in a fenced block (language from the extension; if long, show it in full anyway —
  present is the author's explicit ask).
- a path that resolves to `undefined`/`null` → print `**<path>**: (not produced)` — never invent a
  value.

**No `present` → fall back to the output rubric**: render `results[gate.after]` as readable
markdown — it is an `output_schema`-shaped object. **Never show raw JSON.** Match the shape:
- **plan** (`{plan_path, name, ...}`) → **Read the file at `plan_path`** and show its contents in a fenced ```yaml
  block, prefixed by `name`. If long, show the node graph + key decisions, not every line.
- **verdict** (`{pass, issues[]}`) → bold **PASS** / **FAIL**, then `issues` as a bullet list (summarize
  each issue's salient fields, e.g. severity + message — not the raw object). Empty issues → "PASS, no issues."
- **staging paths** (`{staging_paths[]}`) → a bullet list of the file paths.
- **scope advisory** (`{kind, reason, recommendation}` / `missing_microskills[]`) → the advisory in prose;
  list each missing microskill's `name` + one-line `requirement`.
- **default** → summarize key fields in 2-5 lines of prose; omit internal/verbose fields. Prefer shorter.

**Auto mode (`manifest.gate_mode == "auto"`, except a Pickup session — there every gate asks) —
never `AskUserQuestion` at a gate:**
- `gate.on_headless == "fail"` → journal a `run_error` (`--label 'gate <id> declares on_headless:
  fail'`) and **STOP with a nonzero outcome, naming the gate**. The committed run-state is resumable
  interactively later — that is the declared "do the work, then hand off to a human" pattern.
- otherwise take **`gate.default`** (compile guarantees a pausing gate declares one under auto):
  record `results[gate.id] = { choice: <gate.default> }` — the author-declared label **VERBATIM**,
  never a re-phrasing — print one line `Gate <id>: auto — taking declared default '<default>'`, and
  journal the step with `--gate '<gate.id>' --choice '<gate.default>'`. Then act on that recorded
  choice exactly per the mapping below, with one exception: a `revise`-style default cannot gather
  human notes — journal a `run_error` and stop, naming the gate. (An `extend` default cannot occur:
  compile refuses `on_exhaust.default: extend` — it would re-enter the loop unboundedly headless. A
  hand-edited manifest carrying one → journal a `run_error` and stop, same as revise.)
- a pausing gate with NO `default` (a hand-edited manifest — compile never emits this) → journal a
  `run_error` and stop. **Never pick an option yourself.**

**Interactive mode** — ask via `AskUserQuestion`: `gate.prompt` is the question, `gate.options` the
choices (default `confirm / stop`). **Give each option a one-line `description` of its consequence**
— `approve`/`confirm` → "Continue to the next step."; `revise` → "Re-run this segment with your
notes."; `abandon`/`stop` → "Stop the run and clean up staging." (map other labels to the nearest).

**Record the human's pick — `results[gate.id] = { choice: <selected option> }`** (the chosen option's
label, verbatim). A gate id is a legal `${...}` ref target: a later node/segment whose `when` or `inputs`
reads `${<gate-id>.output.choice}` resolves against this stored object (the gate-choice branching feature
validate-workflow accepts). This is in addition to — not instead of — the approve/revise/abandon handling
below; always store the pick, then act on it. Then act:
- An `approve`/`confirm` choice → continue to the next step.
- A `revise`-style choice → **ask the user what to change** (a follow-up `AskUserQuestion` or their
  free-text note), then re-run the segment that produced the gate's `after` node: rebuild that
  segment's base `args` via `run-step args` (same call as the segment step, no re-compile), fold the
  revision notes into the relevant input value (e.g. append them to the `requirement` arg), and
  re-invoke the segment's script. Then re-render the output and re-present the gate
  (re-recording `results[gate.id]` on the re-presented choice).
- An `extend` choice (the `loop_exhaust` gate) → re-run the LOOP segment with the committed carry
  and a fresh `max_iters` budget, in this order (the kernel reads COMMITTED state, so every fold
  must land on disk BEFORE the args call):
  1. **Fold guidance first** — when the loop step's manifest declares `on_exhaust.notes_input`,
     ask the user (a follow-up `AskUserQuestion` or their free-text note) and fold it into that
     named workflow input: a `materialize: file` input gets the notes APPENDED to its materialized
     file under a `## Loop-extension guidance (extension N)` heading — but when that file lives
     outside THIS run's `run_dir` (a rerun: materialized paths point into the SOURCE run's dir,
     which is provenance and never modified), first COPY it into this run's `run-inputs/`, append
     to the copy, and update `inputs[name]` to the copy's path; a plain string input gets the
     notes appended to its value in `inputs`. Any `inputs` change is committed to
     `run-state.json` (Write tmp → `run-journal append --commit-state`, no step advance) BEFORE
     the next move.
  2. **Rebuild args with the explicit extend flag** — `run-step args ... --step <i> --extend`
     (same call as the segment step plus the flag, no re-compile): the kernel seeds every declared
     `carry_<v>` from the committed `results.loop.carry` ONLY under `--extend` (a revise re-run of
     the same segment keeps the default null seeds), and fails loud if there is nothing committed
     to seed from.
  3. Re-invoke the segment's script, then run the IO check + commit for the LOOP step's refreshed
     results (the three-move protocol — an extend re-run that dies uncommitted must never leave
     stale loop results looking current), and re-render `results.loop` (cumulative extensions are
     dispatcher bookkeeping: say "extension N").
  4. **Exit by verdict** — still unconverged → re-present this gate. Converged → the human's real
     pick stays recorded (`results[gate.id] = { choice: 'extend' }`, never overwritten with a
     skip-null), journal the gate step normally (`--gate '<gate.id>' --choice 'extend'`), and
     continue to the next step.
- An `abandon`/`stop` choice → stop the run cleanly (clean up any staging the segments created).
For `severity: warn` gates (any mode), render the evidence + emit the prompt, then continue without
pausing — record `results[gate.id] = { choice: <gate.default> }` when the gate declares a `default`
(the author-declared label, verbatim), else `{ choice: null }`. **Never record an option nobody
selected**: the recorded choice is always author-declared or null (validate forces a `default` on any
warn gate whose choice is branched on, so a downstream branch always resolves against a real label).
Journal the warn pass-through with `--outcome skipped` (plus `--choice` when a default was recorded).

### `kind: "checkpoint"`, `checkpoint_type: "orchestrator_node"`
An orchestrator-native step (a node with neither `use` nor `agent`, or `delegation: orchestrator`).
**First evaluate the step's declared expressions in code** — run ONE Bash call:
`.claude/scripts/run-step eval --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --run-state '<run_dir>/run-state.json' --step <i>`
It executes the step's `when` / `for_each` / `${...}` refs as the compiler's own translated JS
(under node, context from the committed run-state) — **never evaluate an expression or substitute a
`${...}` ref in your head**; the segment world runs these as real JS, and this is the same JS. A
referenced field a producing step's `spill` map declares resolves to its **handoff file path**
(run-step substitutes it — the same by-reference view a segment receives; its `spilled` key names
each substitution): treat it as a path to Read, never re-inline the contents into the prompt.
Parse its JSON and act on it:
- `skipped: true` (a false `when`) → store `results[node] = null`, journal the step with
  `--outcome skipped`, and continue to the next step — do not execute the prompt.
- no `for_each` → `prompt` is the node's prompt with every `${...}` ref already substituted.
  Execute it here in the main loop.
- `for_each` → `iterations` is the resolved fan-out: execute each entry's `prompt` once, in order,
  collect the per-item results into an array and store it as `results[node]`. Empty `items` → store
  `[]` (never null).
- **Non-zero exit → STOP** and surface its `errors` (journal a `run_error`): an unrecorded upstream
  node, an ungathered required input, or an expression that throws (e.g. a field read through a
  missing object — the same throw a compiled segment would produce). Never patch around it by
  evaluating the expression yourself.
Executing the (already-resolved) prompt is where filesystem side effects and interactive decisions
(`AskUserQuestion`) happen. Store its result into `results[node]`. **When the step's `io[<node>]`
carries a non-null `schema`, that is the node's declared RETURN CONTRACT**: store an object with
exactly those fields (the post-step `check-step-io` validates it) — never a prose summary in its
place.

**Auto mode (`manifest.gate_mode == "auto"`): `AskUserQuestion` is unavailable — there is no human.**
A prompt that REQUIRES asking the user (it instructs an interactive loop, or a decision only a human
can make) → journal a `run_error` **naming the node** and STOP with a nonzero outcome; never answer
on the user's behalf (a fabricated answer is worse than a stop). A prompt that needs no human input
executes normally. (Example: `refine-requirements`' base `clarify` node is NOT headless-able — its
`autonomous` profile, which rewrites the prompt to an unattended single pass, is the supported
unattended path.)

The main loop is also the only place a node may invoke a **nested workflow** (e.g. a `provision`
node running `microskill-create` with the autonomous profile, once per missing microskill via
`for_each`). Background segments cannot — their subagents have no orchestration context.

### `kind: "checkpoint"`, `checkpoint_type: "nested_workflow"`
A first-class nested-workflow call — a `workflow: <name>` node. The child runs here in the main loop,
never in a segment.
1. **Evaluate and resolve in code** — run the same ONE Bash call as an orchestrator node:
   `.claude/scripts/run-step eval --manifest '.claude/workflow-defs/<name>/.compiled/manifest.json' --run-state '<run_dir>/run-state.json' --step <i>`
   For a nested step it additionally resolves the declared `inputs` map (a full `${ref}` value keeps
   its type; an embedded ref string-interpolates; a field a producing step's `spill` map declares
   resolves to its **handoff file path** — by-reference into the child, which a child
   `materialize: file` input passes through as a path) and **cross-checks the resolved map against
   the child's required inputs pre-run** (base profile + the step's `profile` overlay applied). Parse
   its JSON:
   - `skipped: true` (a false `when`) → store `results[node] = null`, journal with
     `--outcome skipped`, continue — never compile or enter the child.
   - no `for_each` → `child_inputs` is the child's fully resolved input set.
   - `for_each` → `iterations` carries one resolved `child_inputs` per item: run the child once per
     entry, in order, collecting the per-child results into an array stored as `results[node]`.
     Empty `items` → store `[]`.
   - **Non-zero exit → STOP** and surface its `errors` (journal a `run_error`) — in particular an
     uncovered required child input means an upstream output or wiring is wrong; never invent the
     missing value or start the child anyway.
2. **Re-enter this same `workflow` skill for `step.workflow`** — compile
   `.claude/workflow-defs/${step.workflow}/WORKFLOW.yaml`, passing **`--profile ${step.profile}` when the
   checkpoint carries a `profile`** (a `customize: {profile}` on the `workflow:` node — e.g. `provision`
   runs `microskill-create` with the `autonomous` profile so its plan gate never pauses; omit the flag
   when absent). **When this run's mode is auto (`manifest.gate_mode == "auto"`), also pass
   `--gate-mode auto` to the child's compile** — mode inheritance; a child whose merged doc/profile
   DECLARES `gate_mode` keeps its own declaration (inside compile, the doc wins over the flag), so a
   child profile-declared mode always beats parent inheritance. **EXCEPT under a Pickup session:**
   the session-interactive override propagates exactly like inheritance does — do NOT pass
   `--gate-mode auto` to the child (the human is present; the child's gates ask inline). The child
   therefore compiles its interactive manifest and mints a fresh child run. Then run its manifest, supplying the
   resolved map as the child's gathered `inputs`
   (skip the interactive gathering — the inputs are already provided by the parent; the child still
   mints its OWN run dir under its own def's `.compiled/runs/` and records them, Setup steps 6-8). **Still
   run the normalization pass (Setup step 7) over the child's `manifest.materialize_inputs`** before its
   first segment: a parent may pass a raw string into the child's `materialize: file` input (e.g.
   `provision` hands `microskill-create` a `requirement_path` whose value is the per-microskill
   requirement *string* from the plan), and that string must be written to a file so only a path reaches
   the child's segment args. A value that is already a path hits the file rule (pass-through).
   Depth ≤ 1 is enforced at compile time — `compile-workflow` hard-dies on a child that itself
   contains a `workflow:` node (and on an import cycle), and `validate-workflow --defs-root` blocks
   the same findings via the same shared helper — so the child contains no further nested call;
   recursion is bounded.
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
- **Loop exhausted (`on_exhaust: fail`)** — the loop segment's post-cap throw is a segment error;
  surface it naming the loop and its round count. (`on_exhaust: escalate` is not a failure: the
  `loop_exhaust` gate handles it; extend declined / abandoned maps onto "Gate abandoned" below.)
- **Step IO check failed** — `check-step-io` exits non-zero on the CANDIDATE state after a step
  (schema violation, missing result, or the probable-truncation/fabrication signature). Stop
  WITHOUT committing: journal `run_error` with `--mark-failed-step <i>`, surface the `errors`,
  never synthesize a replacement output. The committed run-state still points at the failed step,
  so a later resume re-runs it — the corrupt value can never thread forward.
- **run-step failed** — `run-step args` or `run-step eval` exits non-zero (missing recorded result,
  ungathered required input, oversized args payload, a throwing expression, an uncovered nested-child
  required input, or a missing `node` binary). Journal `run_error`, surface its `errors`/`error`,
  stop. Never substitute your own args assembly or expression evaluation for the kernel's.
- **Required input unresolved** — stop, name the input.
- **Gate abandoned** — stop cleanly, report partial state.
- **No recorded run (rerun)** — the `run-journal latest` source scan found nothing committed
  under `runs/`. Stop: there is nothing to rerun.
- **Rerun hash mismatch** — the fresh compile's `manifest_hash` differs from the recorded run's
  (the recompile already used the recorded profile/overrides/gate_mode from `run-config.json`).
  Stop — rerun requires equality; a changed def/registry/profile means the recorded outputs no
  longer line up. Start a fresh run.
- **Rerun seed failed** — `run-journal rerun` exits non-zero (unknown `--from` selector,
  from-point beyond the recorded progress or past a recorded `failed_step`, a missing recorded
  result, a pre-shape run-state). Surface its `error`, stop — never hand-assemble the seed.
- **Rerun re-execution declined** — the human declined a `confirm_steps` re-execution. Journal
  `run_error` naming the step, stop cleanly.
- **Headless gate stop** — auto mode reached a gate with `on_headless: fail` (or a pausing gate with
  no usable `default` in a hand-edited manifest). Journal `run_error` naming the gate, stop with a
  nonzero outcome; a TOP-LEVEL park continues later via `/workflow <name> pickup` (a park inside a
  nested child is out of pickup's scope through the parent — see Pickup step 5).
- **Headless interaction required** — auto mode reached an orchestrator node whose prompt requires
  `AskUserQuestion`: journal `run_error` naming the node, stop with a nonzero outcome — pickup
  continues it interactively. A MISSING REQUIRED INPUT also stops the run, but there is nothing to
  pick up: inputs are gathered before any step commits, so re-invoke fresh with the input supplied.
  Never fabricate the human's side.
- **Pickup hash mismatch** — the provenance recompile's `manifest_hash` differs from the parked
  run's recorded one (def/registry/profile changed since the park). Stop: the parked state cannot
  continue under a changed compile; start a fresh run. Never improvise a partial reuse.
- **Pickup without a human** — `pickup` invoked under auto/headless. Refuse with a nonzero
  outcome: the entire point of pickup is the human's interactive verdict.
