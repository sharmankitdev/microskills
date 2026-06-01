---
name: initialize-harness
description: >
  Bootstrap a project's runtime from the plugin catalog. Seeds harness/harness.yaml from
  the catalog base set (if absent), then materializes the engine + every source:plugin
  component into .claude/. The one global entry point — run once per project, and again to
  re-pull after a plugin update. Triggered via `/initialize-harness`.
---

# Initialize Harness

The single globally-registered plugin command. It lays down the runtime that exposes every
other entry point (`/sync-harness`, `/microskill-create`, `/workflow-create`, and the
per-component shims) into the project's `.claude/`. It owns all `source: plugin`
provenance; `harness-sync` owns `source: custom` and is never touched here.

## Setup

Run before any other step. Execute exactly once per invocation.

1. **Plan.** Run via Bash from the project root:
   `${CLAUDE_PLUGIN_ROOT}/catalog/scripts/initialize-harness --plan`.
   Parse stdout JSON into `_plan`.
   - Exit `2` → stop and surface the JSON `error` (missing PyYAML/jsonschema, or catalog
     not found because `CLAUDE_PLUGIN_ROOT` is unset). Do not proceed.
   - Exit `0` or `1` → continue (`1` only means conflicts/errors are present to show).
2. **Present.** Report `_plan.seeded_harness_yaml` (whether a fresh `harness.yaml` will be
   written from the base set), `_plan.engine` (action + file count), the `_plan.summary`
   counts, then list each `_plan.actions` (action · name), each `_plan.conflicts`
   (name · path · reason), and each `_plan.errors` (name · reason). If there is nothing to
   do (engine `noop`, no actions, no fresh seed, no conflicts/errors), report "already
   initialized — nothing to do" and stop.
3. **Confirm.** If there is anything to do, use `AskUserQuestion` (`apply` / `cancel`). On
   `cancel`, stop and report that nothing was changed (the plan wrote nothing).
4. **Apply.** Run via Bash:
   `${CLAUDE_PLUGIN_ROOT}/catalog/scripts/initialize-harness --apply`.
   Parse stdout JSON. Exit `2` → stop and surface `error`. Exit `0` or `1` → continue.
5. **Report.** Summarize: whether `harness.yaml` was seeded, the engine materialized, the
   `source: plugin` components added/updated, and confirm `state_written`. Tell the user
   the project is initialized — they can now run `/sync-harness` for any `source: custom`
   components they author under `harness/`, plus the create flows (`/microskill-create`,
   `/workflow-create`). **Tell them to restart Claude Code**: the materialized dispatchers,
   per-component command shims, and agents register only on the next session start, so
   those commands and the agents will not resolve until they restart.

## Failure modes

- **Environment error (exit 2)** — missing PyYAML/jsonschema, or catalog not found
  (`CLAUDE_PLUGIN_ROOT` unset / no `--catalog`): stop, surface the JSON `error`, do not proceed.
- **Existing harness.yaml fails schema (exit 1, errors present)** — surface each schema
  error; the manifest must be fixed before initialization can proceed.
- **Conflicts (unmanaged paths already on disk)** — name each conflicting path. These are
  files init did not install (init does not overwrite unmanaged paths). Remove or relocate
  them, then re-run.
- **User cancels at the confirm gate** — stop; report that the plan wrote nothing and
  `.claude/` is unchanged.
