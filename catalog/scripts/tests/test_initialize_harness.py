"""
Tests for initialize-harness materialization + runtime schema resolution.
Run: python3 -m pytest catalog/scripts/tests/ -v

These guard the clean-install bug: the runtime scripts resolve their JSON schemas from
templates/, which init must materialize into .claude/templates/ so the materialized scripts
find them with NO CLAUDE_PLUGIN_ROOT in the environment (segment agents run them by relative
path). We init into a tmp project (CLAUDE_PLUGIN_ROOT=REPO, as the dispatcher does), then run
the MATERIALIZED scripts with the env var unset — exactly the failing case from the live test.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "catalog" / "scripts" / "initialize-harness"

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

# Base-tagged catalog components absent from PARTIAL_MANIFEST.
MISSING_BASE = {"analyze-monolith-orchestrator", "decompose-monolith-orchestrator"}


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
