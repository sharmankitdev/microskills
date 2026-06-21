# Headless runs — the CI recipe

How to run a registered workflow **unattended** (CI, cron, `claude -p`) using the declared
gate-policy mechanism (`gate_mode: auto`). Authoritative grammar: DAG-RULES.md §8,
*Declared gate policy + headless mode*.

## The mechanism in one paragraph

A headless run never `AskUserQuestion`s. Signal it with **`--gate-mode auto`** (or `--headless`) on
the `/workflow` invocation, or **`MICROSKILLS_HEADLESS=1`** in the environment — the dispatcher
passes `--gate-mode auto` to `compile-workflow`, which stamps `gate_mode: "auto"` into the manifest
(only then — interactive compiles stay byte-identical). Under auto mode every pausing gate takes its
**author-declared `default`** and records it **verbatim** (`results[<gate-id>] = {choice: <default>}`);
a hard gate with no `default` makes the **compile die loud** (declare `default:`, or
`on_headless: fail` to have the run do the work and stop at that gate with resumable state, or run
interactively); an orchestrator node whose prompt requires asking the user **stops the run naming
the node**. Every recorded choice is author-declared, or the run fails loud — nothing is fabricated.

## What a def needs to be headless-able

1. Every pausing (hard / human-approval) gate declares a `default:` that is one of its effective
   options (declared `options`, or the implicit `confirm`/`stop` pair) — or `on_headless: fail` for
   a deliberate stop-here gate.
2. No orchestrator node whose prompt **requires** `AskUserQuestion` (interactive loops, judgment
   calls only a human can make).
3. All required inputs supplied on the invocation (the dispatcher cannot ask for missing ones).

A profile overlay is the canonical packaging — declare `gate_mode: auto` plus the gate defaults
there (see `catalog/workflow-defs/microskill-create/profiles/autonomous.yaml`), and the profile is
headless no matter how it is invoked: a doc/profile-declared `gate_mode` wins over the inherited
`--gate-mode` flag, in both directions.

## Status of the shipped defs

| Def | Headless? | How |
|---|---|---|
| `review-changes` | **Yes, today, with no gate-mode at all** — it is gate-free and fully background (summarize → fan-out reviews → collect → verify → synthesize; it ends at the report and never posts or pauses — the caller owns what happens next). It is runnable under `claude -p` as-is; this recipe only documents the (previously recipe-only) gap. | `claude -p "/workflow review-changes diff_path=..."` |
| `microskill-create` | Yes | `--profile autonomous` (declares `gate_mode: auto` + `default: approve` on the plan gate). Its implement/evaluate loop declares `on_exhaust: escalate` with `on_headless: fail`: a headless run whose loop exhausts the cap UNCONVERGED **stops at the `loop_exhaust` gate with committed run-state** instead of silently shipping the failing draft; a converging loop skips the gate and runs straight through. Continue the parked run interactively with `/workflow microskill-create pickup` — the parking gate re-presents with the full extend/accept/abandon protocol (the "approve in the morning" half of the pattern). |
| `workflow-create` | Yes | `--profile autonomous` (same mechanism). Its provision children (`microskill-create` autonomous) and nested `build` (`implement-rvs`, compile-time inlined as a guarded loop region) carry the same `on_exhaust` loop policy — an unconverged child stops at its `loop_exhaust` gate and ends the headless run early. NOTE: a park INSIDE a nested child is out of pickup's scope through the parent — picking up the parent re-runs the child afresh (interactively); the committed child work is not adopted. |
| `refine-requirements` | **Not via gate mode.** Its interactivity lives in the `clarify` ORCHESTRATOR node (a bounded AskUserQuestion loop), which `gate_mode` cannot soften — under a headless run the dispatcher stops naming `clarify`. | use `--profile autonomous`, which rewrites the prompts to an unattended single pass |

## GitHub Actions example

```yaml
# .github/workflows/nightly-review.yml
name: nightly-headless-review
on:
  schedule: [{ cron: "0 3 * * *" }]
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Generate the diff under review
        run: git diff origin/main...HEAD > /tmp/pr.diff
      - name: Headless review-changes (gate-free — runnable under claude -p today)
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          MICROSKILLS_HEADLESS: "1"   # belt-and-braces: any gated def compiled in this job goes auto
        run: |
          claude -p "/workflow review-changes diff_path=/tmp/pr.diff" \
            --permission-mode acceptEdits
```

For a **gated** def, the same job shape works once the gates carry defaults — e.g.
`claude -p "/workflow microskill-create autonomous requirement_path=./req.md ..."`; the
`autonomous` profile's `gate_mode: auto` makes the dispatcher record `{choice: approve}` at the plan
gate and continue. A run that hits an `on_headless: fail` gate (or a clarify-style node) exits
nonzero with the gate/node named in the run journal
(`.claude/workflow-defs/<name>/.compiled/runs/<run-id>/journal.jsonl`) — its run-state is resumable
interactively.

## Guarantees

- **Determinism**: the env signal is read by the dispatcher only; `compile-workflow` output is a
  pure function of argv + files. Auto stamping changes `manifest_hash`, so interactive run-state
  never resumes into a headless run (or vice versa).
- **Partition unchanged**: auto gates remain orchestrator checkpoints — segment bytes are identical
  to an interactive compile.
- **No fabrication**: every recorded gate choice is the human's pick or the author-declared
  `default`, verbatim; anything else fails loud with a nonzero outcome.
