"""microskill-create rewired onto plan-rvs + implement-rvs (2026-06-21, sub-PR 2).

The old guts (plan/implement/evaluate task-* nodes + the top-level implement-evaluate
loop) are replaced by inlined `workflow: plan-rvs` (loop-less base; autonomous adds a
convergence loop region) + `workflow: implement-rvs` (one guarded loop region), with the
host keeping `finalize`. No refine yet (sub-PR 3) — plan_rvs reads requirement_path
directly. Points --defs-root at the REAL catalog so the imported workflow + microskill
registry resolves.

manifest shape (confirmed): `compile --plan` prints JSON to stdout (no --json flag);
`data["manifest"]["steps"]` carries kind / is_loop / region_guard / checkpoint_type /
gate.id / nodes. A loop region = a segment step with is_loop True. The impl_rvs region
carries a region_guard containing scope_advisory.
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


def test_base_compiles():
    r = _plan("base")
    assert r.returncode == 0, r.stderr or r.stdout


def test_autonomous_compiles():
    r = _plan("autonomous")
    assert r.returncode == 0, r.stderr or r.stdout


def test_base_one_loop_region():
    # plan-rvs base is loop-less; impl_rvs contributes the single loop region.
    m = _manifest("base")
    loops = _loops(m)
    assert len(loops) == 1, [s["nodes"] for s in loops]
    assert any("impl_rvs" in n for n in loops[0]["nodes"]), loops[0]["nodes"]


def test_autonomous_two_loop_regions():
    # autonomous adds plan-rvs's convergence loop → two regions (plan_rvs + impl_rvs).
    m = _manifest("autonomous")
    loops = _loops(m)
    assert len(loops) >= 2, [s["nodes"] for s in loops]


def test_impl_region_guarded():
    # The inlined implement-rvs region if-wraps on scope_advisory == null so the
    # advisory path skips the whole build loop.
    m = _manifest("autonomous")
    loops = _loops(m)
    impl = next(s for s in loops if any("impl_rvs" in n for n in s["nodes"]))
    assert "scope_advisory" in (impl.get("region_guard") or ""), impl.get("region_guard")


def test_approve_plan_gate_present():
    # Gate 2 (the plan approval) survives the rewire on both profiles.
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
