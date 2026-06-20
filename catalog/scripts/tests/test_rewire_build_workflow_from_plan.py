"""
Tests for §8 step 7 sub-PR 4: the rewire of `build-workflow-from-plan` from the
single-LLM `[implement, evaluate]` loop to the host-inline review→verify→synthesize
(RVS) loop, plus the new `bundle-draft` script + microskill and the two combined
profiles it wires (`collect-findings/wf-create-all`, `verify-finding/workflow-draft-xref`).

What this proves:
  * the bundle-draft SCRIPT concatenates staged files with per-file provenance
    markers, appends grounding, is byte-deterministic, and fails loud (exit 2);
  * the bundle-draft MICROSKILL passes the floor and resolves its base profile;
  * `wf-create-all` registers exactly the 7 wf + 3 cross fan-in keys (the union of
    the shipped wf-create + cross-create profiles), and `workflow-draft-xref` swaps
    only artifact_kind to the bundle+index phrasing — no forbidden overlay keys;
  * the REAL build-workflow-from-plan validates + compiles to ONE background
    segment whose do/while body fans the 10 review dimensions out as a single
    parallel([...]) batch and runs the for_each verify as an in-loop
    parallelChunked fan-out (the sub-PR-1 engine, exercised host-inline);
  * the emitted segment PARSES under `node --check` (the load-bearing gate — a
    substring-only check shipped a SyntaxError in sub-PR 1);
  * task-evaluate is gone from the def (D3 retire) and the committed closure lock
    matches the rewired def (the re-baseline was committed);
  * build-workflow-from-plan's import set stays base-closed (a fresh consumer
    seeding the flagship base set gets every RVS dependency).

Hermetic where it can be; the compile/parse tests intentionally point at the real
catalog (like the closure-drift CI gate).

Run: python3 -m pytest catalog/scripts/tests/test_rewire_build_workflow_from_plan.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / "catalog"
MS_ROOT = CATALOG / "microskills"
DEFS = CATALOG / "workflow-defs"
BWFP = DEFS / "build-workflow-from-plan"
SCRIPT = CATALOG / "scripts" / "bundle-draft"
RESOLVE = CATALOG / "scripts" / "resolve-microskill"
VALIDATE_WF = CATALOG / "scripts" / "validate-workflow"
VALIDATE_MS = CATALOG / "scripts" / "validate-microskill"
COMPILE_WF = CATALOG / "scripts" / "compile-workflow"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

# The 10 review dimensions the RVS body fans out (7 wf draft + 3 cross-artifact).
WF_DIMS = [
    "wf-dag-decomposition-correctness", "wf-guard-logic-intent", "wf-prompt-task-fidelity",
    "wf-gate-prompt-options-quality", "wf-profile-overlay-coherence",
    "wf-spill-materialize-judgment", "wf-output-schema-downstream-fit",
]
CROSS_DIMS = ["duplicate-capability", "naming-collision", "reverse-consumer"]
REVIEW_NODE_VARS = [f"n_review_{d.replace('-', '_')}" for d in WF_DIMS] + \
                   [f"n_xreview_{d.replace('-', '_')}" for d in CROSS_DIMS]


# ───────────────────────────── bundle-draft script ──────────────────────────────

def _run_bundle(out, files, appends=()):
    cmd = [sys.executable, str(SCRIPT), "--out", str(out)]
    for a in appends:
        cmd += ["--append", str(a)]
    cmd += [str(f) for f in files]
    p = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, data, p.stderr


def _stage(tmp_path):
    a = tmp_path / "foo" / "WORKFLOW.yaml"
    b = tmp_path / "foo" / "profiles" / "base.yaml"
    a.parent.mkdir(parents=True, exist_ok=True)
    b.parent.mkdir(parents=True, exist_ok=True)
    a.write_text("version: 1\nname: foo\n", encoding="utf-8")
    b.write_text("version: 1\ninputs:\n  x: { required: true }\n", encoding="utf-8")
    return a, b


def test_bundle_concat_with_provenance_markers(tmp_path):
    a, b = _stage(tmp_path)
    out = tmp_path / "bundle.md"
    rc, data, err = _run_bundle(out, [a, b])
    assert rc == 0, err
    assert data == {"bundle_path": str(out), "file_count": 2}
    text = out.read_text()
    # one provenance marker per staged file, naming the exact supplied path
    assert f"===== FILE: {a} =====" in text
    assert f"===== FILE: {b} =====" in text
    # content rides verbatim under its marker (line 1 of the section == source line 1)
    assert "version: 1\nname: foo\n" in text
    # the WORKFLOW marker precedes the profiles marker (supplied order preserved)
    assert text.index(str(a)) < text.index(str(b))


def test_bundle_append_grounding_after_staged(tmp_path):
    a, b = _stage(tmp_path)
    idx = tmp_path / "catalog-index.json"
    idx.write_text('[{"name":"bar","kind":"workflow"}]\n', encoding="utf-8")
    out = tmp_path / "xref.md"
    rc, data, err = _run_bundle(out, [a, b], appends=[idx])
    assert rc == 0, err
    # file_count counts STAGED files only; the appended index is grounding, not a draft file
    assert data["file_count"] == 2
    text = out.read_text()
    assert f"===== FILE: {idx} =====" in text
    # the appended index comes AFTER both staged files
    assert text.index(str(idx)) > text.index(str(b))


def test_bundle_byte_deterministic(tmp_path):
    a, b = _stage(tmp_path)
    o1, o2 = tmp_path / "b1.md", tmp_path / "b2.md"
    _run_bundle(o1, [a, b])
    _run_bundle(o2, [a, b])
    assert o1.read_bytes() == o2.read_bytes()


def test_bundle_missing_file_exits_2(tmp_path):
    out = tmp_path / "x.md"
    rc, _, err = _run_bundle(out, [tmp_path / "nope.yaml"])
    assert rc == 2
    assert json.loads(err)["error"].startswith("cannot read input file")
    assert not out.exists()


def test_bundle_requires_at_least_one_file(tmp_path):
    # argparse nargs="+" → no positional files is a usage error (exit 2)
    p = subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path / "x.md")],
                       capture_output=True, text=True)
    assert p.returncode == 2


# ───────────────────────────── bundle-draft microskill ──────────────────────────

def _resolve(skill, profile="base"):
    p = subprocess.run(
        [sys.executable, str(RESOLVE), skill, "--profile", profile, "--skill-root", str(MS_ROOT)],
        capture_output=True, text=True, env=_ENV)
    data = json.loads(p.stdout) if p.stdout.strip() else None
    return p.returncode, data, p.stderr


def test_bundle_draft_microskill_passes_floor():
    p = subprocess.run(
        [sys.executable, str(VALIDATE_MS),
         str(MS_ROOT / "bundle-draft" / "MICROSKILL.md"),
         str(MS_ROOT / "bundle-draft" / "profiles" / "base.yaml")],
        capture_output=True, text=True, env=_ENV)
    data = json.loads(p.stdout)
    assert data["pass"] is True, data["issues"]


def test_bundle_draft_resolves_base():
    rc, data, err = _resolve("bundle-draft")
    assert rc == 0 and data, err
    schema = data["output_schema"]
    assert set(schema["required"]) == {"bundle_path", "file_count"}
    assert data["directives"]["allowed_tools"] == ["Bash"]
    assert data["directives"]["model"] == "haiku"


def test_bundle_draft_is_base_tagged():
    fm = yaml.safe_load((MS_ROOT / "bundle-draft" / "MICROSKILL.md").read_text().split("---", 2)[1])
    assert fm.get("base") is True


# ───────────────────────────── the two combined profiles ────────────────────────

def _raw(path):
    return yaml.safe_load(path.read_text())


def test_wf_create_all_is_the_union_of_wf_and_cross_keys():
    allk = set(_raw(MS_ROOT / "collect-findings" / "profiles" / "wf-create-all.yaml")["inputs"])
    wf = set(_raw(MS_ROOT / "collect-findings" / "profiles" / "wf-create.yaml")["inputs"])
    cross = set(_raw(MS_ROOT / "collect-findings" / "profiles" / "cross-create.yaml")["inputs"])
    # exactly the union of the shipped wf-create + cross-create key sets — 10 keys,
    # = the fan-out item names with '-' -> '_' (the inputs_each desugar contract)
    assert allk == wf | cross
    assert allk == {d.replace("-", "_") for d in WF_DIMS + CROSS_DIMS}
    assert len(allk) == 10


def test_wf_create_all_resolves():
    rc, data, err = _resolve("collect-findings", "wf-create-all")
    assert rc == 0 and data, err
    # collect-findings overlays only register input names; the {findings,count}
    # schema must stay inherited from base (a replaced schema would break the join)
    assert set(data["output_schema"]["required"]) == {"findings", "count"}


def test_workflow_draft_xref_profile():
    doc = _raw(MS_ROOT / "verify-finding" / "profiles" / "workflow-draft-xref.yaml")
    # var-only overlay: no forbidden inputs / runtime / output_schema keys
    assert set(doc) <= {"version", "vars"}
    ak = doc["vars"]["artifact_kind"]
    assert "workflow draft bundle" in ak and "catalog index" in ak and "concatenated" in ak
    rc, data, err = _resolve("verify-finding", "workflow-draft-xref")
    assert rc == 0 and data, err
    assert ak in json.dumps(data)  # artifact_kind substituted into the rendered body


# ───────────────────────────── the rewired def, end to end ──────────────────────

def _validate_bwfp():
    p = subprocess.run(
        [sys.executable, str(VALIDATE_WF), str(BWFP / "WORKFLOW.yaml"),
         str(BWFP / "profiles" / "base.yaml"), "--defs-root", str(DEFS)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    return json.loads(p.stdout) if p.stdout.strip() else None, p.stderr


def _compile_bwfp(check=False):
    cmd = [sys.executable, str(COMPILE_WF), "build-workflow-from-plan", "--defs-root", str(DEFS)]
    if check:
        cmd.append("--check")
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(p.stdout) if p.stdout.strip().startswith("{") else None
    return p.returncode, data, p.stdout + p.stderr


def test_bwfp_validates_clean():
    data, err = _validate_bwfp()
    assert data and data["pass"] is True, (data, err)
    assert [i for i in data["issues"] if i["severity"] == "block"] == []


def test_bwfp_compiles_one_rvs_segment():
    rc, data, out = _compile_bwfp()
    assert rc == 0, out
    # ONE background segment (the whole RVS loop body) + the on_exhaust gate +
    # finalize orchestrator node — finalize is the ONLY checkpoint.
    assert data["segments"] == 1
    assert data["sequence"][1:] == ["gate", "orchestrator_node"]
    seg = data["sequence"][0]
    for nid in ["implement", "catalog_index", "bundle", "bundle_xref", "floor",
                "collect", "verify", "synth"] + \
               [f"review_{d.replace('-', '_')}" for d in WF_DIMS] + \
               [f"xreview_{d.replace('-', '_')}" for d in CROSS_DIMS]:
        assert nid in seg, f"{nid} not in loop segment: {seg}"


def _seg_js():
    rc, data, out = _compile_bwfp()
    assert rc == 0, out
    seg = next(BWFP.glob(".compiled/seg-*.js"))
    return seg.read_text()


def test_bwfp_loop_fans_out_review_and_verify():
    js = _seg_js()
    # the loop is a do/while
    assert "do {" in js and "} while (" in js
    # all 10 review dimensions land in ONE destructured parallel([...]) batch
    # (same rank → concurrent), with the sub-PR-1 leading-';' ASI guard
    batch = ";[" + ", ".join(REVIEW_NODE_VARS) + "] = await parallel(["
    assert batch in js, "review dimensions not emitted as one parallel rank"
    # the for_each verify runs INSIDE the loop as a parallelChunked fan-out
    # (max_parallel: 4) — the sub-PR-1 for_each-in-loop unblock, host-inline
    assert "n_verify = await parallelChunked(((n_collect.findings) || []).map(" in js
    # floor findings bypass verify and feed synth directly
    assert '"floor_findings": n_floor.findings' in js
    # the LOAD-BEARING convergence contract + carry (the RVS only stops on the
    # synth verdict or the cap; the surviving findings seed the next implement) —
    # asserted explicitly so a regression that broke the exit/carry can't pass
    assert "} while ((!(n_synth.verdict == 'approve')) && __iter < __max)" in js
    assert "carry_findings = n_synth.findings" in js


def test_bwfp_finalize_prompt_refs_synth_not_evaluate():
    """The shared finalize-protocol snippet was vars-ified (finalize_verdict_ref /
    finalize_issues_ref); this def points them at the RVS synth. Guard the resolved
    prompt: it must reference the synth output (not the retired evaluate node) with
    NO leftover {{...}} token (a prose drift in the snippet would break this)."""
    rc, _, out = _compile_bwfp()
    assert rc == 0, out
    manifest = json.loads((BWFP / ".compiled" / "manifest.json").read_text())
    prompts = [s["prompt"] for s in manifest["steps"]
               if isinstance(s, dict) and isinstance(s.get("prompt"), str)
               and "Finalize by vendoring" in s["prompt"]]
    assert len(prompts) == 1, "expected exactly one finalize checkpoint prompt"
    p = prompts[0]
    assert "${synth.output.verdict}" in p and "${synth.output.findings}" in p
    assert "${evaluate.output" not in p          # task-evaluate retired (D3)
    assert "{{" not in p and "}}" not in p        # every snippet/var token resolved


def test_bundle_unwritable_out_exits_2(tmp_path):
    # the SECOND exit-2 branch (an unwritable out path) — a stated contract in both
    # the script docstring and the microskill's Failure modes; covered so a
    # regression that swallowed the write error can't pass green.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses filesystem permissions")
    a, _ = _stage(tmp_path)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)  # read+execute, no write
    try:
        rc, _, err = _run_bundle(ro / "bundle.md", [a])
    finally:
        ro.chmod(0o700)  # restore so tmp_path cleanup can remove it
    assert rc == 2
    assert json.loads(err)["error"].startswith("cannot write bundle to")


def test_bwfp_segment_parses_under_node_check():
    """The load-bearing parse gate (sub-PR 1: a substring-only suite shipped a
    SyntaxError). Wrap the seg body in an async function and `node --check`."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    body = _seg_js().replace("export const meta", "const meta", 1)
    wrapped = "(async function(args){\n" + body + "\n})\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(wrapped)
        path = f.name
    try:
        p = subprocess.run([node, "--check", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert p.returncode == 0, "emitted segment failed node --check:\n" + p.stderr


def test_bwfp_retires_task_evaluate():
    raw = (BWFP / "WORKFLOW.yaml").read_text()
    assert "task-evaluate" not in raw  # D3 retire — no use: task-evaluate survives
    assert "task-implement" in raw     # implement stays


def test_bwfp_closure_lock_in_sync():
    # the re-baselined closure.lock.json must match the rewired def (guards against
    # forgetting to commit the re-baseline — the closure-drift CI gate, scoped)
    rc, data, out = _compile_bwfp(check=True)
    assert rc == 0 and data and data.get("check") == "ok", out


def test_bwfp_import_set_is_base_closed():
    """build-workflow-from-plan is base:true; a fresh consumer seeding the base set
    must get every RVS dependency, so each imported microskill is base-tagged."""
    wf = yaml.safe_load((BWFP / "WORKFLOW.yaml").read_text())
    assert wf.get("base") is True
    for imp in wf["imports"]:
        assert imp.startswith("microskills/")
        name = imp.split("/", 1)[1]
        fm = yaml.safe_load((MS_ROOT / name / "MICROSKILL.md").read_text().split("---", 2)[1])
        assert fm.get("base") is True, f"import {name} is not base-tagged (base-closure break)"
