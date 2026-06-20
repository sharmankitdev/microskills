"""
Tests for initialize-harness materialization + runtime schema resolution.
Run: python3 -m pytest catalog/scripts/tests/ -v

These guard the clean-install bug: the runtime scripts resolve their JSON schemas from
templates/, which init must materialize into .claude/templates/ so the materialized scripts
find them with NO CLAUDE_PLUGIN_ROOT in the environment (segment agents run them by relative
path). We init into a tmp project (CLAUDE_PLUGIN_ROOT=REPO, as the dispatcher does), then run
the MATERIALIZED scripts with the env var unset — exactly the failing case from the live test.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "catalog" / "scripts" / "initialize-harness"
SYNC = REPO / "catalog" / "scripts" / "harness-sync"

SCHEMAS = ["config-schema.json", "workflow-schema.json", "harness-schema.json"]


def init_project(tmp):
    """Run initialize-harness --apply into tmp (catalog from CLAUDE_PLUGIN_ROOT, as installed)."""
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
    proc = subprocess.run(
        [sys.executable, str(INIT), "--apply", "--project-root", str(tmp)],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def run_materialized(tmp, script, *args):
    """Run a script from tmp/.claude/scripts with NO CLAUDE_PLUGIN_ROOT (the segment-agent env)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    proc = subprocess.run(
        [sys.executable, str(tmp / ".claude" / "scripts" / script), *args],
        capture_output=True, text=True, cwd=str(tmp), env=env)
    return proc.returncode, proc.stdout, proc.stderr


def test_init_materializes_templates(tmp_path):
    init_project(tmp_path)
    refs = tmp_path / ".claude" / "templates" / "references"
    for name in SCHEMAS:
        assert (refs / name).exists(), f"{name} not materialized into .claude/templates/references/"
    # the engine ledger tracks the materialized template files
    state = json.loads((tmp_path / ".claude" / ".harness-state.json").read_text())
    eng = state["engine"]["installed_paths"]
    assert ".claude/templates/references/config-schema.json" in eng
    assert ".claude/templates/references/workflow-schema.json" in eng


def test_validate_microskill_resolves_schema_without_plugin_root(tmp_path):
    """The exact failing case: materialized validate-microskill, CLAUDE_PLUGIN_ROOT unset."""
    init_project(tmp_path)
    ms = tmp_path / ".claude" / "microskills" / "task-plan"
    rc, out, err = run_materialized(
        tmp_path, "validate-microskill",
        str(ms / "MICROSKILL.md"), str(ms / "profiles" / "base.yaml"))
    assert rc == 0, err
    data = json.loads(out)
    assert data["pass"] is True, data


def test_compile_workflow_hard_blocks_on_missing_schema(tmp_path):
    """Removing the materialized schema makes compile-workflow fail loudly (not silently skip)."""
    init_project(tmp_path)
    (tmp_path / ".claude" / "templates" / "references" / "workflow-schema.json").unlink()
    rc, out, err = run_materialized(tmp_path, "compile-workflow", "microskill-create")
    assert rc != 0, "compile-workflow should fail when its schema is absent"
    assert "not found" in (out + err).lower()


def test_init_is_idempotent(tmp_path):
    init_project(tmp_path)
    second = init_project(tmp_path)
    assert second["engine"]["action"] == "noop"
    assert second["summary"]["add"] == 0
    assert second["summary"]["update"] == 0


# --- base-offering drift detection + gated adopt --------------------------------------------

# A manifest that PREDATES two base components being tagged base in the catalog — exactly the
# reported bug. Header comment + a source:custom entry must survive an adopt rewrite.
PARTIAL_MANIFEST = """\
# Harness manifest (v2) — the components this project uses.
# source: plugin → materialized by initialize-harness; source: custom → reconciled by harness-sync.
version: 2
microskills:
  - name: task-plan
    source: plugin
  - name: task-implement
    source: plugin
  - name: task-evaluate
    source: plugin
  - name: validate-microskill
    source: plugin
  - name: greet-user
    source: custom
    profiles: [base]
workflows:
  - name: microskill-create
    source: plugin
  - name: workflow-create
    source: plugin
"""

# Base-tagged catalog components absent from PARTIAL_MANIFEST. The §8-step-7 RVS
# rewire made build-workflow-from-plan import (and base-tag, for import-closure) the
# review/verify/synth + floor + grounding + bundling microskills, so the flagship
# base set a fresh consumer seeds now includes them.
MISSING_BASE = {"analyze-monolith-orchestrator", "decompose-monolith-orchestrator",
                "build-workflow-from-plan", "run-validators", "build-catalog-index",
                "review-dimension", "collect-findings", "verify-finding",
                "synthesize-review", "bundle-draft"}


def write_manifest(tmp, text):
    hy = tmp / "harness" / "harness.yaml"
    hy.parent.mkdir(parents=True, exist_ok=True)
    hy.write_text(text)
    return hy


def run_init(tmp, *flags):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
    proc = subprocess.run(
        [sys.executable, str(INIT), *flags, "--project-root", str(tmp), "--catalog",
         str(REPO / "catalog")],
        capture_output=True, text=True, env=env)
    assert proc.returncode in (0, 1), proc.stderr
    return json.loads(proc.stdout)


def test_available_base_detects_missing(tmp_path):
    write_manifest(tmp_path, PARTIAL_MANIFEST)
    plan = run_init(tmp_path, "--plan")
    names = {b["name"] for b in plan["available_base"]}
    assert MISSING_BASE <= names, plan["available_base"]
    assert plan["adopted_base"] == []
    # informational only — not materialized without adopt
    assert {a["name"] for a in plan["actions"] if a["action"] == "add"}.isdisjoint(MISSING_BASE)


def test_available_base_empty_when_all_listed(tmp_path):
    init_project(tmp_path)  # seeds the FULL base set
    plan = run_init(tmp_path, "--plan")
    assert plan["available_base"] == []


def test_adopt_base_appends_and_materializes(tmp_path):
    hy = write_manifest(tmp_path, PARTIAL_MANIFEST)
    res = run_init(tmp_path, "--apply", "--adopt-base")
    assert {b["name"] for b in res["adopted_base"]} == MISSING_BASE
    assert res["available_base"] == []
    # harness.yaml now lists both, tagged source: plugin
    text = hy.read_text()
    for name in MISSING_BASE:
        assert name in text
    # materialized into .claude/
    assert (tmp_path / ".claude" / "microskills" / "analyze-monolith-orchestrator").is_dir()
    assert (tmp_path / ".claude" / "workflow-defs" / "decompose-monolith-orchestrator").is_dir()
    assert (tmp_path / ".claude" / "workflow-defs" / "build-workflow-from-plan").is_dir()
    # idempotent: re-run sees no drift and nothing new to add
    second = run_init(tmp_path, "--plan")
    assert second["available_base"] == []
    assert {a["name"] for a in second["actions"] if a["action"] == "add"}.isdisjoint(MISSING_BASE)


def test_adopt_base_preserves_header_and_custom_entry(tmp_path):
    hy = write_manifest(tmp_path, PARTIAL_MANIFEST)
    run_init(tmp_path, "--apply", "--adopt-base")
    text = hy.read_text()
    assert text.startswith("# Harness manifest (v2)")  # header comment survived
    assert "name: greet-user" in text and "source: custom" in text  # custom entry survived
    assert "profiles: [base]" in text  # custom entry's profiles survived


def test_init_vendors_snippets_as_engine(tmp_path):
    # V1: catalog/workflow-defs/_snippets/ ({{snippet:NAME}} includes) is
    # plugin/ENGINE-owned — materialized to .claude/workflow-defs/_snippets/ by
    # initialize-harness and tracked in the ENGINE ledger (not as a component, so
    # harness-sync never touches it).
    init_project(tmp_path)
    snip = tmp_path / ".claude" / "workflow-defs" / "_snippets" / "finalize-protocol.md"
    assert snip.exists(), "_snippets not materialized into .claude/workflow-defs/"
    src = REPO / "catalog" / "workflow-defs" / "_snippets" / "finalize-protocol.md"
    assert snip.read_bytes() == src.read_bytes()
    state = json.loads((tmp_path / ".claude" / ".harness-state.json").read_text())
    assert ".claude/workflow-defs/_snippets/finalize-protocol.md" in state["engine"]["installed_paths"]
    assert "_snippets" not in state.get("components", {})


def test_init_compiles_snippet_consuming_def_in_fresh_project(tmp_path):
    # End-to-end: a fresh project's materialized runtime can compile a def whose
    # prompt is a {{snippet:...}} include (the runtime _snippets/ resolves it).
    init_project(tmp_path)
    rc, out, err = run_materialized(tmp_path, "compile-workflow", "microskill-create", "--plan")
    assert rc == 0, out + err
    data = json.loads(out)
    fin = [s for s in data["manifest"]["steps"] if s.get("node") == "finalize"][0]
    assert "vendoring the approved microskill" in fin["prompt"]
    assert "{{snippet:" not in fin["prompt"]


def test_init_vendors_subgraphs_as_engine(tmp_path):
    # The `subgraph:` registry (catalog/workflow-defs/_subgraphs/<name>/SUBGRAPH.yaml)
    # is plugin/ENGINE-owned, exactly like _snippets: initialize-harness materializes it
    # to .claude/workflow-defs/_subgraphs/ and tracks it in the ENGINE ledger (not a
    # component, so harness-sync never touches it).
    init_project(tmp_path)
    sg = (tmp_path / ".claude" / "workflow-defs" / "_subgraphs"
          / "review-synthesize" / "SUBGRAPH.yaml")
    assert sg.exists(), "_subgraphs not materialized into .claude/workflow-defs/"
    src = (REPO / "catalog" / "workflow-defs" / "_subgraphs"
           / "review-synthesize" / "SUBGRAPH.yaml")
    assert sg.read_bytes() == src.read_bytes()
    state = json.loads((tmp_path / ".claude" / ".harness-state.json").read_text())
    assert (".claude/workflow-defs/_subgraphs/review-synthesize/SUBGRAPH.yaml"
            in state["engine"]["installed_paths"])
    assert "review-synthesize" not in state.get("components", {})


def test_init_compiles_subgraph_splicing_def_from_runtime(tmp_path):
    # End-to-end: a fresh project's materialized runtime can compile + validate a def
    # whose `subgraph:` node resolves from the vendored _subgraphs/ — with NO
    # CLAUDE_PLUGIN_ROOT (the segment-agent env). The splice replaces `rev` in place with
    # its namespaced inner nodes, collapsing into ONE background segment.
    init_project(tmp_path)
    src = REPO / "catalog" / "workflow-defs" / "subgraph-smoke"
    dst = tmp_path / ".claude" / "workflow-defs" / "subgraph-smoke"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".compiled"))

    rc, out, err = run_materialized(tmp_path, "compile-workflow", "subgraph-smoke", "--plan")
    assert rc == 0, out + err
    data = json.loads(out)
    assert data["segments"] == 1 and data["checkpoints"] == 0
    seg = data["manifest"]["steps"][0]
    # The subgraph node `rev` was spliced in place into its namespaced inner nodes — its
    # survival would have hard-blocked the compile (the desugar fail-loud sweep).
    assert seg["nodes"] == ["author", "rev__review", "rev__synthesize", "publish"]
    assert "rev" not in seg["nodes"]

    rc, out, err = run_materialized(
        tmp_path, "validate-workflow",
        str(dst / "WORKFLOW.yaml"), str(dst / "profiles" / "base.yaml"),
        "--defs-root", str(tmp_path / ".claude" / "workflow-defs"))
    assert rc == 0, out + err
    assert json.loads(out)["pass"] is True


def test_reinit_preserves_compiled_runs_ledger(tmp_path):
    # The dispatcher's per-run ledgers live under
    # .claude/workflow-defs/<name>/.compiled/runs/<run-id>/ (run-journal). .compiled/
    # is generated/transient (VENDOR_IGNORE_PARTS): init never vendors, hashes, or
    # ledger-tracks it, so a re-reconcile must leave run ledgers untouched.
    init_project(tmp_path)
    run_dir = (tmp_path / ".claude" / "workflow-defs" / "microskill-create"
               / ".compiled" / "runs" / "20260610T120000Z-abc123")
    (run_dir / "run-inputs").mkdir(parents=True)
    (run_dir / "run-config.json").write_text('{"v": 1, "run_id": "20260610T120000Z-abc123"}')
    (run_dir / "run-state.json").write_text(
        '{"manifest_hash": "sha256:h", "step_index": 1, "results": {}}')
    (run_dir / "journal.jsonl").write_text('{"v":1,"event":"run_start"}\n')
    (run_dir / "run-inputs" / "x.cat").write_text("materialized")

    init_project(tmp_path)  # re-reconcile (refresh path)
    for f in ("run-config.json", "run-state.json", "journal.jsonl", "run-inputs/x.cat"):
        assert (run_dir / f).exists(), f"{f} clobbered by re-init"
    state = json.loads((tmp_path / ".claude" / ".harness-state.json").read_text())
    comp = state["components"]["microskill-create"]
    assert not any(".compiled" in p for p in comp["installed_paths"])
    assert not any(".compiled" in p for p in state["engine"]["installed_paths"])


def test_init_materializes_run_journal_executable(tmp_path):
    # run-journal is engine (catalog/scripts/*): init must vendor it with its exec
    # bit so the dispatcher can call .claude/scripts/run-journal.
    init_project(tmp_path)
    rj = tmp_path / ".claude" / "scripts" / "run-journal"
    assert rj.exists(), "run-journal not materialized into .claude/scripts/"
    assert os.access(rj, os.X_OK), "run-journal lost its exec bit"
    rc, out, err = run_materialized(
        tmp_path, "run-journal", "init",
        "--runs-dir", str(tmp_path / "runs"),
        "--manifest-hash", "sha256:h1", "--run-id", "r1")
    assert rc == 0, out + err
    assert json.loads(out)["run_id"] == "r1"


# --- harness lifecycle: provenance stamping, version pins (holds), eject-to-custom ----------
#
# Hermetic plugin-world fixtures: a throwaway plugin root (catalog/ with one base-tagged
# microskill + .claude-plugin/plugin.json) and a throwaway project. Nothing touches the
# real repo; the real catalog's plugin.json version never leaks into these assertions.

def set_plugin_version(plugin_root, version):
    pj = plugin_root / ".claude-plugin"
    pj.mkdir(exist_ok=True)
    (pj / "plugin.json").write_text(
        json.dumps({"name": "microskills", "version": version}) + "\n")


def write_demo_body(plugin_root, body):
    (plugin_root / "catalog" / "microskills" / "demo" / "MICROSKILL.md").write_text(
        f"---\nname: demo\ndescription: A demo microskill.\nbase: true\n---\n\n# demo\n\n{body}\n")


def make_plugin_world(tmp, version="0.9.0"):
    """Plugin root with catalog/microskills/demo (base-tagged) + plugin.json, and a project dir."""
    plugin_root = tmp / "plugin"
    d = plugin_root / "catalog" / "microskills" / "demo"
    (d / "profiles").mkdir(parents=True)
    write_demo_body(plugin_root, "Body v1.")
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    set_plugin_version(plugin_root, version)
    proj = tmp / "proj"
    proj.mkdir()
    return plugin_root, proj


def run_world(proj, plugin_root, *flags):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    proc = subprocess.run(
        [sys.executable, str(INIT), *flags, "--project-root", str(proj),
         "--catalog", str(plugin_root / "catalog")],
        capture_output=True, text=True, env=env)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data


def run_sync(proj, *flags):
    proc = subprocess.run(
        [sys.executable, str(SYNC), *flags,
         "--harness-root", str(proj / "harness"),
         "--harness-yaml", str(proj / "harness" / "harness.yaml"),
         "--state", str(proj / ".claude" / ".harness-state.json"),
         "--deploy-root", str(proj / ".claude")],
        capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data


def world_state(proj):
    return json.loads((proj / ".claude" / ".harness-state.json").read_text())


def write_pinned_manifest(proj, pin):
    hy = proj / "harness" / "harness.yaml"
    hy.parent.mkdir(parents=True, exist_ok=True)
    hy.write_text(
        "version: 2\nmicroskills:\n  - name: demo\n    source: plugin\n"
        f"    version: {pin}\n")
    return hy


# --- provenance stamping -----------------------------------------------------------------

def test_apply_stamps_plugin_version(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path, version="0.8.0")
    rc, res = run_world(proj, plugin_root, "--apply")
    assert rc == 0, res
    assert res["plugin_version"] == "0.8.0"
    entry = world_state(proj)["components"]["demo"]
    assert entry["source"] == "plugin"
    assert entry["plugin_version"] == "0.8.0"
    add = next(a for a in res["actions"] if a["action"] == "add" and a["name"] == "demo")
    assert add["new_version"] == "0.8.0"


def test_plan_reports_version_transition_not_hash_pair(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path, version="0.8.0")
    run_world(proj, plugin_root, "--apply")
    # plugin upgrade: new version, new bytes
    set_plugin_version(plugin_root, "0.9.0")
    write_demo_body(plugin_root, "Body v2.")
    rc, plan = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan
    upd = next(a for a in plan["actions"] if a["action"] == "update" and a["name"] == "demo")
    assert upd["old_version"] == "0.8.0"
    assert upd["new_version"] == "0.9.0"
    assert upd["transition"] == "0.8.0 -> 0.9.0"
    assert "old_hash" not in upd and "new_hash" not in upd
    # apply re-stamps the ledger at the new version
    rc, _ = run_world(proj, plugin_root, "--apply")
    assert rc == 0
    assert world_state(proj)["components"]["demo"]["plugin_version"] == "0.9.0"


def test_same_version_drift_keeps_hash_pair(tmp_path):
    # Catalog bytes drifted with NO version bump (the dogfood-repo case): there is no
    # version transition to report, so the informative hash pair is preserved.
    plugin_root, proj = make_plugin_world(tmp_path, version="0.8.0")
    run_world(proj, plugin_root, "--apply")
    write_demo_body(plugin_root, "Body v2.")
    rc, plan = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan
    upd = next(a for a in plan["actions"] if a["action"] == "update" and a["name"] == "demo")
    assert upd["old_version"] == "0.8.0" and upd["new_version"] == "0.8.0"
    assert "transition" not in upd
    assert upd["old_hash"] != upd["new_hash"]


def test_noop_does_not_stamp(tmp_path):
    # Stamp on add/update only: a noop never rewrites the recorded provenance.
    plugin_root, proj = make_plugin_world(tmp_path, version="0.8.0")
    run_world(proj, plugin_root, "--apply")
    set_plugin_version(plugin_root, "0.9.0")   # version moved, bytes identical -> noop
    rc, res = run_world(proj, plugin_root, "--apply")
    assert rc == 0, res
    assert [a["action"] for a in res["actions"] if a["name"] == "demo"] == ["noop"]
    assert world_state(proj)["components"]["demo"]["plugin_version"] == "0.8.0"


# --- version pins are HOLDs ---------------------------------------------------------------

def test_version_pin_holds_pending_update(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path, version="0.8.0")
    write_pinned_manifest(proj, "0.8.0")
    rc, res = run_world(proj, plugin_root, "--apply")   # pin == current -> installs
    assert rc == 0, res
    deployed = proj / ".claude" / "microskills" / "demo" / "MICROSKILL.md"
    assert "Body v1." in deployed.read_text()
    # plugin upgrades past the pin
    set_plugin_version(plugin_root, "0.9.0")
    write_demo_body(plugin_root, "Body v2.")
    rc, plan = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan
    hold = next(a for a in plan["actions"] if a["action"] == "hold")
    assert hold["name"] == "demo" and hold["kind"] == "microskill"
    assert hold["pinned"] == "0.8.0" and hold["available"] == "0.9.0"
    assert hold["deployed_version"] == "0.8.0"
    assert hold["pending"] == "update"
    assert plan["summary"]["hold"] == 1
    assert not [a for a in plan["actions"]
                if a["name"] == "demo" and a["action"] in ("add", "update", "remove")]
    # apply: deployed bytes stay put, ledger untouched
    rc, res = run_world(proj, plugin_root, "--apply")
    assert rc == 0, res
    assert "Body v1." in deployed.read_text()
    entry = world_state(proj)["components"]["demo"]
    assert entry["plugin_version"] == "0.8.0"
    # the pending change surfaces in EVERY plan until the pin moves
    rc, plan2 = run_world(proj, plugin_root, "--plan")
    assert [a for a in plan2["actions"] if a["action"] == "hold"]
    # move the pin -> the held update flows, reported as a version transition
    write_pinned_manifest(proj, "0.9.0")
    rc, plan3 = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan3
    upd = next(a for a in plan3["actions"] if a["action"] == "update")
    assert upd["transition"] == "0.8.0 -> 0.9.0"
    assert plan3["summary"]["hold"] == 0
    rc, _ = run_world(proj, plugin_root, "--apply")
    assert "Body v2." in deployed.read_text()
    assert world_state(proj)["components"]["demo"]["plugin_version"] == "0.9.0"


def test_version_pin_holds_uninstalled_add(tmp_path):
    # A pinned component never materialized: the catalog can only provide its current
    # version, so the add is held (nothing written) rather than violating the pin.
    plugin_root, proj = make_plugin_world(tmp_path, version="0.9.0")
    write_pinned_manifest(proj, "0.8.0")
    rc, plan = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan
    hold = next(a for a in plan["actions"] if a["action"] == "hold")
    assert hold["pending"] == "add"
    assert hold["pinned"] == "0.8.0" and hold["available"] == "0.9.0"
    rc, res = run_world(proj, plugin_root, "--apply")
    assert rc == 0, res
    assert not (proj / ".claude" / "microskills" / "demo").exists()
    assert "demo" not in world_state(proj)["components"]


# --- eject-to-custom ------------------------------------------------------------------------

def test_eject_plan_writes_nothing(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path)
    run_world(proj, plugin_root, "--apply")   # seeds harness.yaml + installs demo
    rc, res = run_world(proj, plugin_root, "--eject", "demo")
    assert rc == 0, res
    assert res["mode"] == "plan"
    assert res["eject"]["name"] == "demo" and res["eject"]["kind"] == "microskill"
    assert res["eject"]["in_sync"] is True
    assert not (proj / "harness" / "microskills" / "demo").exists()
    assert "source: plugin" in (proj / "harness" / "harness.yaml").read_text()
    assert world_state(proj)["components"]["demo"]["source"] == "plugin"


def test_eject_apply_transfers_ownership_and_next_sync_noops(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path)
    run_world(proj, plugin_root, "--apply")
    before = world_state(proj)["components"]["demo"]
    # generated junk in the catalog source must not be vendored (vendor_skip on copy)
    src = plugin_root / "catalog" / "microskills" / "demo"
    (src / ".compiled").mkdir()
    (src / ".compiled" / "manifest.json").write_text("{}")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    rc, res = run_world(proj, plugin_root, "--eject", "demo", "--apply")
    assert rc == 0, res
    assert res["state_written"] is True
    # bytes vendored into harness/, junk skipped
    vend = proj / "harness" / "microskills" / "demo"
    assert (vend / "MICROSKILL.md").exists()
    assert (vend / "profiles" / "base.yaml").exists()
    assert not (vend / ".compiled").exists()
    assert not (vend / "__pycache__").exists()
    # harness.yaml source line rewritten in place, header comment preserved
    text = (proj / "harness" / "harness.yaml").read_text()
    assert text.startswith("# Harness manifest (v2)")
    entry_lines = [ln.strip() for ln in text.splitlines() if not ln.strip().startswith("#")]
    assert "source: custom" in entry_lines and "source: plugin" not in entry_lines
    # ledger entry flipped atomically: same hash/paths, custom-owned, stamp dropped
    entry = world_state(proj)["components"]["demo"]
    assert entry["source"] == "custom"
    assert "plugin_version" not in entry
    assert entry["source_hash"] == before["source_hash"]
    assert entry["installed_paths"] == before["installed_paths"]
    assert not list((proj / ".claude").glob("*.tmp"))
    # tree_hash is location-independent -> the next harness-sync plans a noop
    rc, sync = run_sync(proj, "--plan")
    assert rc == 0, sync
    assert [a["action"] for a in sync["actions"]] == ["noop"]
    # and a subsequent initialize-harness no longer reconciles the ejected component
    rc, plan = run_world(proj, plugin_root, "--plan")
    assert rc == 0, plan
    assert not [a for a in plan["actions"] if a["name"] == "demo"]


def test_eject_rejects_bad_targets(tmp_path):
    plugin_root, proj = make_plugin_world(tmp_path)
    run_world(proj, plugin_root, "--apply")
    # unknown name
    rc, res = run_world(proj, plugin_root, "--eject", "nope")
    assert rc == 1 and "error" in res
    # destination already exists
    (proj / "harness" / "microskills" / "demo").mkdir(parents=True)
    rc, res = run_world(proj, plugin_root, "--eject", "demo")
    assert rc == 1 and "error" in res
    (proj / "harness" / "microskills" / "demo").rmdir()
    # already ejected -> source: custom is not ejectable
    rc, res = run_world(proj, plugin_root, "--eject", "demo", "--apply")
    assert rc == 0, res
    rc, res = run_world(proj, plugin_root, "--eject", "demo")
    assert rc == 1 and "error" in res


def test_eject_requires_ledger_entry(tmp_path):
    # Listed in harness.yaml but never materialized: nothing to transfer -> hard error.
    plugin_root, proj = make_plugin_world(tmp_path)
    hy = proj / "harness" / "harness.yaml"
    hy.parent.mkdir(parents=True)
    hy.write_text("version: 2\nmicroskills:\n  - name: demo\n    source: plugin\n")
    rc, res = run_world(proj, plugin_root, "--eject", "demo")
    assert rc == 1 and "error" in res


# --- flip_entry_source line surgery (unit) --------------------------------------------------

def load_init_module():
    loader = importlib.machinery.SourceFileLoader("initialize_harness", str(INIT))
    spec = importlib.util.spec_from_loader("initialize_harness", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


GNARLY_MANIFEST = """\
# header comment
version: 2
microskills:
  - name: alpha
    source: plugin
  # interstitial comment
  - name: demo
    source: plugin
    profiles: [base]
    version: 0.8.0
  - name: custom-one
    source: custom
workflows:
  - name: demo
    source: plugin
"""


def test_flip_entry_source_targets_exactly_one_entry():
    mod = load_init_module()
    out, flipped = mod.flip_entry_source(GNARLY_MANIFEST, "microskills", "demo")
    assert flipped
    assert out.startswith("# header comment")            # comments survive
    assert "  # interstitial comment" in out
    assert "profiles: [base]" in out                     # sibling fields survive
    assert out.count("source: custom") == 2              # custom-one + the flipped demo
    assert out.count("source: plugin") == 2              # alpha + the WORKFLOW demo untouched
    wf_block = out.split("workflows:")[1]
    assert "source: plugin" in wf_block                  # same-name entry in the other list kept


def test_flip_entry_source_misses_are_loud():
    mod = load_init_module()
    out, flipped = mod.flip_entry_source(GNARLY_MANIFEST, "microskills", "nope")
    assert not flipped and out == GNARLY_MANIFEST
    out, flipped = mod.flip_entry_source(GNARLY_MANIFEST, "microskills", "custom-one")
    assert not flipped                                   # already custom: nothing to flip
    out, flipped = mod.flip_entry_source(GNARLY_MANIFEST, "workflows", "alpha")
    assert not flipped                                   # wrong list


# --- _subgraphs engine vendoring (dir-of-dirs, parallel to _snippets) -----------------------

def _mk_subgraph_catalog(tmp, name="demo", inner_agent="reviewer"):
    """A throwaway catalog carrying ONLY a _subgraphs/<name>/SUBGRAPH.yaml — exercises
    engine_outputs/engine_hash on the new dir-of-dirs block in isolation (the other
    ENGINE_SUBDIRS + templates/ are absent → their is_dir() guards skip them)."""
    catalog = tmp / "catalog"
    d = catalog / "workflow-defs" / "_subgraphs" / name
    d.mkdir(parents=True)
    f = d / "SUBGRAPH.yaml"
    f.write_text(
        "version: 1\noutput: x\nnodes:\n"
        f"  - id: x\n    agent: {inner_agent}\n    prompt: do the thing\n")
    return catalog, f


def test_subgraphs_are_engine_outputs_dir_of_dirs(tmp_path):
    # The _subgraphs registry is plugin/ENGINE-owned: engine_outputs() enumerates the
    # nested SUBGRAPH.yaml (rglob, dir-of-dirs) and maps it under
    # .claude/workflow-defs/_subgraphs/<name>/SUBGRAPH.yaml — exactly like _snippets.
    mod = load_init_module()
    catalog, _ = _mk_subgraph_catalog(tmp_path)
    deploy = tmp_path / ".claude"
    dests = {str(d) for _, d in mod.engine_outputs(catalog, deploy)}
    assert str(deploy / "workflow-defs" / "_subgraphs" / "demo" / "SUBGRAPH.yaml") in dests


def test_subgraph_byte_edit_flips_engine_hash(tmp_path):
    # A byte edit to a _subgraphs file must move the engine hash so the refresh path
    # re-materializes it (engine_hash is the drift signal; engine_hash already folds
    # every output's bytes hierarchically).
    mod = load_init_module()
    catalog, f = _mk_subgraph_catalog(tmp_path)
    deploy = tmp_path / ".claude"
    h1 = mod.engine_hash(mod.engine_outputs(catalog, deploy))
    f.write_text(f.read_text().replace("agent: reviewer", "agent: critic"))
    h2 = mod.engine_hash(mod.engine_outputs(catalog, deploy))
    assert h1 != h2


def test_removed_subgraph_file_drops_from_engine_outputs(tmp_path):
    # A removed _subgraphs file drops from engine_outputs → it is no longer a materialize
    # target. The on-disk prune that follows from this is asserted in
    # test_materialize_engine_prunes_removed_subgraph_dir.
    mod = load_init_module()
    catalog, f = _mk_subgraph_catalog(tmp_path)
    deploy = tmp_path / ".claude"
    target = deploy / "workflow-defs" / "_subgraphs" / "demo" / "SUBGRAPH.yaml"
    assert any(d == target for _, d in mod.engine_outputs(catalog, deploy))
    f.unlink()
    assert not any(d == target for _, d in mod.engine_outputs(catalog, deploy))


def test_materialize_engine_prunes_removed_subgraph_dir(tmp_path):
    # The genuinely-new dir-of-dirs behavior (vs the flat _snippets block): removing a
    # vendored subgraph must prune the deployed SUBGRAPH.yaml AND its now-empty
    # _subgraphs/<name>/ (and _subgraphs/) dirs on the next reconcile — the on-disk prune,
    # asserted against disk per the assert-on-state convention.
    mod = load_init_module()
    catalog, f = _mk_subgraph_catalog(tmp_path)
    anchor, deploy = tmp_path, tmp_path / ".claude"
    # pass 1: materialize the subgraph into the runtime (fresh install, no prior ledger)
    outs1 = mod.engine_outputs(catalog, deploy)
    mod.materialize_engine(outs1, None, anchor, deploy)
    dep = deploy / "workflow-defs" / "_subgraphs" / "demo" / "SUBGRAPH.yaml"
    assert dep.read_text().startswith("version: 1")
    installed = [str(d.relative_to(anchor)) for _, d in outs1]
    # pass 2: the file is gone from the catalog → reconcile prunes it + its empty dirs
    f.unlink()
    mod.materialize_engine(mod.engine_outputs(catalog, deploy),
                           {"installed_paths": installed}, anchor, deploy)
    assert not dep.exists(), "deployed SUBGRAPH.yaml not pruned"
    assert not dep.parent.exists(), "empty _subgraphs/demo/ not pruned"
    assert not (deploy / "workflow-defs" / "_subgraphs").exists(), "empty _subgraphs/ not pruned"
