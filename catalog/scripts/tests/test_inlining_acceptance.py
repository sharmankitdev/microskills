"""Sub-PR 6 — end-to-end acceptance for compile-time workflow inlining.

Proves the engine compiles a microskill-create-SHAPE parent FLAT: a refine→plan→build
pipeline of static `workflow:` nodes (the REAL plan-rvs + implement-rvs, plus a small
hand-authored refine stand-in) inlines into THREE loop regions, the guarded implement
region if-wraps, the human approval gate is a real checkpoint, and every emitted segment
parses. Hermetic: copies the catalog microskills/ + workflow-defs/ into a tmp world
(microskills resolve from the defs-root's sibling microskills/), then writes the fixture.

There is no real refine front-end in the shipped create pipelines (the refine-requirements
def was retired in the 2026-06-21 rewire); this hand-authored refine-stub exists ONLY to
supply a third loop region so the test exercises the ENGINE's multi-region inlining, not any
host wiring. The host orders the three regions
with explicit depends_on (a region's zero-dep node would otherwise float across a region
boundary and break do/while contiguity — the multi-region separation contract).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
CATALOG = ROOT / "catalog"

REFINE_STUB = """
version: 1
name: refine-stub
base: true
inputs:
  sources_path: { type: string, required: true, materialize: file }
  staging_dir: { type: string, required: true }
nodes:
  - id: assemble
    agent: scratch
    prompt: "assemble ${workflow.inputs.sources_path}"
  - id: critique
    agent: scratch
    prompt: "critique ${assemble.output}"
loop:
  body: [critique]
  until: ${critique.output.done == true}
  max_iters: 2
  on_exhaust: { action: escalate, on_headless: fail, notes_input: sources_path }
output: { from: critique }
"""

MC_ACCEPT = """
version: 1
name: mc-accept
imports: [refine-stub, plan-rvs, implement-rvs]
inputs:
  requirement: { required: true, materialize: file }
  staging_dir: { required: true }
nodes:
  - id: refine
    workflow: refine-stub
    inputs:
      sources_path: ${workflow.inputs.requirement}
      staging_dir: ${workflow.inputs.staging_dir}
  - id: plan_rvs
    workflow: plan-rvs
    customize: { profile: microskill-create-autonomous }
    depends_on: [refine]
    inputs:
      requirement_path: ${refine.output.document_path}
      staging_dir: ${workflow.inputs.staging_dir}
  - id: impl_rvs
    workflow: implement-rvs
    customize: { profile: microskill-create }
    when: ${plan_rvs.output.scope_advisory == null}
    depends_on: [refine, plan_rvs]
    inputs:
      plan_path: ${plan_rvs.output.plan_path}
      name: ${plan_rvs.output.name}
      requirement_path: ${refine.output.document_path}
      staging_dir: ${workflow.inputs.staging_dir}
  - id: finalize
    delegation: orchestrator
    when: ${plan_rvs.output.scope_advisory == null}
    depends_on: [impl_rvs]
    prompt: "vendor ${impl_rvs.output.staging_paths}"
gates:
  - id: approve_plan
    type: human_approval
    after: plan_rvs
    prompt: "approve plan?"
output: { from: finalize }
"""


def _accept_world(tmp_path: Path) -> Path:
    """Copy the catalog microskills/ + workflow-defs/ into tmp, add refine-stub +
    mc-accept, return the defs-root."""
    shutil.copytree(CATALOG / "microskills", tmp_path / "microskills")
    shutil.copytree(CATALOG / "workflow-defs", tmp_path / "workflow-defs",
                    ignore=shutil.ignore_patterns(".compiled"))
    defs = tmp_path / "workflow-defs"
    (defs / "refine-stub" / "profiles").mkdir(parents=True)
    (defs / "refine-stub" / "profiles" / "base.yaml").write_text("version: 1\n")
    (defs / "refine-stub" / "WORKFLOW.yaml").write_text(REFINE_STUB)
    (defs / "mc-accept").mkdir()
    (defs / "mc-accept" / "WORKFLOW.yaml").write_text(MC_ACCEPT)
    return defs


def test_microskill_create_shape_compiles_flat(tmp_path):
    defs = _accept_world(tmp_path)
    r = subprocess.run(
        [sys.executable, str(COMPILE), "mc-accept", "--defs-root", str(defs)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    manifest = json.loads((defs / "mc-accept" / ".compiled" / "manifest.json").read_text())
    steps = manifest["steps"]

    # FLAT: no surviving STATIC nested_workflow checkpoint (all three children inlined).
    assert not any(s.get("checkpoint_type") == "nested_workflow" for s in steps), \
        [s.get("checkpoint_type") for s in steps]

    # THREE loop regions: refine critique + plan-rvs + implement-rvs.
    loop_steps = [s for s in steps if s.get("is_loop")]
    assert len(loop_steps) >= 3, [s["nodes"] for s in loop_steps]

    # The implement-rvs region is guarded — its region_guard is stamped (hash-visible)
    # and references the scope_advisory guard.
    impl_loop = next((s for s in loop_steps
                      if any("impl_rvs" in n for n in s["nodes"])), None)
    assert impl_loop is not None
    assert "scope_advisory" in (impl_loop.get("region_guard") or ""), impl_loop.get("region_guard")

    # The human approval gate is a real checkpoint.
    gate_ids = [s.get("gate", {}).get("id") for s in steps if s.get("kind") == "checkpoint"]
    assert "approve_plan" in gate_ids, gate_ids

    # Every emitted segment parses.
    for seg in (defs / "mc-accept" / ".compiled").glob("seg-*.js"):
        chk = subprocess.run(["node", "--check", str(seg)], capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg}: {chk.stderr}"


def test_implement_rvs_region_if_wraps_on_scope_advisory(tmp_path):
    defs = _accept_world(tmp_path)
    r = subprocess.run(
        [sys.executable, str(COMPILE), "mc-accept", "--defs-root", str(defs)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    blob = "\n".join(
        p.read_text()
        for p in sorted((defs / "mc-accept" / ".compiled").glob("seg-*.js")))
    # The guard if-wraps the implement-rvs do/while region (one block), not each node.
    assert "scope_advisory == null) {" in blob, blob
    # The implement region's do/while survived the inline.
    assert "do {" in blob


# --- Task 6.2: in-scope regression — the existing create pipelines still compile ---

def _compile_real(name):
    return subprocess.run(
        [sys.executable, str(COMPILE), name,
         "--defs-root", str(CATALOG / "workflow-defs"), "--plan"],
        capture_output=True, text=True)


def test_existing_workflow_create_still_compiles():
    r = _compile_real("workflow-create")
    assert r.returncode == 0, r.stdout + r.stderr


def test_existing_microskill_create_still_compiles():
    r = _compile_real("microskill-create")
    assert r.returncode == 0, r.stdout + r.stderr
