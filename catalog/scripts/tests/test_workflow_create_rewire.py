"""Sub-PR 1 of the 2026-06-21 production-rewire-create-pipelines plan: retire
build-workflow-from-plan. workflow-create's `build` node becomes
`workflow: implement-rvs`, a host `finalize` orchestrator node reads
${build.output.staging_paths}, and output routes through finalize. BWFP and
decompose-monolith-orchestrator defs are deleted."""
import subprocess, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
DEFS = ROOT / "catalog" / "workflow-defs"


def _compile(profile="base"):
    return subprocess.run([sys.executable, str(COMPILE), "workflow-create",
        "--defs-root", str(DEFS), "--profile", profile, "--plan"],
        capture_output=True, text=True)


def test_workflow_create_no_bwfp_import():
    wf = (DEFS / "workflow-create" / "WORKFLOW.yaml").read_text()
    assert "build-workflow-from-plan" not in wf


def test_workflow_create_compiles_with_implement_rvs():
    r = _compile()
    assert r.returncode == 0, r.stderr or r.stdout


def test_bwfp_and_decompose_deleted():
    assert not (DEFS / "build-workflow-from-plan").exists()
    assert not (DEFS / "decompose-monolith-orchestrator").exists()
