---
name: sync-harness
description: >
  Reconcile the vendored harness/ source-of-truth into the runtime .claude/.
  Runs .claude/scripts/harness-sync --plan, presents the add/update/remove
  actions plus any conflicts, confirms, then applies — installing/updating/
  removing ONLY harness-managed components and never touching other .claude/
  contents. Triggered via `/sync-harness`, or `/sync-harness --apply` to skip the
  preview and apply directly after the confirm gate.
---

# Harness Sync Dispatcher

Single owner of the reconcile contract for `source: custom` components. `harness/harness.yaml`
lists what this project uses; `harness/` holds the committed bytes of its custom components;
`.claude/.harness-state.json` is the ownership ledger. This dispatcher reconciles ONLY
`source: custom` entries (authored under `harness/`); `source: plugin` entries are owned by
`initialize-harness` and are never read, modified, or removed here. It previews the reconcile,
gets human confirmation, then applies it. Scoped + non-destructive: paths sync never installed
have no ledger entry and are never touched.

## Setup

Run before any other step. Execute exactly once per invocation.

1. **Plan.** Run via Bash from the project root: `.claude/scripts/harness-sync --plan`.
   Parse stdout JSON into `_plan`.
   - Exit `2` → stop and surface the JSON `error` (environment problem: missing
     PyYAML/jsonschema, or no `harness.yaml`). Do not proceed.
   - Exit `0` or `1` → continue (`1` only means conflicts/errors are present to show).
2. **Present.** Print a compact summary from `_plan.summary`, then list each entry of
   `_plan.actions` (action · name · kind), each `_plan.conflicts` (name · path · reason),
   and each `_plan.errors` (name · reason). If `actions` is empty AND there are no
   conflicts AND no errors, report "harness already in sync — nothing to do" and stop.
3. **Resolve conflicts.** For each entry in `_plan.conflicts`, use `AskUserQuestion`
   (header = the component name; choices `skip` / `overwrite`). `overwrite` makes sync
   take ownership of the pre-existing unmanaged path; `skip` leaves it and the component
   stays uninstalled this run. Collect answers into `--resolve <name>=<answer>` flags.
   Default to `skip` for any conflict the user does not explicitly answer.
4. **Confirm.** If there is at least one `add` / `update` / `remove` action (or any
   conflict the user chose to `overwrite`), use `AskUserQuestion` (`apply` / `cancel`).
   On `cancel`, stop and report that nothing was changed (the plan wrote nothing).
5. **Apply.** Run via Bash: `.claude/scripts/harness-sync --apply [--resolve <name>=<answer> ...]`.
   Parse stdout JSON. Exit `2` → stop and surface `error`. Exit `0` or `1` → continue.
6. **Report.** Summarize what was applied (added / updated / removed names) and confirm
   `state_written`. If the apply exited `1` (partial — some components skipped due to
   unresolved conflicts), name each skipped component and the `--resolve <name>=overwrite`
   that would converge it. Do NOT claim full success on a partial apply.

## Failure modes

- **harness-sync environment error (exit 2)** — missing PyYAML/jsonschema, or no
  `harness.yaml`: stop, surface the JSON `error`, do not proceed.
- **Plan/apply stdout not JSON** — stop and surface stderr only; do not act on partial output.
- **User cancels at the confirm gate** — stop; report that the plan wrote nothing and
  `.claude/` is unchanged.
- **Partial apply (exit 1 after `--apply`)** — report the applied components AND each
  skipped one with its remedy; do not report the run as fully successful.
