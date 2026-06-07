## [0.9.0](https://github.com/sharmankitdev/microskills/compare/v0.8.0...v0.9.0) (2026-06-07)

### Features

* **workflow:** pass create-pipeline requirement by reference (requirement_path) ([#13](https://github.com/sharmankitdev/microskills/issues/13)) ([4b6eab3](https://github.com/sharmankitdev/microskills/commit/4b6eab34bce44464f4ea4704c31023977dcf9deb))

## [0.8.0](https://github.com/sharmankitdev/microskills/compare/v0.7.0...v0.8.0) (2026-06-07)

### Features

* **workflow:** pass large inputs by reference via materialize:file ([#12](https://github.com/sharmankitdev/microskills/issues/12)) ([00488c9](https://github.com/sharmankitdev/microskills/commit/00488c9bf6aec0f4d7221258bbccc1f9b6c7106b))

## [0.7.0](https://github.com/sharmankitdev/microskills/compare/v0.6.0...v0.7.0) (2026-06-07)

### Features

* **workflow:** make review-changes composable; add phase_group + lite/comprehensive profiles ([#11](https://github.com/sharmankitdev/microskills/issues/11)) ([d20ba6f](https://github.com/sharmankitdev/microskills/commit/d20ba6f4412c7b3d33b097108144e27ac73d8e59))

## [0.6.0](https://github.com/sharmankitdev/microskills/compare/v0.5.0...v0.6.0) (2026-06-06)

### Features

* **workflow:** add code-review pipeline with intra-segment sibling parallelism ([#9](https://github.com/sharmankitdev/microskills/issues/9)) ([e3dd79a](https://github.com/sharmankitdev/microskills/commit/e3dd79a21960754287872c45286bc417d8823496))

## [0.5.0](https://github.com/sharmankitdev/microskills/compare/v0.4.1...v0.5.0) (2026-06-06)

### Features

* **workflow:** nested-workflow profile passthrough + capability-named planner output ([#7](https://github.com/sharmankitdev/microskills/issues/7)) ([47694dd](https://github.com/sharmankitdev/microskills/commit/47694dd4aec49fb001375bf6767d364dd36416e5))

## [0.4.1](https://github.com/sharmankitdev/microskills/compare/v0.4.0...v0.4.1) (2026-06-06)

## [0.4.0](https://github.com/sharmankitdev/microskills/compare/v0.3.0...v0.4.0) (2026-06-04)

### Features

* **workflow:** profile-driven composition, nested workflows, and DAG safety/ergonomics ([#4](https://github.com/sharmankitdev/microskills/issues/4)) ([2f29bb9](https://github.com/sharmankitdev/microskills/commit/2f29bb994da7cdb43344b83ad8691dab666f137c)), closes [#3](https://github.com/sharmankitdev/microskills/issues/3) [#2](https://github.com/sharmankitdev/microskills/issues/2) [#1](https://github.com/sharmankitdev/microskills/issues/1)

## [0.3.0](https://github.com/sharmankitdev/microskills/compare/v0.2.0...v0.3.0) (2026-06-02)

### Features

* **hooks:** surface harness-drift advisory to the user and offer to run it ([8889943](https://github.com/sharmankitdev/microskills/commit/88899439af57370cff170d28097d4424224b07f8))

## [0.2.0](https://github.com/sharmankitdev/microskills/compare/v0.1.0...v0.2.0) (2026-06-02)

### Features

* **catalog:** tag monolith-orchestrator components as base ([ff80007](https://github.com/sharmankitdev/microskills/commit/ff80007833c92403e48184492dd0815251be9017))
* **initialize-harness:** detect + adopt newly-released base components ([0261024](https://github.com/sharmankitdev/microskills/commit/0261024112629a537ee37887fe6f85e0d5539063))

# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project follows
[Semantic Versioning](https://semver.org/) (currently in the `0.x` pre-stable
range) and releases are automated by
[semantic-release](https://semantic-release.gitbook.io/) — see `CONTRIBUTING.md`.

## [0.1.0] - 2026-06-02

### Added
- **Harness vendoring model** — `harness/` committed source of truth +
  `harness/harness.yaml` manifest, reconciled into the `.claude/` runtime by
  `.claude/scripts/harness-sync` (scoped, non-destructive; ownership tracked in
  `.claude/.harness-state.json`). Add / update / remove / no-op, idempotent,
  collision skip-or-overwrite.
- **`sync-harness` skill + `/sync-harness` shim** — preview the reconcile,
  resolve conflicts, confirm, apply.
- **Multi-profile selection** in the manifest: `profiles` is a non-empty unique
  list of profile names or the wildcard `"*"` (all). The reconcile vends only the
  selected `profiles/*.yaml` overlays; changing the selection is an update that
  prunes deselected overlays; selecting a profile absent from source is an error.
- **Automated release versioning** — semantic-release drives version bump,
  CHANGELOG, `vX.Y.Z` tag, and GitHub Release from conventional commits on `main`;
  `.claude-plugin/plugin.json` `version` is the canonical source of truth.
- Project hygiene: `README`, `LICENSE` (MIT), `CONTRIBUTING`, CI (pytest).

### Changed
- **Create-flow finalize rewired to the vendoring model.** `microskill-create`
  and `workflow-create` now vendor the approved component into
  `harness/<kind-dir>/<name>/`, upsert `harness/harness.yaml` (`source: custom`),
  and run `harness-sync --apply` — which owns the generated `.claude/` copy, the
  slash shim, and the ledger — instead of writing into `.claude/` directly. The
  workflow path then runs `compile-workflow`. Inputs `output_dir`/`commands_dir`
  were replaced by `harness_root`/`harness_yaml`.
- **Manifest schema** hard-replaced the single `profile` string with `profiles`
  (array | `"*"`); the legacy `profile` field is now rejected.

[0.1.0]: https://github.com/sharmankitdev/microskills/releases/tag/v0.1.0
