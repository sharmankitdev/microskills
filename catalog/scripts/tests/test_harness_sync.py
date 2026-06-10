"""
Tests for harness-sync (v2: custom-only). Run: python3 -m pytest catalog/scripts/tests/ -v

Hermetic fixtures build a tmp_path world (<tmp>/harness/, <tmp>/harness.yaml,
<tmp>/.claude/) and pass all four roots as flags, so nothing touches the real repo.
harness-sync reconciles ONLY source:custom components; source:plugin is owned by
initialize-harness and is skipped here, its ledger entries preserved untouched.
"""
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "harness-sync"


def load_harness_sync():
    loader = importlib.machinery.SourceFileLoader("harness_sync", str(SCRIPT))
    spec = importlib.util.spec_from_loader("harness_sync", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(tmp, *args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--harness-root", str(tmp / "harness"),
         "--harness-yaml", str(tmp / "harness" / "harness.yaml"),
         "--state", str(tmp / ".claude" / ".harness-state.json"),
         "--deploy-root", str(tmp / ".claude"),
         *args],
        capture_output=True, text=True, cwd=str(REPO))
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def write_manifest(tmp, components):
    """components: list of {name, kind?, source?, profiles?, version?} dicts.
    kind (microskill|workflow) routes into the v2 microskills/workflows lists;
    profiles is a list of names or "*"; omit to leave it unset (all)."""
    by_list = {"microskills": [], "workflows": []}
    for c in components:
        by_list["workflows" if c.get("kind") == "workflow" else "microskills"].append(c)
    lines = ["version: 2"]
    for list_key in ("microskills", "workflows"):
        items = by_list[list_key]
        if not items:
            continue
        lines.append(f"{list_key}:")
        for c in items:
            lines.append(f"  - name: {c['name']}")
            lines.append(f"    source: {c.get('source', 'custom')}")
            prof = c.get("profiles")
            if prof is not None:
                lines.append('    profiles: "*"' if prof == "*"
                             else f"    profiles: [{', '.join(prof)}]")
            if c.get("version"):
                lines.append(f"    version: {c['version']}")
    (tmp / "harness").mkdir(parents=True, exist_ok=True)
    (tmp / "harness" / "harness.yaml").write_text("\n".join(lines) + "\n")


def make_microskill(tmp, name, desc="A demo component.", body="Hello.", profiles=("base",)):
    d = tmp / "harness" / "microskills" / name
    (d / "profiles").mkdir(parents=True, exist_ok=True)
    (d / "MICROSKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n{body}\n")
    for p in profiles:
        (d / "profiles" / f"{p}.yaml").write_text("version: 1\n")
    return d


def state_of(tmp):
    return json.loads((tmp / ".claude" / ".harness-state.json").read_text())


def test_plan_shows_add_and_writes_nothing(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 0, err
    assert data["mode"] == "plan"
    assert [a["action"] for a in data["actions"]] == ["add"]
    assert not (tmp_path / ".claude" / ".harness-state.json").exists()


def test_apply_installs_and_records(tmp_path):
    make_microskill(tmp_path, "greet-user", desc="Greet someone.")
    write_manifest(tmp_path, [{"name": "greet-user", "profiles": ["base"]}])
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert data["mode"] == "apply"
    ms = tmp_path / ".claude" / "microskills" / "greet-user"
    assert (ms / "MICROSKILL.md").exists()
    assert (ms / "profiles" / "base.yaml").exists()
    assert (tmp_path / ".claude" / "commands" / "greet-user.md").exists()
    st = state_of(tmp_path)["components"]["greet-user"]
    assert st["kind"] == "microskill"
    assert st["source"] == "custom"
    assert st["profiles"] == ["base"]
    assert st["source_hash"].startswith("sha256:")
    assert set(st["installed_paths"]) == {
        ".claude/microskills/greet-user/MICROSKILL.md",
        ".claude/microskills/greet-user/profiles/base.yaml",
        ".claude/commands/greet-user.md",
    }


def test_second_apply_is_noop(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    run(tmp_path, "--apply")
    state1 = (tmp_path / ".claude" / ".harness-state.json").read_text()
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert [a["action"] for a in data["actions"]] == ["noop"]
    state2 = (tmp_path / ".claude" / ".harness-state.json").read_text()
    assert state1 == state2


def test_edit_source_updates(tmp_path):
    d = make_microskill(tmp_path, "greet-user", body="Hello.")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    run(tmp_path, "--apply")
    h1 = state_of(tmp_path)["components"]["greet-user"]["source_hash"]
    (d / "MICROSKILL.md").write_text(
        "---\nname: greet-user\ndescription: A demo component.\n---\n\n# greet-user\n\nGoodbye.\n")
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 0, err
    upd = [a for a in data["actions"] if a["action"] == "update"]
    assert len(upd) == 1 and upd[0]["old_hash"] != upd[0]["new_hash"]
    run(tmp_path, "--apply")
    deployed = (tmp_path / ".claude" / "microskills" / "greet-user" / "MICROSKILL.md").read_text()
    assert "Goodbye." in deployed
    assert state_of(tmp_path)["components"]["greet-user"]["source_hash"] != h1


def test_delist_removes_and_prunes(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    run(tmp_path, "--apply")
    write_manifest(tmp_path, [])
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert [a["action"] for a in data["actions"]] == ["remove"]
    assert not (tmp_path / ".claude" / "microskills" / "greet-user").exists()
    assert not (tmp_path / ".claude" / "commands" / "greet-user.md").exists()
    assert "greet-user" not in state_of(tmp_path)["components"]
    assert (tmp_path / ".claude" / "microskills").exists()   # shared engine dir preserved


def test_scoped_ownership_untouched(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    foreign_ms = tmp_path / ".claude" / "microskills" / "preexisting"
    foreign_ms.mkdir(parents=True)
    (foreign_ms / "MICROSKILL.md").write_text("FOREIGN")
    foreign_cmd = tmp_path / ".claude" / "commands" / "other.md"
    foreign_cmd.parent.mkdir(parents=True, exist_ok=True)
    foreign_cmd.write_text("FOREIGN CMD")
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert (foreign_ms / "MICROSKILL.md").read_text() == "FOREIGN"
    assert foreign_cmd.read_text() == "FOREIGN CMD"
    paths = state_of(tmp_path)["components"]["greet-user"]["installed_paths"]
    assert all("preexisting" not in p and "other.md" not in p for p in paths)


def test_collision_skip_then_overwrite(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    clash = tmp_path / ".claude" / "microskills" / "greet-user" / "MICROSKILL.md"
    clash.parent.mkdir(parents=True)
    clash.write_text("SENTINEL")
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 1
    assert data["conflicts"] and data["conflicts"][0]["name"] == "greet-user"
    assert not [a for a in data["actions"] if a["action"] == "add"]
    assert clash.read_text() == "SENTINEL"
    assert "greet-user" not in state_of(tmp_path).get("components", {})
    rc, data, out, err = run(tmp_path, "--apply", "--resolve", "greet-user=overwrite")
    assert rc == 0, err
    assert clash.read_text() != "SENTINEL"
    assert "greet-user" in state_of(tmp_path)["components"]


def test_state_file_atomic(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    run(tmp_path, "--apply")
    state_dir = tmp_path / ".claude"
    assert not list(state_dir.glob("*.tmp"))
    txt = (state_dir / ".harness-state.json").read_text()
    json.loads(txt)
    assert txt.endswith("\n")


def test_tree_hash_stable_sensitive_location_independent(tmp_path):
    hs = load_harness_sync()
    a = tmp_path / "a"
    (a / "sub").mkdir(parents=True)
    (a / "f.txt").write_text("x")
    (a / "sub" / "g.txt").write_text("y")
    h1 = hs.tree_hash(a)
    assert h1 == hs.tree_hash(a)                       # stable
    b = tmp_path / "b"
    shutil.copytree(a, b)
    assert hs.tree_hash(b) == h1                       # location-independent
    (b / "f.txt").write_text("z")
    assert hs.tree_hash(b) != h1                       # byte-sensitive
    c = tmp_path / "c"
    shutil.copytree(a, c)
    (c / "f.txt").rename(c / "renamed.txt")
    assert hs.tree_hash(c) != h1                       # path-sensitive


def test_vendor_skip_ignores_compiled_and_pycache(tmp_path):
    hs = load_harness_sync()
    assert hs.vendor_skip(Path(".compiled/manifest.json"))
    assert hs.vendor_skip(Path("__pycache__/x.pyc"))
    assert hs.vendor_skip(Path("foo.pyc"))
    assert not hs.vendor_skip(Path("MICROSKILL.md"))
    assert not hs.vendor_skip(Path("profiles/base.yaml"))


def test_plugin_source_is_skipped(tmp_path):
    # source:plugin is owned by initialize-harness — sync skips it (no action, no error).
    make_microskill(tmp_path, "fromplugin")
    write_manifest(tmp_path, [{"name": "fromplugin", "source": "plugin"}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 0, err
    assert data["actions"] == []
    assert data["errors"] == []


def test_unknown_source_schema_rejected(tmp_path):
    # 'bundle' is no longer a valid source in v2 — the schema rejects it.
    make_microskill(tmp_path, "fromhub")
    write_manifest(tmp_path, [{"name": "fromhub", "source": "bundle"}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert "schema_errors" in data


def test_plugin_ledger_entry_and_engine_preserved(tmp_path):
    # A plugin-owned component + the engine block (written by initialize-harness) must
    # survive a custom sync untouched — sync never reconciles them.
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    run(tmp_path, "--apply")
    sp = tmp_path / ".claude" / ".harness-state.json"
    st = json.loads(sp.read_text())
    st["components"]["task-plan"] = {
        "kind": "microskill", "source": "plugin", "profiles": "*",
        "deploy_path": ".claude/microskills/task-plan",
        "installed_paths": [".claude/microskills/task-plan/MICROSKILL.md"],
        "source_hash": "sha256:dead"}
    st["engine"] = {"installed_paths": [".claude/scripts/resolve-microskill"],
                    "source_hash": "sha256:cafe"}
    sp.write_text(json.dumps(st))
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert [a["action"] for a in data["actions"]] == ["noop"]   # only greet-user (custom)
    st2 = state_of(tmp_path)
    assert st2["components"]["task-plan"]["source"] == "plugin"   # preserved
    assert st2["engine"]["source_hash"] == "sha256:cafe"          # preserved


def test_missing_harness_yaml_exits_2(tmp_path):
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 2
    assert "error" in data


def test_malformed_resolve_exits_1(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user"}])
    rc, data, out, err = run(tmp_path, "--apply", "--resolve", "greet-user=bogus")
    assert rc == 1
    assert "error" in data


def test_partial_apply_commits_clean_component(tmp_path):
    make_microskill(tmp_path, "clean")
    make_microskill(tmp_path, "clashing")
    write_manifest(tmp_path, [{"name": "clean"}, {"name": "clashing"}])
    clash = tmp_path / ".claude" / "microskills" / "clashing" / "MICROSKILL.md"
    clash.parent.mkdir(parents=True)
    clash.write_text("SENTINEL")
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 1
    comps = state_of(tmp_path)["components"]
    assert "clean" in comps
    assert "clashing" not in comps
    assert (tmp_path / ".claude" / "microskills" / "clean" / "MICROSKILL.md").exists()
    assert clash.read_text() == "SENTINEL"


# --- profiles selection (multi-profile + wildcard) ---

def test_profiles_wildcard_vends_all(tmp_path):
    make_microskill(tmp_path, "multi", profiles=("base", "autonomous"))
    write_manifest(tmp_path, [{"name": "multi", "profiles": "*"}])
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    pdir = tmp_path / ".claude" / "microskills" / "multi" / "profiles"
    assert (pdir / "base.yaml").exists() and (pdir / "autonomous.yaml").exists()
    assert state_of(tmp_path)["components"]["multi"]["profiles"] == "*"


def test_profiles_subset_vends_only_selected(tmp_path):
    make_microskill(tmp_path, "multi", profiles=("base", "autonomous"))
    write_manifest(tmp_path, [{"name": "multi", "profiles": ["base"]}])
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    pdir = tmp_path / ".claude" / "microskills" / "multi" / "profiles"
    assert (pdir / "base.yaml").exists()
    assert not (pdir / "autonomous.yaml").exists()
    paths = state_of(tmp_path)["components"]["multi"]["installed_paths"]
    assert not any("autonomous.yaml" in p for p in paths)
    assert state_of(tmp_path)["components"]["multi"]["profiles"] == ["base"]


def test_profiles_omitted_defaults_to_all(tmp_path):
    make_microskill(tmp_path, "multi", profiles=("base", "autonomous"))
    write_manifest(tmp_path, [{"name": "multi"}])   # no profiles key
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    pdir = tmp_path / ".claude" / "microskills" / "multi" / "profiles"
    assert (pdir / "base.yaml").exists() and (pdir / "autonomous.yaml").exists()
    assert state_of(tmp_path)["components"]["multi"]["profiles"] == "*"


def test_profiles_selection_change_is_update_and_prunes(tmp_path):
    make_microskill(tmp_path, "multi", profiles=("base", "autonomous"))
    write_manifest(tmp_path, [{"name": "multi", "profiles": ["base"]}])
    run(tmp_path, "--apply")
    pdir = tmp_path / ".claude" / "microskills" / "multi" / "profiles"
    # widen selection -> update, autonomous now vended
    write_manifest(tmp_path, [{"name": "multi", "profiles": ["base", "autonomous"]}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 0, err
    assert [a["action"] for a in data["actions"]] == ["update"]
    run(tmp_path, "--apply")
    assert (pdir / "autonomous.yaml").exists()
    assert state_of(tmp_path)["components"]["multi"]["profiles"] == ["autonomous", "base"]
    # narrow back -> update, autonomous pruned
    write_manifest(tmp_path, [{"name": "multi", "profiles": ["base"]}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert [a["action"] for a in data["actions"]] == ["update"]
    run(tmp_path, "--apply")
    assert not (pdir / "autonomous.yaml").exists()


def test_selected_profile_missing_in_source_is_error(tmp_path):
    make_microskill(tmp_path, "multi", profiles=("base",))
    write_manifest(tmp_path, [{"name": "multi", "profiles": ["base", "autonomous"]}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert data["errors"] and data["errors"][0]["name"] == "multi"
    assert "autonomous" in data["errors"][0]["reason"]
    assert not data["actions"]


def test_schema_rejects_empty_profiles(tmp_path):
    make_microskill(tmp_path, "greet-user")
    write_manifest(tmp_path, [{"name": "greet-user", "profiles": []}])
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert "schema_errors" in data


def test_schema_rejects_bad_wildcard(tmp_path):
    make_microskill(tmp_path, "greet-user")
    (tmp_path / "harness" / "harness.yaml").write_text(
        "version: 2\nmicroskills:\n  - name: greet-user\n    source: custom\n    profiles: all\n")
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert "schema_errors" in data


def test_schema_rejects_unknown_component_field(tmp_path):
    make_microskill(tmp_path, "greet-user")
    (tmp_path / "harness" / "harness.yaml").write_text(
        "version: 2\nmicroskills:\n  - name: greet-user\n    source: custom\n    profile: base\n")
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert "schema_errors" in data


def test_schema_rejects_v1_components_shape(tmp_path):
    make_microskill(tmp_path, "greet-user")
    (tmp_path / "harness" / "harness.yaml").write_text(
        "version: 1\ncomponents:\n  - name: greet-user\n    kind: microskill\n    source: custom\n")
    rc, data, out, err = run(tmp_path, "--plan")
    assert rc == 1
    assert "schema_errors" in data


def make_workflow(tmp, name, desc="A demo workflow.", prompt="do a"):
    d = tmp / "harness" / "workflow-defs" / name
    (d / "profiles").mkdir(parents=True, exist_ok=True)
    (d / "WORKFLOW.yaml").write_text(
        f"version: 1\nname: {name}\ndescription: {desc}\n"
        f"nodes:\n  - id: a\n    agent: ag\n    prompt: {prompt}\n")
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    return d


def test_update_preserves_compiled_runs_ledger(tmp_path):
    # .compiled/ is generated/transient (VENDOR_IGNORE_PARTS): never vendored, never
    # hashed, never in installed_paths — so the dispatcher's per-run ledgers under
    # .claude/workflow-defs/<name>/.compiled/runs/<run-id>/ survive a sync update
    # (the stale prune unlinks only previously-OWNED paths).
    d = make_workflow(tmp_path, "demo-flow")
    write_manifest(tmp_path, [{"name": "demo-flow", "kind": "workflow"}])
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    st = state_of(tmp_path)["components"]["demo-flow"]
    assert not any(".compiled" in p for p in st["installed_paths"])

    # Plant a per-run ledger exactly as run-journal lays it out.
    run_dir = (tmp_path / ".claude" / "workflow-defs" / "demo-flow"
               / ".compiled" / "runs" / "20260610T120000Z-abc123")
    (run_dir / "run-inputs").mkdir(parents=True)
    (run_dir / "run-config.json").write_text('{"v": 1, "run_id": "20260610T120000Z-abc123"}')
    (run_dir / "run-state.json").write_text('{"manifest_hash": "sha256:h", "step_index": 1, "results": {}}')
    (run_dir / "journal.jsonl").write_text('{"v":1,"event":"run_start"}\n')
    (run_dir / "run-inputs" / "x.cat").write_text("materialized")

    # Edit the source -> the next apply is an UPDATE (stale prune + re-vendor).
    make_workflow(tmp_path, "demo-flow", prompt="do a differently")
    rc, data, out, err = run(tmp_path, "--apply")
    assert rc == 0, err
    assert [a["action"] for a in data["actions"]] == ["update"]
    for f in ("run-config.json", "run-state.json", "journal.jsonl", "run-inputs/x.cat"):
        assert (run_dir / f).exists(), f"{f} clobbered by sync update"
    st = state_of(tmp_path)["components"]["demo-flow"]
    assert not any(".compiled" in p for p in st["installed_paths"])
