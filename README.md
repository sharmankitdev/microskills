# microskills

An authoring + composition layer for **atomic microskills** and **declarative
workflows**, riding Claude Code's native Workflow engine. Packaged as a Claude
Code plugin and distributed over a catalog of reusable components.

- **Microskill** — one atomic unit of work with a declared input contract,
  bounded customization knobs (**profiles**), and an output schema. No branching,
  no hidden state.
- **Workflow** — a DAG that composes microskills (and agents, and nested
  workflows). Control flow — gates, loops, conditionals, fan-out — lives in the
  declarative `WORKFLOW.yaml`, compiled to background segments + human
  checkpoints.
- **Profiles** — overlays that customize a component without forking it.

## Install

```bash
# In Claude Code:
/plugin marketplace add sharmankitdev/microskills   # or a local path to this repo
/plugin install microskills@microskills
/microskills:initialize-harness                      # bootstrap .claude/ from the catalog
```

**Restart Claude Code after `initialize-harness`.** The materialized dispatchers,
per-component command shims, and the six agents register only on the next session
start — until you restart, `/sync-harness`, `/microskill-create`, `/workflow-create`,
and the agents will not resolve in-session.

## The harness (reference + vendoring model)

A project does not pull the whole catalog into its workspace. It declares a
**harness** — `harness/harness.yaml` — the curated working set it uses. Each entry
is tagged by source:

- **`source: plugin`** — a component from the plugin's catalog. `initialize-harness`
  materializes it straight into the generated `.claude/` runtime (referenced, never
  copied into `harness/`).
- **`source: custom`** — a component authored locally under `harness/<kind>/<name>/`.
  `harness-sync` reconciles it into `.claude/`.

```
catalog/                       # the plugin's canonical registry (committed; ships in the plugin)
  microskills/ workflow-defs/ agents/ scripts/ skills/ commands/

harness/harness.yaml           # this project's selection: microskills[] + workflows[], each {name, profiles, source}
harness/microskills|workflow-defs/<name>/   # committed bytes of source:custom components ONLY
        │
        │  initialize-harness   (source: plugin → .claude/, + the engine)
        │  harness-sync         (source: custom  → .claude/)
        ▼
.claude/                       # generated runtime (gitignored)
  scripts/ skills/ agents/ microskills/ workflow-defs/ commands/<name>.md
  templates/references/        # schemas + component templates (materialized engine asset)
  .harness-state.json          # ownership ledger; entries tagged plugin|custom (+ an engine block)
```

`initialize-harness` is the one globally-registered plugin command. On first run it
seeds `harness.yaml` from the catalog's base set, lays down the engine, and
materializes every `source: plugin` component. `harness-sync` then owns ONLY
`source: custom` components and **never touches** plugin/engine entries (and vice
versa). Both are scoped + non-destructive: a path with no ledger entry is never
modified or removed.

## Repository layout note

**This repository is the plugin source.** `catalog/` is the committed source of
truth; the runtime `.claude/` is **generated and gitignored — even here** (the repo
dogfoods the consumer flow). After cloning, rebuild it:

```bash
catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
catalog/scripts/harness-sync --apply             # any source:custom components (e.g. greet-user)
```

## Engine

Engine source lives under `catalog/scripts/` and is materialized into
`.claude/scripts/` at init:

| Piece | Role |
|---|---|
| `resolve-microskill` | Resolve a microskill + profile into an executable body. |
| `compile-workflow` | Compile a `WORKFLOW.yaml` into background segments + checkpoints. |
| `initialize-harness` | Bootstrap `.claude/` from the catalog: engine + `source: plugin` components. |
| `harness-sync` | Reconcile `source: custom` components `harness/` → `.claude/`. |
| `validate-microskill` / `validate-workflow` | Schema + semantic validation. |

The plugin registers exactly one global command — `/microskills:initialize-harness`.
Everything else (`/sync-harness`, `/microskill-create`, `/workflow-create`, the
per-component shims, and the `microskill`/`workflow` dispatchers) is materialized
into the project's `.claude/` at init.

## Development

Requires Python 3.11+ with `pyyaml` and `jsonschema`.

```bash
pip install pyyaml jsonschema pytest
# Bootstrap the runtime (catalog/ → .claude/), then run the suite:
catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
python3 -m pytest catalog/scripts/tests/ -v      # full suite
```

The reconcile loops (dry-run by default; `--apply` to execute + record the ledger):

```bash
catalog/scripts/initialize-harness --plan        # engine + source:plugin
catalog/scripts/harness-sync --plan              # source:custom add/update/remove
```

Compiled workflow output (`**/.compiled/`) and staging dirs are generated and
gitignored; `compile-workflow <name>` regenerates them.

## Status

The restructure is in place: a single committed `catalog/` registry; a v2
`harness.yaml` (`microskills[]`/`workflows[]`, `source: plugin|custom`);
`initialize-harness` (the sole global plugin command) materializing the engine +
`source: plugin` components; `harness-sync` reconciling `source: custom` only; and
`.claude-plugin/` packaging. See [`TODO.md`](./TODO.md) for what's next.

## License

[MIT](./LICENSE) © Ankit Sharma
