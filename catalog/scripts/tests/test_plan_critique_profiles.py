"""Plan-critique content tests (§8 step 7, sub-PR 3).

The plan-stage RVS reviews the planner's plan.yaml object. These tests pin the
three-way naming invariant across all 12 plan-critique review-dimension profiles
(filename == vars.dimension == snippet-prefix `<dim>-rubric`), assert NO floor
carve-out leaks into a plan rubric body (the plan stage has no validator), pin the
review severity vocabulary, and check the verify registration profiles. (The
collect-findings fan-in was removed — verify reads the panel via ${review[].findings}.)
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
SKILL_ROOT = REPO / "catalog" / "microskills"

MS_DIMS = [
    "plan-ms-completeness",
    "plan-ms-scope-fit",
    "plan-ms-input-contract",
    "plan-ms-output-contract",
    "plan-ms-name-capability",
    "plan-ms-failure-coverage",
    "plan-ms-model-tiering",
]

WF_DIMS = [
    "plan-wf-completeness",
    "plan-wf-graph-correctness",
    "plan-wf-control-flow",
    "plan-wf-reuse-survey",
    "plan-wf-scope-fit",
    "plan-wf-name-capability",
    "plan-wf-decompose-fidelity",
    "plan-wf-delegation-mapping",
    "plan-wf-model-tiering",
]


def _resolve(ms, profile):
    p = subprocess.run(
        [
            sys.executable,
            str(RESOLVE),
            ms,
            "--profile",
            profile,
            "--skill-root",
            str(SKILL_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, f"{ms}/{profile}: {p.stderr}"
    return json.loads(p.stdout)


def _assert_plan_dim_invariant(dim):
    r = _resolve("review-dimension", dim)
    cfg = r["config"]
    # filename == vars.dimension
    assert cfg["vars"]["dimension"] == dim
    snaps = cfg["context"]["snippets"]
    # snippet[0].name prefix == dimension (the four-way naming invariant)
    assert snaps[0]["name"] == f"{dim}-rubric"
    # the rubric BODY is the snippet text the reviewer actually reasons over
    rubric = " ".join(s.get("text", "") for s in snaps).lower()
    # reviews the plan object
    assert "plan.yaml" in rubric or "plan" in cfg["vars"]["artifact_kind"].lower()
    # NO floor carve-out: the plan stage has no validator, so the rubric must never
    # cite the deterministic floor or a programmatic validator (the phantom-guarantee trap)
    assert "deterministic-floor" not in rubric
    assert "validate-microskill" not in rubric
    assert "validate-workflow" not in rubric
    # review severity vocabulary, never the floor's block|warn
    assert "blocker" in rubric and "major" in rubric
    assert "minor" in rubric and "nit" in rubric


def test_ms_plan_dims_resolve_and_obey_naming_invariant():
    for dim in MS_DIMS:
        _assert_plan_dim_invariant(dim)


def test_wf_plan_dims_resolve_and_obey_naming_invariant():
    for dim in WF_DIMS:
        _assert_plan_dim_invariant(dim)


def test_plan_verify_kinds():
    # The collect-findings fan-in node was removed — verify now fans out directly over
    # the review panel via ${review[].findings}, so there is no per-dimension collect
    # registration profile to check. The plan-stage verify-finding seats stay.
    for prof in ("plan-microskill", "plan-workflow"):
        vf = _resolve("verify-finding", prof)["config"]["vars"]
        assert "plan" in vf["artifact_kind"].lower()
