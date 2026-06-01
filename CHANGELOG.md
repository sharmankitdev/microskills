# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project will adopt
[Semantic Versioning](https://semver.org/) at its first tagged release.

## [Unreleased]

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

[Unreleased]: https://github.com/sharmankitdev/microskills/commits/main
