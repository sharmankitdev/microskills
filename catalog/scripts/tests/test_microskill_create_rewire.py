"""microskill-create rewired onto plan-rvs + implement-rvs (2026-06-21, sub-PRs 2-3);
refine-requirements front-end REMOVED (2026-06-21 refactor — redundant with the
adversarial plan phase + the approve_plan human gate).

The old guts (plan/implement/evaluate task-* nodes + the top-level implement-evaluate
loop) are replaced by two inlined `workflow:` children: `plan-rvs` (loop-less base;
autonomous adds a convergence loop region) → `implement-rvs` (one guarded loop region),
with the host keeping `finalize`. The sole gate is `approve_plan` (Gate 1). plan_rvs +
impl_rvs review against the RAW requirement_path (${workflow.inputs.requirement_path}) —
there is no upstream refine producing a refined document. Points --defs-root at the REAL
catalog so the imported workflows + microskill registry resolve.

manifest shape (confirmed): `compile --plan` prints JSON to stdout (no --json flag);
`data["manifest"]["steps"]` carries kind / is_loop / region_guard / checkpoint_type /
gate.id / nodes. A loop region = a segment step with is_loop True. The impl_rvs region
carries a region_guard containing scope_advisory. Region count: base = 1 (impl_rvs;
plan-rvs base loop-less); autonomous = 2 (plan_rvs + impl_rvs). The pipeline is fully
flat: 0 nested_workflow checkpoints.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
DEFS = ROOT / "catalog" / "workflow-defs"
WF = (DEFS / "microskill-create" / "WORKFLOW.yaml").read_text()


def _plan(profile):
    return subprocess.run(
        [sys.executable, str(COMPILE), "microskill-create", "--defs-root", str(DEFS),
         "--profile", profile, "--plan"],
        capture_output=True, text=True,
    )


def _manifest(profile):
    r = _plan(profile)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)["manifest"]


def _loops(manifest):
    return [s for s in manifest["steps"] if s.get("is_loop")]


def _gate_ids(manifest):
    return [s.get("gate", {}).get("id")
            for s in manifest["steps"] if s.get("checkpoint_type") == "gate"]


def test_no_task_imports():
    # The three generic task-* microskills are gone — replaced by the RVS workflows.
    assert "task-evaluate" not in WF
    assert "task-implement" not in WF
    assert "task-plan" not in WF


def test_imports_plan_rvs_and_implement_rvs():
    assert "plan-rvs" in WF
    assert "implement-rvs" in WF


def test_refine_front_end_removed():
    # The refine-requirements front-end was unwired: no import, no refine node, no
    # ${refine.output...} ref. The raw requirement_path is the ground truth now.
    assert "refine-requirements" not in WF
    assert "${refine.output" not in WF
    assert "workflow: refine-requirements" not in WF


def test_base_compiles():
    r = _plan("base")
    assert r.returncode == 0, r.stderr or r.stdout


def test_autonomous_compiles():
    r = _plan("autonomous")
    assert r.returncode == 0, r.stderr or r.stdout


def test_base_one_loop_region():
    # plan-rvs base is loop-less and refine is gone → impl_rvs is the SOLE region on
    # the base (interactive) profile.
    m = _manifest("base")
    loops = _loops(m)
    assert len(loops) == 1, [s["nodes"] for s in loops]
    assert any(any("impl_rvs" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]


def test_autonomous_two_loop_regions():
    # autonomous adds plan-rvs's convergence loop → two regions: plan_rvs + impl_rvs.
    m = _manifest("autonomous")
    loops = _loops(m)
    assert len(loops) == 2, [s["nodes"] for s in loops]
    assert any(any("plan_rvs" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]
    assert any(any("impl_rvs" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]


def test_no_nested_workflow_checkpoint():
    # microskill-create is fully flat — both children (plan-rvs, implement-rvs) inline,
    # so no nested_workflow checkpoint survives on either profile.
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        offenders = [s for s in m["steps"] if s.get("checkpoint_type") == "nested_workflow"]
        assert not offenders, offenders


def test_single_plan_approval_gate():
    # approve_plan is the sole human-approval gate (Gate 1) on both profiles; the old
    # refine__approve_requirements gate is gone with the front-end. (loop_exhaust_*
    # escalation gates come from the inlined RVS loops and are not requirement gates.)
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        gate_ids = _gate_ids(m)
        assert "approve_plan" in gate_ids, gate_ids
        assert "refine__approve_requirements" not in gate_ids, gate_ids


def test_impl_region_guarded():
    # The inlined implement-rvs region if-wraps on scope_advisory == null so the
    # advisory path skips the whole build loop.
    m = _manifest("autonomous")
    loops = _loops(m)
    impl = next(s for s in loops if any("impl_rvs" in n for n in s["nodes"]))
    assert "scope_advisory" in (impl.get("region_guard") or ""), impl.get("region_guard")


def test_approve_plan_gate_present():
    # Gate 1 (the plan approval) survives the rewire on both profiles.
    assert "approve_plan" in _gate_ids(_manifest("base"))
    assert "approve_plan" in _gate_ids(_manifest("autonomous"))


def test_finalize_is_terminal_orchestrator():
    m = _manifest("base")
    nodes = [s.get("node") for s in m["steps"]
             if s.get("checkpoint_type") == "orchestrator_node"]
    assert "finalize" in nodes, nodes


def test_segments_parse():
    # Full compile (writes .compiled/) + node --check every emitted segment — the
    # parse-gate a green substring suite would otherwise hide.
    import shutil
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node not on PATH")
    r = subprocess.run(
        [sys.executable, str(COMPILE), "microskill-create", "--defs-root", str(DEFS),
         "--profile", "autonomous"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    segs = sorted((DEFS / "microskill-create" / ".compiled").glob("seg-*.js"))
    assert segs, "no segments emitted"
    for seg in segs:
        chk = subprocess.run(["node", "--check", str(seg)], capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg.name}: {chk.stderr}"
