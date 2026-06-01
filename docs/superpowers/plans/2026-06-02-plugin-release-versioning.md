# Plugin Release Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up fully automated `0.x` release versioning for the `microskills` Claude Code plugin — conventional commits drive version bump + CHANGELOG + git tag + GitHub Release with zero manual version edits.

**Architecture:** `semantic-release` runs in GitHub Actions on push to `main`. It reads conventional-commit history since the last tag, computes the next `0.x` version, stamps it into `.claude-plugin/plugin.json` (canonical source of truth) via a small Python writer, updates `CHANGELOG.md`, tags `vX.Y.Z`, and cuts a GitHub Release. Release tooling lives in a root `scripts/` + Node dev-deps, kept separate from `catalog/` so it is never vendored into consumer runtimes.

**Tech Stack:** semantic-release 24 (+ commit-analyzer, release-notes-generator, changelog, exec, git, github plugins), `conventionalcommits` preset, Python 3 writer script, GitHub Actions, pytest.

---

## Spec

`docs/superpowers/specs/2026-06-02-plugin-release-versioning-design.md`

## Pre-existing issues fixed in passing

- **`ci.yml` test gate is broken.** It runs `pytest .claude/scripts/tests/`, but `.claude/` is gitignored and never rebuilt in CI — so the suite effectively tests nothing. Per `CONTRIBUTING.md`, the real path is `catalog/scripts/tests/`, and the `compile-workflow` e2e tests need `initialize-harness` run first. Task 5 fixes this because the release job depends on a working test gate.

## File map

| File | Responsibility |
|---|---|
| `CHANGELOG.md` | Reconcile `[Unreleased]` → `[0.1.0]`; semantic-release owns it afterward |
| `scripts/set_plugin_version.py` | Stamp a semver into `.claude-plugin/plugin.json` (called by semantic-release exec) |
| `scripts/tests/test_set_plugin_version.py` | Hermetic `tmp_path` tests for the writer |
| `.gitignore` | Ignore `node_modules/` |
| `package.json` + `package-lock.json` | Private, no-publish dev deps pinning semantic-release + plugins |
| `.releaserc.json` | semantic-release plugin chain + 0.x release rules |
| `.github/workflows/ci.yml` | Fix test path + add `initialize-harness` + run `scripts/tests/` |
| `.github/workflows/release.yml` | Push-to-main: test, then `npx semantic-release` |
| `.github/workflows/pr-title.yml` | Enforce conventional PR title |
| `CONTRIBUTING.md` | Conventional-commit convention + automated-release / 0.x policy |
| `v0.1.0` git tag | Seed baseline so the first auto release is the next feature, not 1.0.0 |

---

### Task 1: Reconcile CHANGELOG to a finalized 0.1.0

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite `CHANGELOG.md`**

Replace the whole file with this. The previous `[Unreleased]` content becomes the finalized `[0.1.0]`; a bullet for the new release automation is added under Added; the intro is updated to say SemVer/semantic-release are now in effect.

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): finalize 0.1.0 baseline"
```

---

### Task 2: Version writer script (TDD)

The writer is called by semantic-release as `python scripts/set_plugin_version.py <version>`. It must update only the `version` key, preserve key order and the em-dash characters in `description`, and fail loudly on a bad version or missing file.

**Files:**
- Create: `scripts/set_plugin_version.py`
- Test: `scripts/tests/test_set_plugin_version.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_set_plugin_version.py`:

```python
"""Hermetic tests for the plugin version writer. No real repo files touched."""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "set_plugin_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("set_plugin_version", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample(tmp_path: Path) -> Path:
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({
        "name": "microskills",
        "description": "engine — bootstraps a project — dormant data",
        "version": "0.1.0",
        "author": {"name": "Ankit Sharma"},
    }, indent=2) + "\n")
    return p


def test_updates_version_and_preserves_other_keys(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("0.2.0", path=target)
    data = json.loads(target.read_text())
    assert data["version"] == "0.2.0"
    assert list(data.keys()) == ["name", "description", "version", "author"]
    assert data["author"] == {"name": "Ankit Sharma"}


def test_preserves_non_ascii_and_trailing_newline(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("0.2.0", path=target)
    text = target.read_text()
    assert "—" in text  # em-dash not escaped to —
    assert text.endswith("}\n")


def test_accepts_prerelease(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("1.0.0-rc.1", path=target)
    assert json.loads(target.read_text())["version"] == "1.0.0-rc.1"


@pytest.mark.parametrize("bad", ["v1.2.3", "1.2", "abc", "1.2.3.4", ""])
def test_rejects_non_semver(tmp_path, bad):
    mod = _load()
    target = _sample(tmp_path)
    with pytest.raises(ValueError):
        mod.set_version(bad, path=target)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_set_plugin_version.py -v`
Expected: FAIL — `set_plugin_version.py` does not exist (collection / import error).

- [ ] **Step 3: Write the script**

Create `scripts/set_plugin_version.py`:

```python
#!/usr/bin/env python3
"""Stamp a semver into .claude-plugin/plugin.json (the canonical plugin version).

Invoked by semantic-release (@semantic-release/exec prepareCmd) as:
    python scripts/set_plugin_version.py <version>
Fails non-zero on a bad version or unreadable file rather than writing garbage.
"""
import json
import re
import sys
from pathlib import Path

PLUGIN_JSON = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
SEMVER = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def set_version(version: str, path: Path = PLUGIN_JSON) -> None:
    if not SEMVER.match(version):
        raise ValueError(f"not a valid semver: {version!r}")
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv: list) -> int:
    if len(argv) != 2:
        print("usage: set_plugin_version.py <semver>", file=sys.stderr)
        return 2
    try:
        set_version(argv[1])
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_set_plugin_version.py -v`
Expected: PASS (6 tests: 3 named + 5 parametrized rejections + 2 others = all green).

- [ ] **Step 5: Smoke-test the CLI against the real manifest (no version change)**

Run: `python scripts/set_plugin_version.py 0.1.0 && git diff --stat .claude-plugin/plugin.json`
Expected: exit 0; `git diff --stat` shows no change (version already `0.1.0`). If it reports a formatting-only diff, run `git checkout .claude-plugin/plugin.json` to discard it before committing.

- [ ] **Step 6: Commit**

```bash
git add scripts/set_plugin_version.py scripts/tests/test_set_plugin_version.py
git commit -m "feat(scripts): add plugin version writer for semantic-release"
```

---

### Task 3: Node dev tooling (semantic-release dependencies)

**Files:**
- Modify: `.gitignore`
- Create: `package.json`
- Create: `package-lock.json` (generated by `npm install`)

- [ ] **Step 1: Ignore `node_modules/`**

Append to `.gitignore` (after the `.pytest_cache/` / `__pycache__/` block):

```gitignore

# Node dev tooling (release automation only; the plugin ships no JS)
node_modules/
```

- [ ] **Step 2: Create `package.json`**

```json
{
  "name": "microskills-release-tooling",
  "version": "0.0.0",
  "private": true,
  "description": "Dev-only release tooling for the microskills plugin. Not published.",
  "license": "MIT",
  "devDependencies": {
    "@semantic-release/changelog": "^6.0.3",
    "@semantic-release/commit-analyzer": "^13.0.0",
    "@semantic-release/exec": "^7.0.3",
    "@semantic-release/git": "^10.0.1",
    "@semantic-release/github": "^11.0.0",
    "@semantic-release/release-notes-generator": "^14.0.0",
    "conventional-changelog-conventionalcommits": "^8.0.0",
    "semantic-release": "^24.2.0"
  }
}
```

- [ ] **Step 3: Generate the lockfile (requires network)**

Run: `npm install`
Expected: creates `package-lock.json` and `node_modules/` (the latter is gitignored). No high-severity audit failure should block; warnings are fine.

- [ ] **Step 4: Commit**

```bash
git add .gitignore package.json package-lock.json
git commit -m "build: add semantic-release dev tooling"
```

---

### Task 4: semantic-release config

**Files:**
- Create: `.releaserc.json`

- [ ] **Step 1: Create `.releaserc.json`**

`commit-analyzer` rules force `0.x` semantics: breaking → minor (never major), feat → minor, the rest → patch. The git commit re-commits the bumped manifest + changelog with `[skip ci]` to avoid a release loop.

```json
{
  "branches": ["main"],
  "plugins": [
    [
      "@semantic-release/commit-analyzer",
      {
        "preset": "conventionalcommits",
        "releaseRules": [
          { "breaking": true, "release": "minor" },
          { "type": "feat", "release": "minor" },
          { "type": "fix", "release": "patch" },
          { "type": "perf", "release": "patch" },
          { "type": "refactor", "release": "patch" },
          { "type": "revert", "release": "patch" }
        ]
      }
    ],
    [
      "@semantic-release/release-notes-generator",
      { "preset": "conventionalcommits" }
    ],
    [
      "@semantic-release/changelog",
      { "changelogFile": "CHANGELOG.md" }
    ],
    [
      "@semantic-release/exec",
      { "prepareCmd": "python scripts/set_plugin_version.py ${nextRelease.version}" }
    ],
    [
      "@semantic-release/git",
      {
        "assets": [".claude-plugin/plugin.json", "CHANGELOG.md"],
        "message": "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}"
      }
    ],
    "@semantic-release/github"
  ]
}
```

- [ ] **Step 2: Validate config loads (off-main dry run)**

Run: `npx semantic-release --dry-run --no-ci`
Expected: exits 0. On a non-`main` branch it prints `This test run was triggered on the branch <x>, while semantic-release is configured to only publish from main` and skips — that confirms the config parses. (A clean parse is the goal here, not a computed version.)

- [ ] **Step 3: Commit**

```bash
git add .releaserc.json
git commit -m "ci: add semantic-release configuration"
```

---

### Task 5: Fix CI test gate

The release job (Task 6) gates on tests. The current `ci.yml` tests a gitignored, never-built path. Point it at the canonical `catalog/scripts/tests/`, rebuild the runtime first (the `compile-workflow` e2e tests need `.claude/`), and add the new `scripts/tests/`.

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: python -m pip install --upgrade pip pyyaml jsonschema pytest
      - name: Rebuild runtime from catalog
        run: catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
      - name: Run tests
        run: python -m pytest catalog/scripts/tests/ scripts/tests/ -v
```

- [ ] **Step 2: Reproduce the CI test run locally**

Run:
```bash
catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
python -m pytest catalog/scripts/tests/ scripts/tests/ -v
```
Expected: all tests PASS, including `scripts/tests/test_set_plugin_version.py`. If `initialize-harness` is not executable, run it as `python3 catalog/scripts/initialize-harness ...` and note that form, but prefer the documented direct invocation.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: fix test gate to use catalog path and run scripts tests"
```

---

### Task 6: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create `.github/workflows/release.yml`**

`fetch-depth: 0` lets semantic-release read full history/tags. The release runs only after the same test gate passes.

```yaml
name: Release

on:
  push:
    branches: [main]

permissions:
  contents: write
  issues: write
  pull-requests: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: true
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install Python deps
        run: python -m pip install --upgrade pip pyyaml jsonschema pytest
      - name: Rebuild runtime from catalog
        run: catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog
      - name: Run tests
        run: python -m pytest catalog/scripts/tests/ scripts/tests/ -v
      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Install release tooling
        run: npm ci
      - name: Release
        run: npx semantic-release
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

> **Branch-protection note:** `@semantic-release/git` pushes the `chore(release)` commit back to `main`. If `main` is a protected branch, allow the GitHub Actions bot to bypass the protection, or replace `GITHUB_TOKEN` with a `secrets.RELEASE_TOKEN` PAT that can push. With no branch protection (current state) the default `GITHUB_TOKEN` + `contents: write` is sufficient.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add semantic-release release workflow"
```

---

### Task 7: Conventional PR-title check

PRs are squash-merged, so the squash commit = the PR title. This gate keeps that title a valid conventional commit so release bumps stay correct. (A true next-version preview can't run off `main`, so this check is title-validity only.)

**Files:**
- Create: `.github/workflows/pr-title.yml`

- [ ] **Step 1: Create `.github/workflows/pr-title.yml`**

```yaml
name: PR Title

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions:
  pull-requests: read

jobs:
  conventional-title:
    runs-on: ubuntu-latest
    steps:
      - uses: amannn/action-semantic-pull-request@v5
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          types: |
            feat
            fix
            perf
            refactor
            revert
            docs
            chore
            ci
            test
            build
          requireScope: false
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/pr-title.yml
git commit -m "ci: enforce conventional PR titles"
```

---

### Task 8: Document the convention and release policy

**Files:**
- Modify: `CONTRIBUTING.md:82-86`

- [ ] **Step 1: Replace the `## Commits` section (lines 82-86) with**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: document conventional commits and automated releases"
```

---

### Task 9: Seed the v0.1.0 baseline tag

Tag `v0.1.0` at the final commit so everything up to and including the versioning setup IS `0.1.0`; the first automated release then fires on the next `feat`/`fix` merged to `main`.

**Files:** none (git tag + push only)

- [ ] **Step 1: Confirm the working tree is clean and on `main` with all tasks committed**

Run: `git status --short && git log --oneline -8`
Expected: clean tree; commits from Tasks 1–8 present.

- [ ] **Step 2: Create the annotated tag at HEAD**

```bash
git tag -a v0.1.0 -m "v0.1.0 — first tagged baseline"
```

- [ ] **Step 3: Verify semantic-release sees the baseline and finds nothing to release yet**

Run: `npx semantic-release --dry-run --no-ci --branches main`
Expected: with no releasable commits after `v0.1.0`, it reports `There are no relevant changes, so no new version is released.` (Off-branch skip messaging is also acceptable — the point is no crash and the `v0.1.0` tag is recognized.)

- [ ] **Step 4: Push branch and tag**

```bash
git push origin main
git push origin v0.1.0
```
Expected: `release.yml` runs on the push, tests pass, semantic-release no-ops (no commits after `v0.1.0`). The automation is now armed.

- [ ] **Step 5: (Post-merge, manual verification) confirm the first real release**

After the next `feat:`/`fix:` PR is squash-merged to `main`, confirm `release.yml` produced: a bumped `.claude-plugin/plugin.json`, an updated `CHANGELOG.md`, a new `vX.Y.Z` tag, and a GitHub Release. No action if so.

---

## Self-review

- **Spec coverage:** source of truth (Task 2/4) ✓; semantic-release driver + plugin chain (Task 4) ✓; seed `v0.1.0` (Task 9) ✓; CHANGELOG reconcile (Task 1) ✓; writer script (Task 2) ✓; package.json/lock (Task 3) ✓; release.yml (Task 6) ✓; PR-title check (Task 7) ✓; CONTRIBUTING policy (Task 8) ✓; .gitignore node_modules (Task 3) ✓; 0.x release rules (Task 4) ✓; token/branch-protection guardrail (Task 6 note) ✓; harness schema-version decoupling (Task 8 note) ✓.
- **Deviation from spec:** spec listed a PR `--dry-run` next-version preview; semantic-release cannot compute a version off `main`, so Task 7 enforces title validity only (preview dropped, noted). CI test-gate fix (Task 5) is an added in-scope repair the spec did not mention.
- **Naming consistency:** `set_version(version, path=…)` defined in Task 2 and called identically in tests; `scripts/set_plugin_version.py` referenced consistently in Task 2, `.releaserc.json` (Task 4), and CONTRIBUTING (Task 8). `catalog/scripts/tests/ scripts/tests/` test command identical in Tasks 5 and 6.
