# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A composition layer for deterministic multi-agent orchestration on Claude Code. Three component kinds build on each other:

- **Microskills** — single-purpose, super-specialized units with a declared input contract and output schema. Internals may branch, loop, classify, use tools — even pause for a human (declare `AskUserQuestion` in `runtime.allowed_tools` and the compiler runs the node at an orchestrator checkpoint). `validate-microskill` checks only structure (sections, name, inputs table, output schema) and warns past ≤10 steps — it has no control-flow opinion; the "is this one job?" test lives in the create pipeline's semantic review, not a regex. A unit graduates to a workflow when it must compose *other* components — call another microskill/agent/workflow, or orchestrate a multi-node graph (fan-out, gated loops).
- **Workflows** — declarative `WORKFLOW.yaml` DAGs that compose microskills, agents, and nested workflows. All control flow (gates, loops, `when`, `for_each`) lives in the YAML. `compile-workflow` partitions the DAG into background segments + checkpoints — *same inputs → byte-identical output*.
- **Profiles** — YAML overlays deep-merged onto a component's `base.yaml`, tuning it without forking. Merge rules: `output_schema` **replaces** wholesale, `gates.add` appends, everything else deep-merges. Resolution order: explicit `--profile` flag → `base.profile.default` → `base`.

This repo **is the plugin source**. It dogfoods the consumer flow.

## Commands

This is a pure-Python engine — there is no build step. Requires Python 3.11+ with `pyyaml`, `jsonschema`, `pytest`.

```bash
pip install pyyaml jsonschema pytest

# Rebuild the generated runtime (.claude/) from the committed catalog. REQUIRED before tests —
# the compile-workflow e2e tests resolve components from the runtime .claude/, not catalog/.
catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
catalog/scripts/harness-sync --apply          # any source:custom components under harness/

# Full suite (this is exactly what CI runs):
python3 -m pytest catalog/scripts/tests/ scripts/tests/ hooks/tests/ -v

# A single file / single test:
python3 -m pytest catalog/scripts/tests/test_compile_workflow.py -v
python3 -m pytest catalog/scripts/tests/test_resolve_microskill.py -k "profile" -v
```

The two reconcile loops are **dry-run by default** (`--plan`); pass `--apply` to execute and record the ledger:

```bash
catalog/scripts/initialize-harness --plan     # preview: engine + source:plugin components
catalog/scripts/harness-sync --plan           # preview: source:custom add/update/remove
catalog/scripts/compile-workflow <name> [--profile P] [--override k=v]   # regenerate .compiled/
catalog/scripts/validate-microskill path/to/MICROSKILL.md [profiles/base.yaml ...]
catalog/scripts/validate-workflow   path/to/WORKFLOW.yaml [profile-overlays ...]
```

## Architecture — the parts that span multiple files

### The reference / vendoring model (read this before touching files)

```
catalog/         committed SOURCE OF TRUTH — the plugin's canonical registry
                 (microskills/ workflow-defs/ agents/ scripts/ skills/ commands/)
harness/         harness.yaml (this project's selection) + committed bytes of source:custom components ONLY
.claude/         GENERATED runtime — gitignored EVEN IN THIS REPO. Never edit by hand; overwritten on reconcile.
```

Edit source under `catalog/` (plugin components) or `harness/` (custom components). **Never edit the generated copies under `.claude/`** — they are rebuilt on the next reconcile.

Two scoped, non-destructive reconcile loops keep `.claude/` in sync, tracked by the `.claude/.harness-state.json` ownership ledger (never hand-edit it):

- **`initialize-harness`** owns `source: plugin` entries + the engine. The one globally-registered plugin command (`/microskills:initialize-harness`). Seeds `harness.yaml` from the catalog's `base:` set on first run; on re-run, hash-gated refresh of drifted plugin/engine components + reports `available_base` (adopt with `--adopt-base`). On `--apply` it stamps `plugin_version` into each added/updated ledger entry, so plans report version transitions (`0.8.0 -> 0.9.0`). A manifest entry's `version:` field is a **hold**: while it differs from the catalog's version, deployed bytes stay put and the pending change surfaces in every plan. `--eject <name>` transfers a `source: plugin` component to `source: custom` (vendors catalog bytes into `harness/`, rewrites the manifest line, atomically flips the ledger entry — the next `harness-sync` plans noop).
- **`harness-sync`** owns `source: custom` entries. It hard-errors if a manifest `custom` entry's name is plugin-owned in the ledger (eject instead — no clobber).

Neither touches the other's entries; a path with no ledger entry is never modified. Manifest schema: `templates/references/harness-schema.json` (closed grammar).

### How a workflow actually runs (two execution worlds)

`compile-workflow` topologically sorts the DAG and partitions it into **maximal background segments separated by orchestrator checkpoints**. A node never crosses that boundary:

- **Background segment** — `use:`/`agent:` nodes on Claude Code's native Workflow engine. **Cannot pause for a human** (no `AskUserQuestion`; a subagent that tries will silently fabricate). Background segments also can't spawn nested sub-agents.
- **Orchestrator checkpoint** — human-approval gates + `delegation: orchestrator` nodes, run in the main loop. All human interaction, filesystem side-effects, and nested-workflow calls live here.

The **dispatcher skills** are the conductors (`catalog/skills/microskill`, `catalog/skills/workflow`): they compile, read the run manifest (`.compiled/manifest.json`), gather inputs, run each segment autonomously, pause at each checkpoint, and thread node outputs forward via `args`. They own the runtime contract (profile resolution, input gathering) — so component bodies must **not** add a `## Setup` section.

Hand-authoring reference for `WORKFLOW.yaml` constructs: **DAG-RULES.md**.

### The plan→build agentic model

The create pipelines (`microskill-create`, `workflow-create`) are built from the **generic** microskills `task-plan` and `task-implement` — whose domain is selected by profile (`microskill` = default/base vs `workflow` overlay). Each runs **as** the domain agent named in its `runtime.agent` (e.g. `microskill-planner` vs `workflow-planner`) on the pinned `runtime.model`, reading the phase contract named by the `contract_doc` var. There is no nested sub-agent dispatch — the executor *is* the planner/implementer, because background segments can't nest. This is why the same microskill serves two domains with a ~2-line profile delta. The plan and build phases are each wrapped by a reusable RVS loop — `plan-rvs` and `implement-rvs` (review→verify→synthesize over the staged artifact) — which the two pipelines compile-time inline; the host owns the single vendor→sync→compile `finalize`. (As of the 2026-06-21 refactor the `refine-requirements` front-end and the `task-evaluate` check phase were removed as redundant — the adversarial RVS review plus the `approve_plan` human gate own quality. The old `build-workflow-from-plan` workflow is also retired.)

## Conventions

- **Tests are hermetic and test-first.** Every script change ships `tmp_path` tests under `catalog/scripts/tests/` — build a throwaway world, pass all roots as flags, assert on JSON output and on-disk state. No test touches the real repo (except tests that intentionally point at the real `catalog/`). See `test_harness_sync.py` for the pattern.
- **Conventional Commits** drive automated releases. Format `<type>(<scope>): <subject>`; types `feat|fix|perf|refactor|revert|docs|chore|ci|test|build`; scopes `harness|microskill|workflow|scripts|ci`. PRs are squash-merged so the **PR title** must be a valid conventional commit (`pr-title.yml` enforces). While in `0.x`: `feat`/`feat!` → minor, `fix`/`perf`/`refactor`/`revert` → patch, nothing bumps major.
- **Releases are fully automated** by semantic-release on push to `main`. The canonical version lives in `.claude-plugin/plugin.json` — **never hand-edit it** (`scripts/set_plugin_version.py` writes it). Land changes via **PR, not direct push to main** — a direct push breaks the release job (its back-push is blocked by the branch ruleset).
- After `initialize-harness`, registration of dispatchers/shims/agents only takes effect on the **next** Claude Code session start.
