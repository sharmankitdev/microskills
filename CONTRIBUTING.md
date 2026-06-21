# Contributing

Thanks for your interest. This project is an authoring layer for atomic
microskills and declarative workflows on Claude Code. A few conventions keep it
coherent.

## Setup

```bash
pip install pyyaml jsonschema pytest
# Rebuild the generated runtime from the committed catalog (it is gitignored):
catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
python3 -m pytest catalog/scripts/tests/ -v
```

The full suite must be green before a change merges. CI runs the same command.

## Repository model

- `catalog/` is the **committed source of truth** — the plugin's canonical registry
  of microskills, workflow-defs, agents, scripts, and dispatcher skills.
- `harness/harness.yaml` is this project's selection (v2: `microskills[]` +
  `workflows[]`, each `{name, profiles, source}`). `harness/<kind>/<name>/` holds the
  committed bytes of `source: custom` components **only**.
- `.claude/` is the generated runtime. It is **gitignored, even in this repo** —
  rebuild it with `initialize-harness` (+ `harness-sync` for custom components).
- `.claude/.harness-state.json` is the ownership ledger. Never hand-edit it.

Edit the source under `catalog/` (for plugin components) or `harness/` (for your own
custom components) — never the generated copies under `.claude/`, which are
overwritten on the next reconcile.

## Adding a component

Prefer the create flows — they plan (with an adversarial review), get your
approval, then implement in a self-correcting review loop, then vendor into
`harness/` (as `source: custom`) + sync:

- `/microskill-create "<requirement>"`
- `/workflow-create "<requirement>"`

To author a custom component by hand: add it under
`harness/<microskills|workflow-defs>/<name>/`, add an entry to `harness/harness.yaml`
under `microskills:`/`workflows:` with `source: custom`, then:

```bash
catalog/scripts/harness-sync --plan      # review
catalog/scripts/harness-sync --apply     # install + ledger + generated shim
```

To add a component to the shared catalog itself, place it under
`catalog/<microskills|workflow-defs>/<name>/`; tag it `base: true` (MICROSKILL.md
frontmatter / WORKFLOW.yaml top level) if `initialize-harness` should seed it into a
new project's `harness.yaml`.

## Manifest entries (harness.yaml, v2)

```yaml
version: 2
microskills:
  - name: <kebab-case>
    source: plugin | custom   # plugin = materialized from the catalog by initialize-harness;
                              # custom = authored under harness/, reconciled by harness-sync
    profiles: [base]          # explicit list, or "*" for all; omit to mean all
workflows:
  - { name: <kebab-case>, source: custom, profiles: "*" }
```

Schema: `templates/references/harness-schema.json` (closed grammar — unknown fields
are rejected). `source: plugin` entries are owned by `initialize-harness`;
`source: custom` entries by `harness-sync`. Neither command touches the other's
entries.

## Tests

Test-first. Every script change ships with hermetic `tmp_path` tests under
`catalog/scripts/tests/` (see `test_harness_sync.py` for the pattern: build a
throwaway world, pass all roots as flags, assert on JSON output and on-disk state).
No test may touch the real repo. Tests that exercise the real catalog point at
`catalog/`; the `compile-workflow` end-to-end tests resolve from the runtime
`.claude/`, so run an `initialize-harness` first (the Setup step does this).

## Commits

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) — the
prefix drives automated releases (see Releases). Keep the working tree's tests
green per commit and describe the *why*, not just the *what*.

Format: `<type>(<scope>): <subject>`

- **Types:** `feat`, `fix`, `perf`, `refactor`, `revert`, `docs`, `chore`, `ci`,
  `test`, `build`.
- **Scopes (optional):** `harness`, `microskill`, `workflow`, `scripts`, `ci`.
- **Breaking:** add a `!` (`feat!: …`) or a `BREAKING CHANGE:` footer.

PRs are squash-merged, so the **PR title** must be a valid Conventional Commit —
CI (`pr-title.yml`) enforces this.

## Releases

Releases are fully automated by
[semantic-release](https://semantic-release.gitbook.io/) on every push to `main`
(`release.yml`). It reads the conventional-commit history since the last tag,
computes the next version, writes it into `.claude-plugin/plugin.json` (the
canonical version — **never hand-edit it**), updates `CHANGELOG.md`, creates the
`vX.Y.Z` tag, and publishes the GitHub Release.

The project is intentionally pre-stable (`0.x`). Bump mapping while in `0.x`:

| Commit | Bump | Example |
|---|---|---|
| `fix:` | patch | bug fix in a script / dispatcher / microskill body |
| `feat:` | minor | new microskill / workflow / agent / script capability |
| `feat!:` / `BREAKING CHANGE:` | minor | harness-schema, dispatcher-contract, or component rename/removal |

Nothing bumps the major while in `0.x`. The jump to `1.0.0` is a deliberate manual
release made when the harness contract is frozen — not produced automatically.
Commits of type `docs`, `chore`, `ci`, `test`, or `build` alone do not trigger a
release.

> The plugin release version is independent of the harness manifest schema
> version (`harness/harness.yaml` `version: 2`) — the two are not coupled.
