"""
Coverage for the bundle-draft script + microskill and the two combined RVS
profiles it wires (`collect-findings/wf-create-all`,
`verify-finding/workflow-draft-xref`).

These tests were split out of the retired test_rewire_build_workflow_from_plan.py
(2026-06-21 production rewire). The BWFP-specific group (test_bwfp_*) was retired
with build-workflow-from-plan; THIS group covers components that REMAIN live and
in-scope for the create pipelines:

  * the bundle-draft SCRIPT — concatenates staged files with per-file provenance
    markers, appends grounding, is byte-deterministic, and fails loud (exit 2);
  * the bundle-draft MICROSKILL — passes the floor, resolves its base profile,
    and is base-tagged. bundle-draft is consumed by implement-rvs
    (catalog/workflow-defs/implement-rvs/WORKFLOW.yaml: the `bundle`/`bundle_xref`
    nodes);
  * the combined profiles — `wf-create-all` registers exactly the 7 wf + 3 cross
    fan-in keys (the union of the shipped wf-create + cross-create profiles), and
    `workflow-draft-xref` swaps only artifact_kind to the bundle+index phrasing,
    no forbidden overlay keys.

Hermetic where it can be; the resolve/validate tests intentionally point at the
real catalog.

Run: python3 -m pytest catalog/scripts/tests/test_bundle_draft.py -v
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / "catalog"
MS_ROOT = CATALOG / "microskills"
SCRIPT = CATALOG / "scripts" / "bundle-draft"
RESOLVE = CATALOG / "scripts" / "resolve-microskill"
VALIDATE_MS = CATALOG / "scripts" / "validate-microskill"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

# The 10 review dimensions the RVS body fans out (7 wf draft + 3 cross-artifact).
WF_DIMS = [
    "wf-dag-decomposition-correctness", "wf-guard-logic-intent", "wf-prompt-task-fidelity",
    "wf-gate-prompt-options-quality", "wf-profile-overlay-coherence",
    "wf-spill-materialize-judgment", "wf-output-schema-downstream-fit",
]
CROSS_DIMS = ["duplicate-capability", "naming-collision", "reverse-consumer"]


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
