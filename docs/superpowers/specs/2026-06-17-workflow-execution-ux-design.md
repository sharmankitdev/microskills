# Workflow Execution UX — the "conductor" redesign

**Date:** 2026-06-17
**Status:** Approved design, pending implementation plan
**Scope:** How a compiled workflow *presents itself* while it runs. Plus the
minimal structural support (authored human names) the presentation layer needs.

---

## 1. The problem

Running `/workflow <name>` today feels like watching a CI build log, not like
working with a colleague. The maintainer's complaints, each traced to a concrete
root cause in the code:

| Complaint | Root cause |
|---|---|
| "Runtime feels dead." | A background segment runs **35–57 min** (recorded) under a single static `Step i/M` header. The dispatcher is architecturally blocked from narrating *inside* a segment, and the post-segment recap is mandated terse ("2–4 lines, never paste object arrays"). `catalog/skills/workflow/SKILL.md` segment block + recap rubric. |
| "CLI is full of bash that makes no sense." | ~5 internal CLI scripts fire **per step** with raw argv (`run-state.json.tmp`, `manifest_hash`, `--commit-state`, `--mark-failed-step`) surfaced as the user-facing surface. A skill like `/review` hides exactly this class of plumbing. |
| "You don't know where you are." | Only a coarse `Step i/M` counter; no upfront roadmap (the roadmap exists only in `--plan` mode, which halts before running); the counter **resets inside nested workflows** (`Step 1/6` → child's `Step 1/9`). |
| "Names are programmatic aliases." | There is **no human-label field anywhere** on a node or gate — the schema's `additionalProperties:false` forbids one. The dispatcher *title-cases the snake_case id at render time* (`synth_req` → "Synth req"), which is both ugly and non-deterministic. Review dimensions reuse one token (`req-nfr`) as profile name **and** node-id suffix **and** join key, leaving no slot for a readable name. |
| Core thesis: "a workflow is a sophisticated skill; it should orchestrate as seamlessly." | The dispatcher was authored — deliberately — as a *bookkeeping protocol that reports*. Gate evidence is rendered "MECHANICALLY … no synthesis, so the approver sees the same evidence every run." That is the opposite of a skill, which *performs* the work and hides its machinery. |

## 2. The north star

**The workflow dispatcher should conduct a run the way a good Claude Code
session conducts itself.**

The reference experience is the brainstorming session that produced this very
document:

1. **Announce intent** before delegating ("I've kicked off a deep parallel read…").
2. Let the work run **fully autonomously and opaque** — the user explicitly does
   *not* want token-level narration during autonomous work, and trusts it will
   come back.
3. When control **reverts, synthesize** what happened into a briefing tied to the
   user's concerns — not a status line.
4. At every **decision point**, keep the user in control with framed, legible choices.
5. Never expose the **plumbing**.

The key enabling realization: **the dispatcher already runs in the main loop with
full reasoning capability, and a segment hands back its complete structured output
when it returns.** It already *has* everything needed to brief the user like a
colleague. It is currently *instructed not to*. We are not adding a capability;
we are removing a gag.

## 3. The feasibility constraint (and why presentation-first still wins)

A focused probe (recorded in the session) established two hard facts about the
runtime:

1. **Segment launch fully blocks the main loop.** The dispatcher runs a segment
   via the native **Workflow tool**, which freezes the orchestrator for the
   segment's entire duration. There is no interleaving point to emit text
   mid-segment.
2. **The `phase()`/`log()` heartbeat never touches disk in a live run.** Those are
   host globals consumed by the engine's `/workflows` progress UI; they are never
   persisted, so nothing can tail/poll/relay them. (The incremental journal that
   does exist belongs to `run-segment-host`, which is **test-harness-only**.)

Therefore **true inline narration during a segment is not buildable in the
dispatcher** — it would require a new Claude Code engine capability.

This does **not** block the goal. A real skill also goes quiet while it works
(`/review` shows an announcement, then silence, then "Review complete: N
findings…"). The dead feeling comes not from silence-during-work but from the
**missing framing around the work**: no announcement, no roadmap, cryptic names,
a terse recap, exposed bash. Every one of those is in the dispatcher's control.
The one genuine during-work surface — the native `/workflows` box — already
updates live; we make it legible (authored phase names) and mention it in passing.

## 4. The designed experience

A full run under this design (`microskill-create`, end to end):

```
🛠  microskill-create — I'll turn your requirement into a validated microskill.

   Four steps, ~3–6 min:
     1 · Plan the microskill      2 · Review the plan with you (you decide)
     3 · Build & check            4 · Finalize & register

▶ 1 / 4 — Planning the microskill
   Designing the input contract and output schema from your requirement.
   Working on it… (~2 min; peek at /workflows if you like)

   ✓ The planner came back with a microskill called "extract-table-rows" —
     takes a markdown file path, returns the rows as structured objects.
     Six inputs, a clean 4-step linear body, output schema with three fields.
     One judgment call worth noting: it assumes the first row is a header.

▶ 2 / 4 — Your call on the plan
   That header assumption is the one thing I'd flag before we build. Otherwise
   the contract looks solid and atomic.
       [ Approve ]   [ Revise ]   [ Stop ]      → Approved.

▶ 3 / 4 — Building & checking
   Implementing it, then running the evaluator until it passes. (~2 min)

   ✓ Built and passed in two rounds — the first pass missed an edge-case note
     the evaluator wanted, the second added it and came back clean. Wrote
     MICROSKILL.md and profiles/base.yaml.

▶ 4 / 4 — Finalizing
   ✓ Vendored into harness/, registered, recompiled. Saved.

✅ Done — "extract-table-rows" is registered and ready (run took 4m12s).
   Try it:  /microskill extract-table-rows
```

The exact glyphs (`▶`, `✓`, `🛠`) and column layout are illustrative, not
load-bearing; the implementation plan can settle final formatting. What *is*
load-bearing: announcement → roadmap → per-step intent → (opaque autonomous work)
→ synthesis recap → conductor-framed gate → human wrap-up with a next action.

The duration hints (`~2 min`, `run took 4m12s`) are **optional polish**: the
end-of-run total is read from the journal timestamps (already recorded); the
per-step *estimates* are derived from prior-run journal timings for the same
workflow when available, and simply omitted otherwise. They are never a hard
promise and never gate anything.

## 5. The five changes

These are the units of work. Each has a clear home and a clear interface.

### 5.1 Conduct, don't report — *dispatcher voice*
**Where:** `catalog/skills/workflow/SKILL.md`
Rewrite the dispatcher's execution contract from "bookkeeping protocol" to
"conductor." Open every run with an announcement + roadmap. Carry a "you are
here" cursor that **survives nested workflows** (today `Step 1/6` resets into a
child's `Step 1/9` — the cursor must read against a global denominator or a
breadcrumb like `2/4 ▸ refine-requirements 3/9`).

### 5.2 The recap becomes a synthesis — *the heart*
**Where:** `catalog/skills/workflow/SKILL.md` (recap rubric)
The dispatcher already receives the segment's full structured output keyed by
node id. Replace the terse-status rubric with a directive to **brief the user on
what came back** — in prose, surfacing judgment calls and what's worth their
attention. This is the behavior the maintainer called "fantastic," made default.
Guardrail retained: never dump raw JSON / object arrays; synthesize them.

### 5.3 Gates: conductor's voice *over* invariant evidence
**Where:** `catalog/skills/workflow/SKILL.md` (gate rendering)
Today gates render "MECHANICALLY … the same evidence every run" — a deliberate
determinism/trust property. Keep that **deterministic evidence core** (counts,
the plan, verdicts — rendered identically every run) and **layer an ephemeral
conductor framing on top** ("here's what the planner decided and what I'd push
back on"). The human gets both: invariant facts *and* a synthesis. The framing
never replaces or mutates the evidence.

### 5.4 Plumbing goes silent
**Where:** `catalog/skills/workflow/SKILL.md` (setup + 3-move checkpoint)
The internal CLI scripts (`compile-workflow`, `run-step`, `check-step-io`,
`run-journal`, `normalize-input`) run quietly; their outcomes translate to plain
language ("Saved" not `--commit-state run-state.json.tmp`). Same pattern as
`/review` hiding its `.reviews/` sidecars.

### 5.5 Authored human names — *the structural piece*
**Where:** `templates/references/workflow-schema.json`,
`catalog/scripts/compile-workflow`, `catalog/scripts/validate-workflow`,
the catalog workflow defs.

- Add an **optional `name:`** field to node and gate grammar (relax
  `additionalProperties:false`).
- Carry `name:` into the **compiled manifest** step records so the displayed
  name is authored, not improvised — **and exclude it from `manifest_hash`**
  (decision §6) so a label edit never invalidates an in-flight run.
- Provide a **deterministic humanization fallback** in the compiler for unlabeled
  nodes (e.g. `check_gaps` → "Check gaps"), replacing the dispatcher's
  render-time title-casing. Even un-migrated defs read better and deterministically.
- **Decouple the review/judge `over:` token** from its triple duty so a dimension
  like `req-nfr` can carry a readable name ("Non-functional requirements
  coverage") while keeping its short join key.
- **Backfill authored `name:`** on this repo's own catalog defs (dogfooding) so
  the first improved run already reads beautifully.

## 6. Decisions locked in

- **Scope:** Both layers, **presentation-first**. Presentation lands against
  today's manifest; authored names follow as the structural phase.
- **Label hashing:** Labels live **in the manifest but out of `manifest_hash`** —
  they are presentation, not execution semantics, so a label change must never
  invalidate a resumable run.
- **Naming model:** **Optional `name:` + deterministic humanization fallback**,
  plus a one-time backfill of the catalog defs. Not mandatory-on-every-node, not
  a separate display-name map.
- **During-work liveness:** **Settled — none.** No engine feature request, no
  incremental heartbeat file, no `Monitor` relay, no artificial segment
  splitting, no during-segment inline narration. The user does not want it; the
  `/workflows` box is mentioned in passing as the optional live surface.

## 7. The determinism boundary

The engine's headline promise is *same inputs → byte-identical output*. This
design preserves it by a clean split:

- **Ephemeral (model-authored, never hashed, never a work product):** the run
  narration, recap synthesis, and gate conductor-framing.
- **Deterministic (reproducible, hashed where it matters):** the staged work
  products (unchanged), the manifest, the journal, the gate evidence core, and
  the authored labels (deterministic content; merely excluded from the hash for
  resume-stability).

This mirrors how this very session works: the prose briefings are ephemeral; the
artifacts they describe are reproducible.

## 8. Out of scope (YAGNI)

- Any change to the native Workflow engine.
- An on-disk heartbeat / `Monitor`-based inline relay.
- Re-partitioning the compiler to create more checkpoints for narration cadence.
- Mandatory labels / a separate display-name map structure.
- Headless/auto-mode narration changes (auto mode stays narration-free by design;
  a richer post-hoc journal is a possible later follow-up, not part of this work).

## 9. Success criteria

1. A first-time observer of a `/workflow` run can state, at any moment, **what is
   happening, which step they're on, and how many remain** — including inside
   nested workflows — without running `--plan` first.
2. **No raw orchestration bash argv** (`run-state.json.tmp`, `manifest_hash`,
   `--commit-state`, etc.) appears in the user-facing transcript.
3. Every step header and gate reads as a **human phrase**, never a title-cased
   snake_case id, on both new and existing (un-migrated) defs.
4. Each segment recap is a **synthesis of the returned output**, not a status
   line — it names the judgment calls and what's worth attention.
5. `manifest_hash` is **unchanged by adding/editing a `name:`**, so existing
   in-flight runs still resume.
6. **Determinism preserved:** staged outputs remain byte-identical for the same
   inputs; the full test suite (`catalog/scripts/tests/`, `scripts/tests/`,
   `hooks/tests/`) passes; new schema/compiler behavior ships with hermetic
   `tmp_path` tests (test-first, per repo convention).

## 10. Open questions for the implementation plan

- Exact "you are here" representation across nesting: global denominator vs.
  breadcrumb (`2/4 ▸ child 3/9`). Both satisfy the success criterion; pick the
  one that's cleanest to render from the manifest.
- Final glyph/format vocabulary for the trace (cosmetic).
- Whether the humanization fallback lives purely in the compiler (preferred, so
  the manifest already carries a usable label) or partly in the dispatcher.
```
