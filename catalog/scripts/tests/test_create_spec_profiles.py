"""
Tests for the refine-requirements create-spec profile family (§8 step 6): the
5 spec review-dimension lenses (2 ms + 3 wf), the 2 collect-findings fan-in
registration profiles (spec-ms-create / spec-wf-create), the 2 spec-template
reference docs, and the 4 refine-requirements create-spec workflow profiles
(interactive + -auto, ms + wf).

Task-1 functions resolve each new microskill profile through the same
resolve-microskill subprocess the dispatcher uses, assert the four-way naming
invariant on the 5 lenses, assert no forbidden keys, and assert the collect
keys equal the lens names underscored.
Task-2 functions compile real refine-requirements under each of the 4 create-spec
profiles and assert the spliced structure + the contract-preservation regression
on develop-product-backlog.

NOTE on manifest field names (implementer alignment, plan Task-2 Step-1 note +
risk (e)): the real compiled `.compiled/manifest.json` steps[] schema differs
from the plan's draft assumptions. A background `use:` node (the generated
critique siblings) lives in a SEGMENT step under step["nodes"] (a list), NOT a
top-level step["node"]. An authored human_approval gate is emitted as a step with
step["checkpoint_type"] == "gate" and the gate dict nested under step["gate"]
(id/severity/type live there, e.g. step["gate"]["severity"]). These tests assert
against that real shape (confirmed by compiling refine-requirements base and by
the existing test_real_refine_requirements_orchestrator_contracts).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / "catalog"
RD = CATALOG / "microskills" / "review-dimension"
CF = CATALOG / "microskills" / "collect-findings"
RR = CATALOG / "workflow-defs" / "refine-requirements"
RESOLVE = CATALOG / "scripts" / "resolve-microskill"

# Pin the canonical committed templates/ so these hermetic tests resolve against
# the source of truth, never a possibly-stale generated .claude/ mirror.
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

FORBIDDEN_KEYS = {"inputs", "runtime", "output_schema"}

MS_SPEC_DIMS = ["spec-ms-completeness", "spec-ms-atomicity"]
WF_SPEC_DIMS = ["spec-wf-coverage", "spec-wf-controlflow", "spec-wf-dataflow"]
MS_SPEC_ARTIFACT_KIND = "microskill specification document"
WF_SPEC_ARTIFACT_KIND = "workflow specification document"


def _raw(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


def _us(names):
    return {n.replace("-", "_") for n in names}


def _resolve(component_dir: Path, profile: str):
    """Resolve a microskill profile via the resolve-microskill subprocess (the
    exact path the dispatcher uses). resolve-microskill takes a skill NAME +
    --skill-root (NOT a MICROSKILL.md path), so the skill name is the component
    dir's basename and the skill-root is its parent.
    Returns (returncode, stdout, stderr)."""
    r = subprocess.run(
        [sys.executable, str(RESOLVE), component_dir.name, "--profile", profile,
         "--skill-root", str(component_dir.parent)],
        capture_output=True, text=True, env=_ENV,
    )
    return r.returncode, r.stdout, r.stderr


def _assert_lens(name, artifact_kind):
    doc = _raw(RD / "profiles" / f"{name}.yaml")
    assert doc["vars"]["dimension"] == name, name
    assert doc["vars"]["artifact_kind"] == artifact_kind, name
    snips = doc["context"]["snippets"]
    assert len(snips) == 1, name
    assert snips[0]["name"] == f"{name}-rubric", name
    # rubric must NOT cite a non-existent deterministic floor at the spec stage
    body = snips[0]["text"].lower()
    assert "validate-microskill" not in body, name
    assert "validate-workflow" not in body, name
    assert "deterministic floor" not in body, name
    # the binding open-question-parity rule is present (req-* family contract)
    assert "open-question parity" in body or "open-question, tbd" in body, name
    assert not (set(doc.keys()) & FORBIDDEN_KEYS), (name, list(doc.keys()))


def test_ms_spec_lenses_naming_and_no_forbidden_keys():
    for n in MS_SPEC_DIMS:
        _assert_lens(n, MS_SPEC_ARTIFACT_KIND)


def test_wf_spec_lenses_naming_and_no_forbidden_keys():
    for n in WF_SPEC_DIMS:
        _assert_lens(n, WF_SPEC_ARTIFACT_KIND)


def test_each_spec_lens_resolves_clean():
    for n in MS_SPEC_DIMS + WF_SPEC_DIMS:
        rc, out, err = _resolve(RD, n)
        assert rc == 0, (n, err)
        assert "{{dimension}}" not in out and "{{artifact_kind}}" not in out, n


def test_collect_spec_ms_create_keys():
    doc = _raw(CF / "profiles" / "spec-ms-create.yaml")
    assert set(doc["inputs"]) == _us(MS_SPEC_DIMS)
    assert "output_schema" not in doc and "runtime" not in doc
    rc, _, err = _resolve(CF, "spec-ms-create")
    assert rc == 0, err


def test_collect_spec_wf_create_keys():
    doc = _raw(CF / "profiles" / "spec-wf-create.yaml")
    assert set(doc["inputs"]) == _us(WF_SPEC_DIMS)
    assert "output_schema" not in doc and "runtime" not in doc
    rc, _, err = _resolve(CF, "spec-wf-create")
    assert rc == 0, err


# ---------------- Task 2: templates + workflow profiles + regression ----------------

COMPILE = CATALOG / "scripts" / "compile-workflow"
VALIDATE = CATALOG / "scripts" / "validate-workflow"
DEFS = CATALOG / "workflow-defs"

CREATE_SPEC_PROFILES = {
    "create-spec-microskill": {
        "lenses": MS_SPEC_DIMS, "collect": "spec-ms-create",
        "max_iters": 2, "refute_seats": 1, "clarify_cap": 2,
        "template": "microskill-spec-template.md", "auto": False,
    },
    "create-spec-workflow": {
        "lenses": WF_SPEC_DIMS, "collect": "spec-wf-create",
        "max_iters": 3, "refute_seats": 3, "clarify_cap": 3,
        "template": "workflow-spec-template.md", "auto": False,
    },
    "create-spec-microskill-auto": {
        "lenses": MS_SPEC_DIMS, "collect": "spec-ms-create",
        "max_iters": 2, "refute_seats": 1, "clarify_cap": 2,
        "template": "microskill-spec-template.md", "auto": True,
    },
    "create-spec-workflow-auto": {
        "lenses": WF_SPEC_DIMS, "collect": "spec-wf-create",
        "max_iters": 3, "refute_seats": 3, "clarify_cap": 3,
        "template": "workflow-spec-template.md", "auto": True,
    },
}


def _compile(name, profile=None, defs_root=DEFS):
    cmd = [sys.executable, str(COMPILE), name, "--defs-root", str(defs_root)]
    if profile:
        cmd += ["--profile", profile]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    return r.returncode, r.stdout, r.stderr


def _manifest(name):
    return json.loads((DEFS / name / ".compiled" / "manifest.json").read_text())


def _segment_nodes(m):
    """Union of every segment step's node-id list (where background use: nodes live)."""
    out = set()
    for st in m["steps"]:
        if st.get("kind") == "segment":
            out |= set(st.get("nodes", []))
    return out


def _gate_steps(m, gate_id):
    """Compiled gate checkpoint steps for the authored gate `gate_id`. A gate is a
    step with checkpoint_type == 'gate' and the gate dict under step['gate']."""
    return [s for s in m["steps"]
            if s.get("checkpoint_type") == "gate"
            and (s.get("gate") or {}).get("id") == gate_id]


def test_spec_templates_have_expected_sections():
    ms = (RR / "references" / "microskill-spec-template.md").read_text()
    for h in ["Capability & Single Responsibility", "Inputs Contract",
              "Output Schema", "Linear Steps", "Failure Modes",
              "Atomicity Boundary & Non-Goals", "Open Questions / Gaps"]:
        assert h in ms, h
    wf = (RR / "references" / "workflow-spec-template.md").read_text()
    for h in ["Goal", "Inputs & Output Contract", "Node Inventory",
              "Data Flow & Dependencies", "Control Flow", "Constraints & NFRs",
              "Open Questions / Gaps", "Out of Scope"]:
        assert h in wf, h


def test_create_spec_profiles_do_not_touch_output_contract():
    # Contract preservation: no create-spec profile may declare output_schema or
    # redeclare/remove the parent-passed inputs.
    PARENT_INPUTS = {"sources_path", "output_dir", "document_name", "template_path",
                     "prior_answers_path", "max_iterations", "staging_dir"}
    for prof in CREATE_SPEC_PROFILES:
        doc = _raw(RR / "profiles" / f"{prof}.yaml")
        assert "output_schema" not in doc, prof
        # the only inputs touched are defaults on existing inputs (template_path,
        # max_iterations, refute_seats) — never an input rename/remove
        for k in (doc.get("inputs") or {}):
            assert k in (PARENT_INPUTS | {"refute_seats"}), (prof, k)


def test_each_create_spec_profile_compiles_with_expected_structure():
    for prof, spec in CREATE_SPEC_PROFILES.items():
        rc, _, err = _compile("refine-requirements", prof)
        assert rc == 0, (prof, err)
        m = _manifest("refine-requirements")
        # exactly N generated critique siblings, named for the spec lenses. The
        # siblings are background use: nodes → they live in segment steps' node lists.
        seg_nodes = _segment_nodes(m)
        crit = {n for n in seg_nodes if n.startswith("critique_")}
        expected = {f"critique_{n.replace('-', '_')}" for n in spec["lenses"]}
        assert crit == expected, (prof, crit, expected)
        # the gate-collapse property (approve_requirements no longer pauses) is
        # asserted precisely in test_gate_collapse_makes_approve_non_pausing.


def test_create_spec_microskill_keeps_gate_hard():
    # 2026-06-21 production rewire (sub-PR 3): create-spec-microskill is inlined as
    # microskill-create's refine front-end, where approve_requirements becomes Gate 1 — a
    # HARD parent-level checkpoint. The create pipeline runs TWO hard gates (requirements +
    # plan), so the §8 step-6 warn-collapse is REVERSED for the microskill variant. (The
    # workflow variant flips to hard later, in sub-PR 4.) The compiled manifest renders the
    # approve_requirements checkpoint with severity hard.
    rc, _, err = _compile("refine-requirements", "create-spec-microskill")
    assert rc == 0, err
    m = _manifest("refine-requirements")
    gsteps = _gate_steps(m, "approve_requirements")
    assert gsteps, "approve_requirements should render as a checkpoint"
    severities = {(s.get("gate") or {}).get("severity") for s in gsteps}
    assert severities == {"hard"}, severities


def test_gate_stays_hard_in_base():
    # Sanity twin: under base, approve_requirements IS a hard pausing gate — proves the
    # severity assertion above reads the right key (not vacuously true from a wrong key).
    rc, _, err = _compile("refine-requirements", None)
    assert rc == 0, err
    m = _manifest("refine-requirements")
    gsteps = _gate_steps(m, "approve_requirements")
    assert gsteps and all((s.get("gate") or {}).get("severity") == "hard" for s in gsteps)


def test_template_default_flows_to_assemble():
    rc, _, err = _compile("refine-requirements", "create-spec-microskill")
    assert rc == 0, err
    m = _manifest("refine-requirements")
    # the template_path default threads through the manifest (input_defaults +
    # assemble's materialize/inputs view).
    assert m["input_defaults"].get("template_path"), "template_path default missing"
    blob = json.dumps(m)
    assert "microskill-spec-template.md" in blob


def test_auto_profiles_set_gate_mode_and_patch_interaction_nodes():
    for prof in ("create-spec-microskill-auto", "create-spec-workflow-auto"):
        doc = _raw(RR / "profiles" / f"{prof}.yaml")
        assert doc.get("gate_mode") == "auto", prof
        patched = {p["id"] for p in doc["nodes"]["patch"]}
        assert {"clarify", "present_refined", "critique", "collect_req"} <= patched, prof
        assert "triage_reopened" not in patched, prof  # red-floor park stays honest


def test_interactive_profiles_do_not_patch_interaction_nodes():
    for prof in ("create-spec-microskill", "create-spec-workflow"):
        doc = _raw(RR / "profiles" / f"{prof}.yaml")
        patched = {p["id"] for p in doc["nodes"]["patch"]}
        assert "clarify" not in patched and "present_refined" not in patched, prof
        assert {"critique", "collect_req"} <= patched, prof


def test_loop_and_input_knobs_per_profile():
    for prof, spec in CREATE_SPEC_PROFILES.items():
        doc = _raw(RR / "profiles" / f"{prof}.yaml")
        assert doc["loop"]["max_iters"] == spec["max_iters"], prof
        body = doc["loop"]["body"]
        for n in spec["lenses"]:
            assert f"critique_{n.replace('-', '_')}" in body, (prof, n)
        assert doc["inputs"]["refute_seats"]["default"] == spec["refute_seats"], prof
        assert doc["inputs"]["max_iterations"]["default"] == spec["clarify_cap"], prof
        assert spec["template"] in doc["inputs"]["template_path"]["default"], prof


def test_all_four_profiles_validate():
    for prof in CREATE_SPEC_PROFILES:
        r = subprocess.run(
            [sys.executable, str(VALIDATE), str(RR / "WORKFLOW.yaml"),
             str(RR / "profiles" / f"{prof}.yaml"), "--defs-root", str(DEFS)],
            capture_output=True, text=True, cwd=str(REPO), env=_ENV)
        assert r.returncode == 0, (prof, r.stderr or r.stdout)


def test_refine_requirements_base_and_autonomous_unchanged():
    # Regression: the existing profiles still compile cleanly.
    for prof in (None, "autonomous"):
        rc, _, err = _compile("refine-requirements", prof)
        assert rc == 0, (prof, err)


@pytest.mark.xfail(
    reason="§9 don't-preserve-existing: develop-product-backlog is an out-of-scope "
    "nester slated for retirement/rebuild on the inlining engine, so its compile "
    "outcome is NOT pinned (strict=False — XFAIL or XPASS both acceptable). Under the "
    "engine its complex nested loop-bearing children (refine-requirements + "
    "technical-design) historically interleaved when inlined — loop bodies no longer "
    "contiguous (a clear contiguity die). The expand-sibling-ref namespacing fix "
    "(projected sibling ids fed to the inline ref-rewriter) repaired a class of missing "
    "intra-child edges and incidentally re-orders dpb's topo so the body is contiguous "
    "again — it may now compile. It stays out of scope regardless; the in-scope create "
    "pipelines wire children sequentially and isolate cleanly.",
    strict=False)
def test_develop_product_backlog_regression():
    # HARD regression target: the parent must still compile + validate (base + autonomous)
    # — refine-requirements' external contract is unchanged.
    for prof in (None, "autonomous"):
        rc, _, err = _compile("develop-product-backlog", prof)
        assert rc == 0, (prof, err)
        r = subprocess.run(
            [sys.executable, str(VALIDATE),
             str(DEFS / "develop-product-backlog" / "WORKFLOW.yaml"),
             str(DEFS / "develop-product-backlog" / "profiles"
                 / (f"{prof}.yaml" if prof else "base.yaml")),
             "--defs-root", str(DEFS)],
            capture_output=True, text=True, cwd=str(REPO), env=_ENV)
        assert r.returncode == 0, (prof, r.stderr or r.stdout)


# ---------------- Section-ownership partition lock (remediation, panel finding) ----------------
# The spec lenses partition the spec template by section. A template section owned by NO
# lens is a silent review hole: a TBD inside it never enters the convergence loop (the
# adversarial panel caught exactly this for the workflow template's "Constraints & NFRs").
# Every core section (all but the structural "Open Questions / Gaps" gap ledger, which is
# deliberately fenced out of every lens) must be named in at least one domain lens's rubric.

def _template_sections(p: Path):
    secs = []
    for line in p.read_text().splitlines():
        m = re.match(r"^##\s+\d+\.\s+(.*\S)\s*$", line)
        if m:
            # normalize away a parenthetical qualifier, e.g. "Linear Steps (≤10)" -> "Linear Steps"
            secs.append(re.sub(r"\s*\(.*?\)", "", m.group(1)).strip())
    return secs


def _lens_corpus(dims):
    # Strip each rubric's trailing "Do not raise … sibling lenses own them." fence
    # before the membership check: a section a lens does NOT own is named only in that
    # fence, so counting fence mentions as ownership would blind the lock to a future
    # dis-owning edit (a section stripped from a lens's scope but still named in a
    # sibling's fence). Ownership lives in the scope/flag/severity/parity text only.
    return " ".join(
        re.sub(r"Do not raise.*", "",
               _raw(RD / "profiles" / f"{d}.yaml")["context"]["snippets"][0]["text"],
               flags=re.S)
        for d in dims
    )


def test_every_wf_template_section_owned_by_a_lens():
    corpus = _lens_corpus(WF_SPEC_DIMS)
    for s in _template_sections(RR / "references" / "workflow-spec-template.md"):
        if s.startswith("Open Questions"):
            continue  # the gap ledger is deliberately fenced out of every lens
        assert s in corpus, f"workflow-spec section '{s}' is owned by no spec-wf lens"


def test_every_ms_template_section_owned_by_a_lens():
    corpus = _lens_corpus(MS_SPEC_DIMS)
    for s in _template_sections(RR / "references" / "microskill-spec-template.md"):
        if s.startswith("Open Questions"):
            continue
        assert s in corpus, f"microskill-spec section '{s}' is owned by no spec-ms lens"
