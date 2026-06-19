"""
Tests for run-validators (§8 step 5 / spec §11 D4): the deterministic-floor node,
the programmatic half carved out of the retired task-evaluate. base + microskill
domain run validate-microskill; the workflow profile swaps the floor-contract
snippet to validate-workflow + compile-workflow. Pure additive profiles — assert
each profile resolves with no leftover tokens, the floor-contract snippet differs
between domains, and the D4-pinned output_schema (no `pass`) holds.

Run: python3 -m pytest catalog/scripts/tests/test_run_validators.py -v
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
MS_ROOT = REPO / "catalog" / "microskills"
RV = MS_ROOT / "run-validators"
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
VALIDATE_MS = REPO / "catalog" / "scripts" / "validate-microskill"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}


def _resolve(profile=None):
    cmd = [sys.executable, str(RESOLVE), "run-validators", "--skill-root", str(MS_ROOT)]
    if profile:
        cmd += ["--profile", profile]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def test_validates_clean_all_profiles():
    for profile in ("base", "microskill", "workflow"):
        cfg = RV / "profiles" / f"{profile}.yaml"
        proc = subprocess.run(
            [sys.executable, str(VALIDATE_MS), str(RV / "MICROSKILL.md"), str(cfg)],
            capture_output=True, text=True, env=_ENV)
        data = json.loads(proc.stdout)
        blocks = [i for i in data["issues"] if i["severity"] == "block"]
        assert data["pass"] is True, (profile, blocks)


def test_base_and_workflow_floor_snippets_differ():
    rc_b, base, err_b = _resolve(None)
    rc_w, wf, err_w = _resolve("workflow")
    assert rc_b == 0 and rc_w == 0, (err_b, err_w)
    base_ctx = base.get("context_block", "") or ""
    wf_ctx = wf.get("context_block", "") or ""
    # microskill floor runs ONE validator; workflow floor runs validate-workflow
    # + compile-workflow. The snippet must change between domains.
    assert "validate-microskill" in base_ctx
    assert "validate-workflow" in wf_ctx and "compile-workflow" in wf_ctx
    assert base_ctx != wf_ctx
    assert base["unresolved_vars"] == [] and wf["unresolved_vars"] == []


def test_floor_mapping_clauses_present():
    """The D4 mapping is carried as profile prose; deleting a clause would
    silently change the floor's behavior while leaving the schema tests green.
    Assert the load-bearing mapping clauses survive in the resolved snippet."""
    _, base, _ = _resolve(None)
    _, wf, _ = _resolve("workflow")
    base_ctx = base.get("context_block", "") or ""
    wf_ctx = wf.get("context_block", "") or ""
    # microskill domain: severity remap + id ordering
    assert "block→blocker" in base_ctx and "warn→warn" in base_ctx
    assert "floor-1" in base_ctx
    # workflow domain: compile non-zero is ALWAYS a blocker at location compilation
    assert "ALWAYS a blocker" in wf_ctx
    assert "location compilation" in wf_ctx
    assert "compile-workflow" in wf_ctx


def test_output_schema_is_d4_pinned_no_pass():
    doc = yaml.safe_load((RV / "profiles" / "base.yaml").read_text())
    schema = doc["output_schema"]
    assert schema["required"] == ["dimension", "findings"]
    item = schema["properties"]["findings"]["items"]
    assert set(item["required"]) == {"id", "severity", "location", "message", "source"}
    assert item["properties"]["severity"]["enum"] == ["blocker", "warn"]
    # No `pass` anywhere — the floor node emits findings, the loop decides.
    assert "pass" not in schema["properties"]


def test_microskill_profile_inherits_base_floor():
    # microskill.yaml is the explicit selector; its resolved floor == base's.
    rc_m, ms, _ = _resolve("microskill")
    rc_b, base, _ = _resolve(None)
    assert rc_m == 0 and rc_b == 0
    assert ms.get("context_block", "") == base.get("context_block", "")
