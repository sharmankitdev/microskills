"""workflow-create rewired to the symmetric north-star (2026-06-21, sub-PRs 1 + 4).

Sub-PR 1 retired build-workflow-from-plan: workflow-create's `build` node became
`workflow: implement-rvs` + a host `finalize` orchestrator, and BWFP +
decompose-monolith-orchestrator defs were deleted.

Sub-PR 4 makes workflow-create SYMMETRIC to microskill-create: a `refine` front-end
(refine-requirements, create-spec-workflow — its critique loop becomes a loop region,
its clarify / present_refined / triage_reopened orchestrators become checkpoints, and
its terminal approve_requirements becomes Gate 1, HARD) → `plan_rvs` (plan-rvs; base
loop-less, autonomous adds a convergence loop region) → Gate 2 (approve_plan) →
`provision` (microskill-create per missing microskill) → `build` (implement-rvs, one
guarded loop region) → host `finalize`. plan_rvs + build review against the REFINED
document (${refine.output.document_path}).

The workflow planner's gaps flow as missing_microskills off plan-rvs's aggregate exit
(synth): the create-plan-rvs synth profile declares + echoes the field, the plan-rvs
WORKFLOW.yaml synth node passes ${plan.output.missing_microskills} through, and
workflow-create's provision `for_each` reads ${plan_rvs.output.missing_microskills}
(inlined to ${plan_rvs__synth.output.missing_microskills}). provision STAYS a
nested_workflow checkpoint — its fan-out N is RUNTIME (depth-1, NOT inlined).

manifest shape (confirmed): `compile --plan` prints JSON to stdout (no --json flag);
`data["manifest"]["steps"]` carries kind / is_loop / region_guard / checkpoint_type /
gate / node / nodes / for_each. A loop region = a segment step with is_loop True.
Region count: base = 2 (refine critique + build; plan-rvs base loop-less); autonomous
= 3 (refine critique + plan_rvs + build). Points --defs-root at the REAL catalog so the
imported workflows + microskill registry resolve.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
DEFS = ROOT / "catalog" / "workflow-defs"
WF = (DEFS / "workflow-create" / "WORKFLOW.yaml").read_text()


def _plan(profile="base"):
    return subprocess.run(
        [sys.executable, str(COMPILE), "workflow-create", "--defs-root", str(DEFS),
         "--profile", profile, "--plan"],
        capture_output=True, text=True,
    )


def _manifest(profile="base"):
    r = _plan(profile)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)["manifest"]


def _loops(manifest):
    return [s for s in manifest["steps"] if s.get("is_loop")]


def _gates(manifest):
    return {(s.get("gate") or {}).get("id"): (s.get("gate") or {})
            for s in manifest["steps"] if s.get("checkpoint_type") == "gate"}


def _gate_order(manifest):
    return [(s.get("gate") or {}).get("id")
            for s in manifest["steps"] if s.get("checkpoint_type") == "gate"]


def _nested_workflow_nodes(manifest):
    return [s.get("node") for s in manifest["steps"]
            if s.get("checkpoint_type") == "nested_workflow"]


# --- Sub-PR 1: BWFP retired -------------------------------------------------

def test_workflow_create_no_bwfp_import():
    assert "build-workflow-from-plan" not in WF


def test_bwfp_and_decompose_deleted():
    assert not (DEFS / "build-workflow-from-plan").exists()
    assert not (DEFS / "decompose-monolith-orchestrator").exists()


# --- Sub-PR 4: symmetric front-end + provision reposition --------------------

def test_no_task_plan_import():
    # The old task-plan front is gone — replaced by the plan-rvs RVS workflow.
    assert "microskills/task-plan" not in WF
    assert "task-plan" not in WF


def test_imports_symmetric_set():
    for imp in ("refine-requirements", "plan-rvs", "implement-rvs", "microskill-create"):
        assert imp in WF, imp


def test_base_compiles():
    r = _plan("base")
    assert r.returncode == 0, r.stderr or r.stdout


def test_autonomous_compiles():
    r = _plan("autonomous")
    assert r.returncode == 0, r.stderr or r.stdout


def test_base_two_loop_regions():
    # plan-rvs base is loop-less; refine's critique loop + the build (implement-rvs)
    # region are the two regions on the base (interactive) profile.
    m = _manifest("base")
    loops = _loops(m)
    assert len(loops) == 2, [s["nodes"] for s in loops]
    assert any(any("refine" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]
    assert any(any("build" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]


def test_autonomous_three_loop_regions():
    # autonomous adds plan-rvs's convergence loop → three regions: refine critique +
    # plan_rvs + build (implement-rvs).
    m = _manifest("autonomous")
    loops = _loops(m)
    assert len(loops) >= 3, [s["nodes"] for s in loops]
    assert any(any("refine" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]
    assert any(any("plan_rvs" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]
    assert any(any("build" in n for n in s["nodes"]) for s in loops), [s["nodes"] for s in loops]


def test_build_region_guarded_on_scope_advisory():
    # The inlined implement-rvs region if-wraps on scope_advisory == null so the
    # advisory path skips the whole build loop.
    m = _manifest("autonomous")
    loops = _loops(m)
    build = next(s for s in loops if any("build" in n for n in s["nodes"]))
    assert "scope_advisory" in (build.get("region_guard") or ""), build.get("region_guard")


def test_refine_and_rvs_inline_only_provision_stays_nested():
    # refine / plan-rvs / implement-rvs all inline FLAT; the ONLY surviving
    # nested_workflow checkpoint is provision (runtime for_each, depth-1).
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        assert _nested_workflow_nodes(m) == ["provision"], _nested_workflow_nodes(m)


def test_provision_is_nested_workflow_for_each_not_inlined():
    # provision's fan-out N is RUNTIME (the planner's missing_microskills), so the
    # compiler keeps it a depth-1 nested_workflow checkpoint into microskill-create —
    # NOT inlined as a loop region.
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        prov = next(s for s in m["steps"]
                    if s.get("checkpoint_type") == "nested_workflow" and s.get("node") == "provision")
        assert prov.get("for_each"), prov
        assert prov.get("workflow") == "microskill-create", prov.get("workflow")
        # provision is NEVER a loop region (no inlined build/provision nodes).
        assert not any(any("provision" in n for n in s["nodes"]) for s in _loops(m))


def test_missing_microskills_surfaced_through_plan_rvs():
    # The wf-domain planner's gaps ride plan-rvs's aggregate exit (synth): the
    # create-plan-rvs synth profile declares + echoes missing_microskills, and the
    # plan-rvs WORKFLOW.yaml synth node passes ${plan.output.missing_microskills}.
    synth_profile = (ROOT / "catalog" / "microskills" / "synthesize-review"
                     / "profiles" / "create-plan-rvs.yaml").read_text()
    assert "missing_microskills" in synth_profile
    plan_rvs_wf = (DEFS / "plan-rvs" / "WORKFLOW.yaml").read_text()
    assert "missing_microskills: ${plan.output.missing_microskills}" in plan_rvs_wf
    # The host references it off plan_rvs's aggregate exit.
    assert "${plan_rvs.output.missing_microskills}" in WF


def test_provision_for_each_reads_missing_microskills_after_inlining():
    # After plan-rvs inlines, provision's for_each resolves to the synth aggregate
    # exit's missing_microskills (${plan_rvs__synth.output.missing_microskills}).
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        prov = next(s for s in m["steps"] if s.get("node") == "provision")
        fe = prov.get("for_each") or ""
        assert "missing_microskills" in fe, fe
        assert "plan_rvs__synth" in fe, fe


def test_two_hard_gates_requirements_then_plan():
    # Gate 1 = refine's inlined approve_requirements (namespaced refine__…, flipped
    # warn→hard in create-spec-workflow); Gate 2 = the host approve_plan. Both present,
    # both severity hard, Gate 1 precedes Gate 2 — on both profiles.
    for profile in ("base", "autonomous"):
        m = _manifest(profile)
        gates = _gates(m)
        assert "refine__approve_requirements" in gates, list(gates)
        assert "approve_plan" in gates, list(gates)
        assert gates["refine__approve_requirements"].get("severity") == "hard"
        assert gates["approve_plan"].get("severity") == "hard"
        order = _gate_order(m)
        assert order.index("refine__approve_requirements") < order.index("approve_plan"), order


def test_create_spec_workflow_gate1_hard():
    p = (DEFS / "refine-requirements" / "profiles" / "create-spec-workflow.yaml").read_text()
    # The warn→hard flip (sub-PR 4 step 5): Gate 1 is the requirements checkpoint.
    assert "severity: hard" in p
    assert "severity: warn" not in p


def test_finalize_is_terminal_orchestrator():
    m = _manifest("base")
    nodes = [s.get("node") for s in m["steps"]
             if s.get("checkpoint_type") == "orchestrator_node"]
    assert "finalize" in nodes, nodes
    # finalize is the last orchestrator step (output: from finalize).
    assert nodes[-1] == "finalize", nodes


def test_autonomous_plan_rvs_uses_autonomous_profile():
    prof = (DEFS / "workflow-create" / "profiles" / "autonomous.yaml").read_text()
    assert "workflow-create-autonomous" in prof
    assert "gate_mode: auto" in prof


def test_segments_parse():
    # Full compile (writes .compiled/) + node --check every emitted segment — the
    # parse-gate a green substring suite would otherwise hide.
    import shutil
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node not on PATH")
    r = subprocess.run(
        [sys.executable, str(COMPILE), "workflow-create", "--defs-root", str(DEFS),
         "--profile", "autonomous"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    segs = sorted((DEFS / "workflow-create" / ".compiled").glob("seg-*.js"))
    assert segs, "no segments emitted"
    for seg in segs:
        chk = subprocess.run(["node", "--check", str(seg)], capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg.name}: {chk.stderr}"
