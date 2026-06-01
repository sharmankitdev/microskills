# Plugin Release Versioning — Design

**Date:** 2026-06-02
**Status:** Approved (design); pending implementation plan
**Scope:** Whole-plugin release versioning. Per-component versions and harness
manifest/schema migration are explicitly out of scope.

## Goal

Stand up a fully automated release process for the `microskills` Claude Code
plugin: conventional-commit messages drive version bump + CHANGELOG + git tag +
GitHub Release, with zero manual version edits. The project stays in the `0.x`
range until a deliberate manual flip to `1.0.0`.

## Source of truth

- Canonical version: `.claude-plugin/plugin.json` `version`.
- `semantic-release` computes the next version from git tags + commit history
  and writes it back into `plugin.json`.
- `.claude-plugin/marketplace.json` stays version-less (it points at `./`).
- The harness manifest's `version: 2` (`harness/harness.yaml`) is a **separate
  internal schema-contract version** and is deliberately NOT conflated with the
  plugin release version. The two are independent.

## Driver

`semantic-release` running in GitHub Actions on push to `main`. Conventional
commit messages are the input; version bump, CHANGELOG update, git tag, and
GitHub Release are the outputs.

## Files added / changed

| File | Action |
|---|---|
| `v0.1.0` git tag | **Seed** at the current HEAD so semantic-release continues from 0.1.0 (prevents the default 1.0.0 first release) |
| `CHANGELOG.md` | Reconcile: move the current `[Unreleased]` block to `## [0.1.0] - 2026-06-02` (that work *is* 0.1.0). semantic-release owns it from then on |
| `.releaserc.json` | semantic-release config (plugin chain below) |
| `scripts/set_plugin_version.py` | Writer: stamps `nextRelease.version` into `plugin.json` |
| `package.json` + `package-lock.json` | Private (`"private": true`), no publish — pins semantic-release + plugins as dev-only tooling |
| `.github/workflows/release.yml` | On push to `main`: run pytest, then `npx semantic-release` |
| `.github/workflows/pr-title.yml` | Lint PR title as a conventional commit; run `semantic-release --dry-run` to preview the next version on PRs |
| `CONTRIBUTING.md` | Document commit convention, scopes, 0.x bump policy, and that releases are now automatic |
| `.gitignore` | Add `node_modules/` |

## semantic-release plugin chain (`.releaserc.json`)

Ordered:

1. `@semantic-release/commit-analyzer` — `conventionalcommits` preset with **0.x
   releaseRules**:
   - `BREAKING CHANGE` / `!` → `minor`
   - `feat` → `minor`
   - `fix`, `perf`, `refactor`, `revert` → `patch`
   - Nothing maps to `major` → the version stays in `0.x` until a manual flip.
2. `@semantic-release/release-notes-generator` — `conventionalcommits` preset.
   Notes still group Features / Bug Fixes / **BREAKING CHANGES** separately even
   though `feat` and breaking share the same `minor` bump in `0.x`, so the
   signal is preserved in the changelog text.
3. `@semantic-release/changelog` — prepend release notes to `CHANGELOG.md`.
4. `@semantic-release/exec` — `prepareCmd: "python scripts/set_plugin_version.py ${nextRelease.version}"`.
5. `@semantic-release/git` — commit `plugin.json` + `CHANGELOG.md` as
   `chore(release): ${nextRelease.version} [skip ci]`.
6. `@semantic-release/github` — create the `vX.Y.Z` GitHub Release with the notes.

`branches: ["main"]`.

## `scripts/set_plugin_version.py`

- Single argument: the new semver string.
- Reads `.claude-plugin/plugin.json`, sets `version`, writes it back preserving
  key order and 2-space indentation + trailing newline.
- Exits non-zero on a missing/malformed file or a non-semver argument (fails the
  release rather than writing a bad version).

## Data flow

```
PR opened
  → pr-title.yml: conventional-title check + semantic-release --dry-run (shows next version)
  → squash-merge to main
    → release.yml:
        pytest passes
        → semantic-release analyzes commits since the last tag
          → computes next 0.x version
            → writes plugin.json + CHANGELOG.md
              → commit "chore(release): x.y.z [skip ci]"
                → tag vX.Y.Z
                  → GitHub Release
```

## 0.x bump policy (documented in CONTRIBUTING)

| Commit | Bump | Meaning in this repo |
|---|---|---|
| `fix:` | patch | bug fix in a script / dispatcher / microskill body, no contract change |
| `feat:` | minor | new microskill / workflow / agent / script capability (backward-compatible) |
| `feat!:` / `BREAKING CHANGE:` | minor (0.x) | harness schema change, dispatcher runtime-contract change, component rename/removal, resolve/sync behavior change |

Scopes: `harness`, `microskill`, `workflow`, `scripts`, `ci`.

`1.0.0` is a deliberate manual bump when the harness contract is considered
frozen — not produced automatically.

## Error handling / guardrails

- Only `chore` / `docs` / `ci` commits since the last tag → semantic-release
  **no-ops**; no release is cut.
- `[skip ci]` on the release commit prevents an infinite release loop.
- semantic-release owns both the git tag and `plugin.json`, so the tag and the
  in-file version cannot drift apart.
- `release.yml` needs `permissions: contents: write` and the default
  `GITHUB_TOKEN`. If `main` is branch-protected, the release bot must be allowed
  to push the `chore(release)` commit; otherwise use a PAT secret. (Called out
  here so the implementation plan handles it explicitly.)

## Testing

- pytest (existing suite) gates the release job — semantic-release runs only
  after tests pass.
- `semantic-release --dry-run` on PRs verifies config validity and previews the
  next version without publishing.

## Decisions locked

1. **Merge strategy = squash** → enforce the conventional convention on the PR
   *title*. (If the repo later switches to rebase/merge-commit, swap to
   commitlint over every commit.)
2. **Writer script = Python** (repo-consistent) rather than a Node one-liner.
3. **semantic-release owns `CHANGELOG.md`** going forward, rather than keeping it
   hand-curated.

## Out of scope

- Per-component (microskill / workflow) versioning.
- Harness manifest schema migration / `.harness-state` versioning.
- Publishing anywhere other than GitHub Releases (no npm / marketplace publish
  step).
