"""plan-rvs compiles under both domains × loop variants (sub-PR 5).

plan-rvs is the reusable plan-stage RVS. The BASE (and the interactive workflow-create /
microskill-create profiles) ships LOOP-LESS — a single plan→review→verify→synth pass, with
the host's approve_plan gate as the backstop. The *-autonomous profiles ADD the convergence
loop. Points --defs-root at the REAL catalog so the profile/microskill registry resolves.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
DEFS = ROOT / "catalog" / "workflow-defs"


def _compile(profile):
    return subprocess.run(
        [sys.executable, str(COMPILE), "plan-rvs", "--defs-root", str(DEFS),
         "--profile", profile, "--plan"],
        capture_output=True, text=True,
    )


def _summary(profile):
    r = _compile(profile)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def test_plan_rvs_base_compiles():
    assert _compile("base").returncode == 0


def test_plan_rvs_workflow_create_is_loopless():
    # A loop-less single pass — one segment, NO checkpoint (no loop, no on_exhaust gate).
    s = _summary("workflow-create")
    assert s["checkpoints"] == 0, s["sequence"]
    assert s["sequence"] == ["segment[plan,review_plan_wf_completeness,"
                             "review_plan_wf_graph_correctness,review_plan_wf_control_flow,"
                             "review_plan_wf_reuse_survey,review_plan_wf_scope_fit,"
                             "review_plan_wf_name_capability,collect,verify,synth]"], s["sequence"]


def test_plan_rvs_microskill_create_is_loopless():
    s = _summary("microskill-create")
    assert s["checkpoints"] == 0, s["sequence"]
    # ms critique dimensions were swapped in.
    assert "review_plan_ms_completeness" in s["sequence"][0], s["sequence"]


def test_plan_rvs_workflow_create_autonomous_has_loop():
    # The loop variant adds the on_exhaust escalate gate → one checkpoint behind the
    # loop-body segment.
    s = _summary("workflow-create-autonomous")
    assert s["checkpoints"] == 1, s["sequence"]
    assert s["sequence"][-1] == "gate", s["sequence"]


def test_plan_rvs_microskill_create_autonomous_has_loop():
    s = _summary("microskill-create-autonomous")
    assert s["checkpoints"] == 1, s["sequence"]
    assert "review_plan_ms_completeness" in s["sequence"][0], s["sequence"]
