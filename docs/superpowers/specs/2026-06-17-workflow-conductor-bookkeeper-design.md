# Workflow Execution UX — the "bookkeeper" split

**Date:** 2026-06-17
**Status:** Approved design, pending implementation plan
**Branch:** `workflow-conductor-ux`
**Supersedes:** the *mechanism* of §5.4 ("Plumbing goes silent") and §9.2 (success
criterion "no raw orchestration bash in the transcript") of
`docs/superpowers/specs/2026-06-17-workflow-execution-ux-design.md`. The goals and
all other sections of that spec stand; this document replaces only *how* the
plumbing is hidden. The conductor voice + authored-name work it specified (Tasks
1–4) is already committed and this builds on it.

---

## 1. The problem the live run exposed

The prior design specified §5.4 "plumbing goes silent" as a **prose instruction**
to the dispatcher: "run the internal CLI scripts quietly; their outcomes translate
to plain language." A live `/develop-product-backlog` run on this branch — driving
the *already-shipped* conductor skill faithfully — disproved the mechanism:

- The transcript was still wall-to-wall `Bash` / `Write` / JSON blocks: run-ids,
  manifest hashes, `run-state.json.tmp`, `--commit-state`, `check-step-io`,
  `normalize-input`, `.cat` paths.
- The maintainer's words: *"I'm still seeing SO SO MANY Bash, Write logs and
  technical commentary."*

Root cause: **prose only governs what the model *says*. The Claude Code harness
renders every main-loop tool call (`Bash`/`Write`/`Read`) regardless of how terse
the model's narration is.** A success criterion of "no orchestration bash in the
transcript" is therefore unreachable by instruction. The per-step bookkeeping
(~5 visible calls/step: `run-step args` → `Workflow` → `Write` run-state → `check-step-io`
→ `run-journal append`, plus the setup calls) *is* the noise, and it lives in the
main loop where it is unconditionally rendered.

There are in fact **two distinct leak sources**, conflated in the original §5.4:

1. **Tool-block leakage** — the `Bash`/`Write`/`Read` UI blocks themselves. Not
   fixable by prose. (The bulk of the noise.)
2. **Prose leakage** — the conductor's own words: run-ids, paths, "Committing
   state", "IO check passed", "Building the segment args". Fixable by prose rules,
   but the *current* rules are too soft ("run quietly").

This design fixes (1) structurally and (2) with a hard ban-list.

## 2. North star (unchanged) and the enabling realization

The north star from the prior spec holds: **a running workflow should feel like a
colleague conducting the work** — announce intent, run opaquely, synthesize a
briefing on return, keep the human in control at decision points, never expose the
plumbing.

The new enabling realization: **work that runs inside a subagent never enters the
main transcript.** When the dispatcher delegates to an Agent, only that agent's
final message returns to the main loop; its inner `Bash`/`Write`/`Read` calls are
not interleaved into the user-facing transcript. This is the same property that
makes `Explore` and `/review` clean. So the fix for leak source (1) is to **move
the deterministic plumbing off the main loop into a subagent.**

## 3. The split — two roles, one run

The dispatcher becomes two cooperating roles:

- **Conductor** — the `workflow` *Orchestrator Skill*, running in the main loop. It
  owns **everything the human sees or decides**: the opening announcement +
  roadmap, each `▶ Step` intent line, the single tidy "working…" beat per segment
  (the `Workflow` tool call itself), recaps synthesized from returned output, gates
  (`AskUserQuestion`), interactive / side-effecting orchestrator-node prompts,
  nested-workflow re-entry, and the finish sign-off.

- **Bookkeeper** — a dedicated `workflow-bookkeeper` *agent*, dispatched from the main
  loop, with an enforced toolset of **`Bash` / `Read` / `Write` only**. It owns
  **every deterministic CLI call**: `compile-workflow`,
  `run-journal latest|init|record-inputs|append`, `normalize-input`,
  `run-step args|eval`, `check-step-io`, the `Write` of `run-state.json.tmp`, and
  the `Read` of the manifest / gate `read_file` evidence. It returns a small
  structured digest, never speaks to the user, and — by toolset — *cannot* call
  `AskUserQuestion` or `Workflow`.

The boundary rule: **the conductor never issues a raw orchestration CLI call; the
bookkeeper never makes a human-facing or work-launching call.** Segments still run
in the main loop via the `Workflow` tool (the one legitimate "work is running"
beat), so this design does **not** depend on nesting a background `Workflow` inside
a subagent (the deferred "fully silent" variant, §10).

**Orchestrator-node work is a distinct surface, not plumbing.** An orchestrator-node
prompt does the node's actual *work* in the main loop — filesystem side effects,
microskill invocations, and possibly `AskUserQuestion` rounds (e.g. the `clarify`
node) — precisely because it may need to ask the human. Those calls are work, not
bookkeeping, and remain visible. The bookkeeper still hides the deterministic
plumbing *around* the node (the `run-step eval` that resolves the prompt and the
commit afterward). Hiding the non-interactive portions of orchestrator-node work is
a possible later refinement, out of scope here (§10).

## 4. The rhythm — ~1 collapsed bookkeeper line per step

Each bookkeeper dispatch does **"commit the step that just finished + prepare the
next one,"** so the visible surface is roughly one collapsed agent line per step
instead of ~5 raw tool blocks.

| Phase | Conductor (main loop, visible) | Bookkeeper (subagent, hidden) |
|---|---|---|
| **Setup** | Announce + roadmap; gather inputs (`AskUserQuestion`); present resume offer | ① `compile` + resume-scan + read manifest → return roadmap (description, steps[+labels], required/materialize inputs, defaults, gate_mode) + resume offer. ② mint run + `normalize-input` + `record-inputs` → return `run_dir` |
| **Segment i** | `▶ Step g/T · <label>` + "working…"; `Workflow(seg-i, args_i)`; recap from returned results | commit step i (results relayed **verbatim**) + prep step i+1 → `{committed \| failed+reason, next:{args \| eval \| evidence}}` |
| **Gate** | render the (bookkeeper-resolved) evidence core **verbatim** + conductor framing; `AskUserQuestion` | resolve `when` + the full `present` payload; commit the recorded choice + prep next |
| **Orch node** | execute the resolved prompt in the main loop (side effects / asks as the node needs) | resolve the prompt (`run-step eval`); commit the node result (relayed verbatim) + prep next |
| **Nested wf** | re-enter the `workflow` skill for the child → child gets its **own** conductor+bookkeeper pair; thread the parent cursor (display only) | resolve `child_inputs`; commit the child result + prep next |

Setup is the one asymmetric phase (two bookkeeper calls bracketing the human input
gathering), because the roadmap must exist before the conductor can announce, and
inputs must be gathered (a human step) before the run can be recorded.

## 5. Integrity contracts

Three contracts keep the split correct. The first two are today's rules, relocated;
the third is new and *strengthens* the existing gate invariant.

1. **Results relayed verbatim.** The conductor passes a segment's / node's returned
   output to the bookkeeper **without summarization** (identical to today's "store
   each into `results`" rule). The bookkeeper writes those exact bytes into
   run-state. The recap is the conductor's **separate** synthesis of the same data
   — never a substitute for the committed value.

2. **Exact CLI, no hand-assembly.** The bookkeeper uses the pinned commands and
   flags verbatim (`run-step args/eval`, the three-move checkpoint ordering, the
   setup commands) and **never** hand-assembles args or run-state — the
   determinism-critical rule the current SKILL.md already states ("never assemble
   args in your head"). The full plumbing contract, including the SECURITY guidance
   for untrusted materialize inputs (single-quote paths, reject shell
   metacharacters, write content via the `Write` tool not a shell arg), lives in
   the bookkeeper agent def verbatim.

3. **Gate `present` resolves inside the bookkeeper.** The bookkeeper resolves each
   `present` entry **in declared order** — scalar (`**<field>**: <value>`),
   fenced-json, or `{read_file:}` file-contents — and returns a **render-ready,
   ordered evidence payload**. The conductor prints it **verbatim**, wraps ephemeral
   framing around it, and never reorders/summarizes/substitutes it. This both
   preserves the approval-integrity invariant ("MECHANICAL, identical every run, no
   synthesis; recorded choice committed verbatim") **and** hides the `{read_file:}`
   `Read` calls that are visible today.

## 6. Determinism & resume (preserved by construction)

- **On-disk bytes identical.** run-state, journal, run-config, and every compiled
  artifact are produced by the same commands with the same flags — only the *caller*
  (bookkeeper vs. conductor) changed. `manifest_hash`, the four-key run-state, atomic
  commit ordering, and `failed_step` stamping are byte-identical.
- **Disk is the source of truth; the conductor goes light.** Because the bookkeeper
  reads *committed* run-state to build args, resolve `eval`, and resolve `present`,
  the conductor holds a fresh result only transiently (to recap it and relay it for
  commit) and need not accumulate a full in-memory `results` map. Resume becomes
  trivial: the bookkeeper runs the resume scan at setup and returns the offer; on
  resume the conductor adopts `run_dir`, sets `i = step_index`, and continues — no
  reconstruction.
- **Loud failures stay in the conductor.** An IO-check failure, an oversized-args
  error, or a throwing `eval` comes back as `{failed, reason}` from the bookkeeper;
  the conductor surfaces it in plain language and **stops without committing** —
  exactly today's behavior (the still-committed run-state points at the failed
  step, so a later resume re-runs it). The bookkeeper still stamps `--mark-failed-step`
  on an IO-check fail.
- **The bookkeeper is not a background segment.** It is a main-loop-dispatched Agent
  doing only pinned, deterministic CLI work with zero human dependency, so the
  "subagents fabricate when they can't ask" hazard does not apply — it makes no
  judgment calls, it runs commands and returns their JSON.
- **Narration ephemeral.** Conductor prose and the bookkeeper digest are printed /
  returned, never journaled, never written to run-state.

## 7. Prose ban-list (firm requirement)

The structural split removes leak source (1). Leak source (2) — the conductor's own
words — is fixed by replacing the soft "run quietly" rules with a hard ban-list in
the conductor voice section:

- **Banned from conductor output:** run-ids, manifest hashes; run-dir / `.tmp` /
  `.cat` / `.compiled` / `seg-N.js` paths; command names & argv (`run-step`,
  `check-step-io`, `run-journal append --commit-state`, `--mark-failed-step`);
  process phrases ("Committing state", "IO check passed", "Minting the run",
  "Recording inputs", "Building the segment args", "Resolving … against committed
  run-state"); raw JSON, byte counts, schema field names; internal node ids when a
  `label` exists.
- **Required of conductor output:** plain outcomes ("Saved.", "Ready.", "Done.");
  the `▶ Step g/T · <label>` cursor (the human `label`, never the id); artifact
  references by purpose **with the user-facing path they would actually open**
  (a *product* path like `/tmp/daily-planner/requirements.md` is fine — it is the
  user's output, not runtime plumbing); recaps that synthesize judgment calls and
  what is worth attention, never dumps.

## 8. The bookkeeper agent (the structural piece)

- **Component:** `catalog/agents/workflow-bookkeeper/AGENT.md` — a new
  `source: plugin` component, materialized into `.claude/agents/` by
  `initialize-harness` and registered on the **next session restart**.
- **Toolset (enforced):** `tools: Bash, Read, Write`. The harness grants exactly
  these — the agent cannot call `AskUserQuestion` or `Workflow`. (This is genuine
  enforcement, the same mechanism that makes `Explore` read-only — distinct from a
  prose instruction, which is not enforced.)
- **Contract:** the conductor dispatches it with a small task spec naming the phase
  ("setup-compile", "setup-record", "commit-and-prep <step i> with results <…>",
  "resolve-gate <step i>", "resolve-eval <step i>"), the def name, the `run_dir`
  (once minted), and any verbatim results to commit. It returns a small JSON digest:
  the roadmap (setup-compile), the `run_dir` (setup-record), or
  `{committed|failed+reason, next:{kind, args|eval|evidence|child_inputs}}` for a
  step boundary. The body is the plumbing contract lifted verbatim from today's
  SKILL.md (setup commands, three-move checkpoint, `run-step args/eval`, SECURITY
  guidance).
- **Why a dedicated agent (not inline):** the plumbing protocol lives in **one
  file** (lean conductor skill, single source of truth) and the enforced lock is
  defense-in-depth on top of the protocol's own guarantee that interactive/segment
  work stays with the conductor.

## 9. Decisions locked in

- **Hiding bar:** bookkeeper hides the **bookkeeping**; segments stay in the main
  loop with **one clean "working…" beat** (no Task IDs / transcript dirs / resume
  blurb). Not the "fully silent" variant.
- **Bookkeeper form:** a **dedicated locked-down agent** (`tools: Bash/Read/Write`),
  not an inline general-purpose dispatch.
- **Granularity:** **one bookkeeper dispatch per step boundary** (commit-prior +
  prep-next), not per tool call.
- **`present` resolution:** moved **into the bookkeeper**, returned render-ready;
  conductor prints verbatim. Evidence invariant preserved.
- **Cost:** one extra agent round-trip per step boundary (more for deeply-nested
  runs) is **accepted** in exchange for the clean transcript.

## 10. Out of scope (YAGNI)

- **Workflow-in-subagent** ("fully silent" — even the segment beat hidden). Deferred;
  would need a feasibility probe on nesting a background `Workflow` inside an Agent.
- Any change to the native Workflow engine, the compiler, or the run scripts
  (`compile-workflow`, `run-step`, `check-step-io`, `run-journal`, `normalize-input`
  keep byte-identical flags/argv).
- Headless/auto-mode narration: the plumbing still delegates, but no human watches,
  so prose/narration is moot; auto-mode gate behavior is unchanged.
- Rerun / Pickup: they inherit the same conductor↔bookkeeper split for free; no
  mode-specific work.

## 11. Success criteria

1. During a `/workflow` run the user-facing transcript shows **no orchestration-
   plumbing tool blocks** — none of `compile-workflow`, `run-step`, `check-step-io`,
   `run-journal`, `normalize-input`, the run-state `Write`, or the manifest /
   gate-evidence `Read` — and **no run-ids / hashes / runtime paths / command argv
   in the conductor's prose**. What stays visible is the conductor's narration
   (opening + roadmap, `▶ Step` intent lines, recaps, gate questions, finish), the
   one "working…" beat per segment, and any legitimate *work* an orchestrator node
   does in the main loop (§3 note).
2. Plumbing failures (IO-check fail, oversized args, throwing eval) still surface
   loudly in the conductor and stop the run without committing.
3. Determinism preserved: same inputs → byte-identical compiled output and on-disk
   run-state/journal; the full suite (`catalog/scripts/tests/`, `scripts/tests/`,
   `hooks/tests/`) stays green (scripts are untouched).
4. Resume, Rerun, and Pickup all still work, driven through the bookkeeper.
5. The gate evidence core renders identically every run and the recorded choice
   stays the author-declared label verbatim (approval-integrity invariant intact).

## 12. Testing

- **Scripts unchanged → existing suite green unchanged.** No new script tests are
  required for behavior that did not change; run the full suite as the regression
  gate.
- **Contract verification (prose):** a trace-through of the rewritten SKILL.md +
  the new AGENT.md against a real compiled manifest (`microskill-create`), confirming
  the conductor issues no raw CLI call and the bookkeeper never needs a human/segment
  tool.
- **End-to-end run:** execute a small real workflow and assert the transcript
  matches success criterion §11.1 (only conductor prose + collapsed bookkeeper lines
  + "working…" beats + gates).
- **Determinism:** the existing `test_deterministic` (compiled output) plus a
  by-hand confirmation that run-state/journal bytes are unchanged for a given run.
- **Activation note:** after `initialize-harness --apply`, the new agent registers
  on the next session restart — call this out in the plan's verification steps.

## 13. Open questions for the implementation plan

- Exact JSON shape of the bookkeeper digest per phase (setup-compile / setup-record /
  commit-and-prep / resolve-gate / resolve-eval) — pick the minimal fields the
  conductor needs and pin them.
- How much of today's SKILL.md plumbing text moves verbatim into AGENT.md vs. stays
  referenced — prefer moving it whole so there is one source of truth.
- Whether the conductor passes the manifest path + step index and lets the bookkeeper
  read everything, or also passes the just-returned results inline (it must, for the
  commit) — confirm the inline-results size stays within an Agent prompt comfortably
  (today the same bytes are `Write`-n by the conductor, so volume is unchanged).
