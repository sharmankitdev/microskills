---
name: initialize-harness
description: >
  Bootstrap a project's runtime from the plugin catalog. Seeds harness/harness.yaml from
  the catalog base set (if absent), then materializes the engine + every source:plugin
  component into .claude/. The one global entry point — run once per project, and again
  after a plugin update to refresh drifted components and adopt newly-released base ones.
  Triggered via `/initialize-harness`.
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
   counts, then list each `_plan.actions` (action · name — `update` actions carry a
   `transition` like `0.8.0 -> 0.9.0` when the plugin version moved; `hold` actions are
   version-pinned components whose pending change is withheld: show name · `pinned` →
   `available`, and note the pin in `harness.yaml` must move before they reconcile), each
   `_plan.conflicts` (name · path · reason), and each `_plan.errors` (name · reason). Also list
   `_plan.available_base` (name · kind) — base components released in the catalog but not yet
   in this project's `harness.yaml` (e.g. after a plugin update). If there is nothing to do
   (engine `noop`, no actions other than `hold`, no fresh seed, **no `available_base`**, no
   conflicts/errors), report "already initialized — nothing to do" (mentioning any held
   components) and stop.
3. **Confirm.** If there is anything to do, use `AskUserQuestion`. When `_plan.available_base`
   is non-empty, offer three choices: `apply` (materialize the listed actions only),
   `apply + adopt base` (also add the `available_base` components to `harness.yaml` as
   `source: plugin` and materialize them), or `cancel`. When `available_base` is empty, offer
   `apply` / `cancel`. On `cancel`, stop and report that nothing was changed (the plan wrote
   nothing).
4. **Apply.** Run via Bash:
   `${CLAUDE_PLUGIN_ROOT}/catalog/scripts/initialize-harness --apply` — append `--adopt-base`
   iff the user chose "apply + adopt base" in step 3.
   Parse stdout JSON. Exit `2` → stop and surface `error`. Exit `0` or `1` → continue.
5. **Report.** Summarize: whether `harness.yaml` was seeded, the engine materialized, the
   `source: plugin` components added/updated, any `_result.adopted_base` newly added to
   `harness.yaml`, and confirm `state_written`. Tell the user
   the project is initialized — they can now run `/sync-harness` for any `source: custom`
   components they author under `harness/`, plus the create flows (`/microskill-create`,
   `/workflow-create`).
   Then tell them how to **activate** what changed — branch on the plan:
   - If `_plan.seeded_harness_yaml` is true **or** `_plan.engine.action` is `add` / `update`
     (first-time init, or the engine was materialized/changed): **restart Claude Code**. The
     engine's **agents** (`.claude/agents/`) register only on the next session start, so the
     create flows and per-component agents will not resolve until they restart.
   - Otherwise (engine `noop` — only `source: plugin` components were added/updated): they only
     need to run **`/reload-skills`** to register the new command shims. The dispatchers read
     each component's data (`MICROSKILL.md` / `WORKFLOW.yaml`) fresh at runtime, so no restart
     is required.

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
