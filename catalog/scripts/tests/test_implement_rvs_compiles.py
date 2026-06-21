"""implement-rvs compiles under both creation profiles (sub-PR 4).

implement-rvs is the reusable implement-stage RVS loop (the now-retired
build-workflow-from-plan minus its finalize). It must compile standalone under
workflow-create AND microskill-create. Points
--defs-root at the REAL catalog so the profile/microskill registry resolves.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPILE = ROOT / "catalog" / "scripts" / "compile-workflow"
DEFS = ROOT / "catalog" / "workflow-defs"


def _compile(profile):
    return subprocess.run(
        [sys.executable, str(COMPILE), "implement-rvs", "--defs-root", str(DEFS),
         "--profile", profile, "--plan"],
        capture_output=True, text=True,
    )


def test_implement_rvs_workflow_create_compiles():
    r = _compile("workflow-create")
    assert r.returncode == 0, r.stderr or r.stdout


def test_implement_rvs_microskill_create_compiles():
    r = _compile("microskill-create")
    assert r.returncode == 0, r.stderr or r.stdout


def test_implement_rvs_base_compiles():
    r = _compile("base")
    assert r.returncode == 0, r.stderr or r.stdout
