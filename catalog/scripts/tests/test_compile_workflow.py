"""
Tests for compile-workflow. Run: python3 -m pytest .claude/scripts/tests/ -v

Hermetic fixtures use --defs-root pointing at tmp_path; one end-to-end test
compiles the real microskill-create.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "compile-workflow"
# REAL_DEFS stays the runtime: compile writes .compiled/ here and resolves the use:
# microskills it references from .claude/microskills (both require a dogfood init first).
REAL_DEFS = REPO / ".claude" / "workflow-defs"
# Pin the in-repo source schema so tests exercise the committed templates/ rather
# than a possibly-stale .claude/templates materialized by an earlier reconcile.
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}


def run(defs_root, name, *args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), name, "--defs-root", str(defs_root), *args],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def make_flow(defs_root, name, wf_yaml, base="version: 1\n"):
    d = defs_root / name
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(wf_yaml)
    (d / "profiles" / "base.yaml").write_text(base)
    return d


LINEAR = """\
version: 1
name: linear-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.x}
"""

GATED = LINEAR + """\
gates:
  - id: g1
    after: a
    type: human_approval
    prompt: approve a?
    options: [approve, abandon]
"""

ORCH = """\
version: 1
name: orch-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: fin
    delegation: orchestrator
    depends_on: [a]
    prompt: finalize using ${a.output.x}
"""


def test_linear_single_segment(tmp_path):
    make_flow(tmp_path, "linear-flow", LINEAR)
    rc, data, out, err = run(tmp_path, "linear-flow")
    assert rc == 0, err
    assert data["segments"] == 1
    assert data["checkpoints"] == 0


def test_gate_splits_segments(tmp_path):
    make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow")
    assert rc == 0, err
    assert data["segments"] == 2
    assert data["checkpoints"] == 1
    assert data["sequence"] == ["segment[a]", "gate", "segment[b]"]


def test_orchestrator_node_is_checkpoint(tmp_path):
    make_flow(tmp_path, "orch-flow", ORCH)
    rc, data, out, err = run(tmp_path, "orch-flow")
    assert rc == 0, err
    assert data["sequence"] == ["segment[a]", "orchestrator_node"]


def test_emitted_js_has_guard_and_runtime(tmp_path):
    d = make_flow(tmp_path, "linear-flow", LINEAR)
    run(tmp_path, "linear-flow")
    seg1 = (d / ".compiled" / "seg-1.js").read_text()
    assert "JSON.parse(_args)" in seg1          # args JSON-string guard
    assert "async function runAgent" in seg1    # node runtime present
    assert "export const meta" in seg1          # native Workflow shape


def test_deterministic(tmp_path):
    d = make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    run(tmp_path, "gated-flow")
    first = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    run(tmp_path, "gated-flow")
    second = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    assert first == second


def test_schema_violation_blocks(tmp_path):
    make_flow(tmp_path, "bad-flow", "version: 1\nname: bad-flow\n")  # no nodes
    rc, data, out, err = run(tmp_path, "bad-flow")
    assert rc == 1
    assert "schema_errors" in data


WHENF = """\
version: 1
name: when-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: b
    agent: ag
    when: ${a.output.ok}
    depends_on: [a]
    prompt: use ${a.output.ok}
"""

FOREACH = """\
version: 1
name: fe-flow
inputs:
  items:
    type: array
    required: true
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
"""

PLAIN_LOOP = """\
version: 1
name: pl-flow
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: impl
    agent: ag
    depends_on: [p]
    prompt: impl
  - id: ev
    agent: ag
    depends_on: [impl]
    prompt: ev
gates:
  - id: g
    after: p
    type: human_approval
    prompt: ok?
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
  carry:
    last: ${ev.output}
"""

GUARDED_LOOP = """\
version: 1
name: gl-flow
nodes:
  - id: p
    agent: ag
    prompt: plan
    output_schema:
      type: object
      properties:
        adv: { type: ["object", "null"] }
  - id: impl
    agent: ag
    when: ${p.output.adv == null}
    depends_on: [p]
    prompt: impl
  - id: ev
    agent: ag
    when: ${p.output.adv == null}
    depends_on: [impl, p]
    prompt: ev
gates:
  - id: g
    after: p
    type: human_approval
    prompt: ok?
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
  carry:
    last: ${ev.output}
"""


def test_when_emits_ternary(tmp_path):
    d = make_flow(tmp_path, "when-flow", WHENF)
    rc, data, out, err = run(tmp_path, "when-flow")
    assert rc == 0, err
    seg1 = (d / ".compiled" / "seg-1.js").read_text()
    assert "(n_a.ok) ? await runAgent" in seg1
    assert ": null" in seg1


def test_for_each_emits_parallel(tmp_path):
    d = make_flow(tmp_path, "fe-flow", FOREACH)
    rc, data, out, err = run(tmp_path, "fe-flow")
    assert rc == 0, err
    seg1 = (d / ".compiled" / "seg-1.js").read_text()
    assert "await parallel(" in seg1
    assert ".map((item) => () => runAgent" in seg1
    assert "${__ref(item)}" in seg1


def test_plain_loop_has_no_ran_flag(tmp_path):
    d = make_flow(tmp_path, "pl-flow", PLAIN_LOOP)
    rc, data, out, err = run(tmp_path, "pl-flow")
    assert rc == 0, err
    seg2 = (d / ".compiled" / "seg-2.js").read_text()
    assert "__ran" not in seg2          # unguarded loop output unchanged


def test_guarded_loop_has_ran_and_break(tmp_path):
    d = make_flow(tmp_path, "gl-flow", GUARDED_LOOP)
    rc, data, out, err = run(tmp_path, "gl-flow")
    assert rc == 0, err
    seg2 = (d / ".compiled" / "seg-2.js").read_text()
    assert "let __ran = false" in seg2
    assert "if (!__ran) break" in seg2


def test_input_named_output_does_not_pollute_needs_nodes(tmp_path):
    # Regression: a ${workflow.inputs.output_path} ref inside a segment node must
    # NOT make the needs-scan match `inputs.output` and add a bogus `inputs` node.
    body = """\
version: 1
name: io-flow
inputs:
  output_path: { type: string, required: true }
nodes:
  - id: a
    agent: ag
    inputs:
      out: ${workflow.inputs.output_path}
    prompt: write to ${workflow.inputs.output_path}
"""
    make_flow(tmp_path, "io-flow", body)
    rc, data, out, err = run(tmp_path, "io-flow")
    assert rc == 0, err
    m = json.loads((tmp_path / "io-flow" / ".compiled" / "manifest.json").read_text())
    needs = m["steps"][0]["needs"]
    assert needs["nodes"] == []                 # no bogus 'inputs' node
    assert "output_path" in needs["wf_inputs"]


def test_real_workflow_create_compiles():
    # The implement/evaluate loop moved out into build-workflow-from-plan, so
    # workflow-create is now a single background segment (plan) followed by three
    # checkpoints: `provision` (nested workflow: microskill-create, autonomous
    # profile, for_each over missing microskills), `advise` (orchestrator node),
    # and `build` (nested workflow: build-workflow-from-plan).
    rc, data, out, err = run(REAL_DEFS, "workflow-create")
    assert rc == 0, err
    assert data["segments"] == 1
    assert data["sequence"] == [
        "segment[plan]", "gate", "nested_workflow", "orchestrator_node",
        "nested_workflow"]


def test_real_build_workflow_from_plan_compiles():
    # The shared build half extracted from workflow-create: the implement/evaluate
    # loop segment followed by the canonical finalize orchestrator node. No plan
    # node and no gate (those live in the caller).
    rc, data, out, err = run(REAL_DEFS, "build-workflow-from-plan")
    assert rc == 0, err
    assert data["segments"] == 1
    assert data["sequence"] == [
        "segment[implement,evaluate]", "orchestrator_node"]


# --- Nested-workflow profile passthrough -------------------------------------

NESTED_CHILD = """\
version: 1
name: child-flow
nodes:
  - id: c
    agent: ag
    prompt: child do
output:
  from: c
"""

NESTED_PARENT = """\
version: 1
name: parent-flow
imports:
  - child-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: call
    workflow: child-flow
    depends_on: [a]
    customize: { profile: autonomous }
    inputs:
      x: ${a.output.x}
"""

NESTED_PARENT_PLAIN = """\
version: 1
name: parent-plain
imports:
  - child-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: call
    workflow: child-flow
    depends_on: [a]
    inputs:
      x: ${a.output.x}
"""


def _nested_step(defs_root, name):
    man = json.loads((defs_root / name / ".compiled" / "manifest.json").read_text())
    return next(s for s in man["steps"] if s.get("checkpoint_type") == "nested_workflow")


def test_nested_workflow_carries_profile(tmp_path):
    # A workflow: node with customize.profile stamps that profile into the
    # nested_workflow checkpoint so the dispatcher compiles the child with it.
    make_flow(tmp_path, "child-flow", NESTED_CHILD)
    make_flow(tmp_path, "parent-flow", NESTED_PARENT)
    rc, data, out, err = run(tmp_path, "parent-flow")
    assert rc == 0, err
    assert _nested_step(tmp_path, "parent-flow")["profile"] == "autonomous"


def test_nested_workflow_omits_profile_when_no_customize(tmp_path):
    # No customize → no profile key → checkpoint byte-identical to the old shape.
    make_flow(tmp_path, "child-flow", NESTED_CHILD)
    make_flow(tmp_path, "parent-plain", NESTED_PARENT_PLAIN)
    rc, data, out, err = run(tmp_path, "parent-plain")
    assert rc == 0, err
    assert "profile" not in _nested_step(tmp_path, "parent-plain")


def test_real_flow_segments_and_advisory_branch():
    # Retrofitted with a scope_advisory branch: an `advise` orchestrator node
    # (when scope_advisory != null) before the loop, plus the finalize node.
    rc, data, out, err = run(REAL_DEFS, "microskill-create")
    assert rc == 0, err
    assert data["segments"] == 2
    assert data["checkpoints"] == 3
    assert data["sequence"] == [
        "segment[plan]", "gate", "orchestrator_node",
        "segment[implement,evaluate]", "orchestrator_node"]


def test_real_flow_guarded_loop_breaks_when_skipped():
    # The guarded loop body must emit the __ran flag + break so the advisory
    # path doesn't read a skipped (null) node in the while condition.
    run(REAL_DEFS, "microskill-create")
    seg2 = (REAL_DEFS / "microskill-create" / ".compiled" / "seg-2.js").read_text()
    assert "let __ran = false" in seg2
    assert "if (!__ran) break" in seg2
    assert "scope_advisory == null" in seg2


def test_autonomous_profile_softens_gate():
    # The autonomous profile keeps 2 segments + the gate checkpoint (loop stays
    # separated) but the gate is warn-severity (no human pause at runtime).
    rc, data, out, err = run(REAL_DEFS, "microskill-create", "--profile", "autonomous")
    assert rc == 0, err
    assert data["segments"] == 2
    assert "gate" in data["sequence"]


def _gate_checkpoint(defs_root, name):
    man = json.loads((defs_root / name / ".compiled" / "manifest.json").read_text())
    return next(s["gate"] for s in man["steps"] if s.get("checkpoint_type") == "gate")


def test_gates_patch_verb_equals_full_restatement(tmp_path):
    # Migrating the autonomous profiles from full-gate restatement to
    # gates:{patch:[...]} must be byte-identical: patching severity+prompt by id
    # resolves to the SAME gate (after/type/options inherited from the base) as
    # restating the whole gate, and leaves the segment bytes untouched.
    wf = (
        "version: 1\n"
        "name: gate-flow\n"
        "nodes:\n"
        "  - id: a\n"
        "    agent: ag\n"
        "    prompt: do a\n"
        "gates:\n"
        "  - id: g\n"
        "    after: a\n"
        "    type: human_approval\n"
        "    severity: hard\n"
        "    prompt: Original prompt.\n"
        "    options: [approve, revise, abandon]\n"
    )
    d = tmp_path / "gate-flow"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(wf)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    # (a) full restatement of the gate, flipping severity + prompt
    (d / "profiles" / "restate.yaml").write_text(
        "version: 1\n"
        "gates:\n"
        "  - id: g\n"
        "    after: a\n"
        "    type: human_approval\n"
        "    severity: warn\n"
        "    prompt: >\n"
        "      (autonomous) Soft prompt.\n"
        "    options: [approve, revise, abandon]\n"
    )
    # (b) the same change expressed as a patch verb keyed by gate id
    (d / "profiles" / "patchverb.yaml").write_text(
        "version: 1\n"
        "gates:\n"
        "  patch:\n"
        "    - id: g\n"
        "      severity: warn\n"
        "      prompt: >\n"
        "        (autonomous) Soft prompt.\n"
    )

    def compiled(profile):
        rc, data, out, err = run(tmp_path, "gate-flow", "--profile", profile)
        assert rc == 0, err
        seg = (d / ".compiled" / "seg-1.js").read_text()
        return _gate_checkpoint(tmp_path, "gate-flow"), seg

    g_restate, seg_restate = compiled("restate")
    g_patch, seg_patch = compiled("patchverb")
    assert g_patch == g_restate            # resolved gate is identical
    assert seg_patch == seg_restate        # segment bytes untouched
    assert g_patch["severity"] == "warn"
    assert g_patch["after"] == "a"         # inherited from base (proves merge, not replace)
    assert g_patch["type"] == "human_approval"
    assert g_patch["options"] == ["approve", "revise", "abandon"]


def test_real_autonomous_gate_patch_applies():
    # The shipped autonomous profiles soften approve_plan via gates:{patch:[...]}.
    # Verify the patch merged by id on the REAL defs: severity→warn + the autonomous
    # prompt land, while after/type/options are inherited from the base gate.
    for name, domain in [("microskill-create", "microskill"), ("workflow-create", "workflow")]:
        rc, data, out, err = run(REAL_DEFS, name, "--profile", "autonomous")
        assert rc == 0, err
        gate = _gate_checkpoint(REAL_DEFS, name)
        assert gate["id"] == "approve_plan"
        assert gate["severity"] == "warn"
        assert gate["prompt"].startswith("(autonomous)")
        assert f"created {domain} afterward" in gate["prompt"]
        assert gate["after"] == "plan"
        assert gate["type"] == "human_approval"
        assert gate["options"] == ["approve", "revise", "abandon"]


# --- P1.2: profile-driven node verbs (add/patch/remove) through compile ---

def test_node_verb_add_via_profile_compiles(tmp_path):
    base = (
        "version: 1\n"
        "nodes:\n"
        "  add:\n"
        "    - id: c\n"
        "      agent: ag\n"
        "      depends_on: [b]\n"
        "      prompt: use ${b.output.y}\n"
    )
    make_flow(tmp_path, "verb-add", LINEAR.replace("name: linear-flow", "name: verb-add"), base=base)
    rc, data, out, err = run(tmp_path, "verb-add")
    assert rc == 0, err
    assert "c" in str(data["sequence"])


def test_node_verb_patch_via_profile_takes_effect(tmp_path):
    base = (
        "version: 1\n"
        "nodes:\n"
        "  patch:\n"
        "    - id: b\n"
        "      prompt: PATCHED_PROMPT ${a.output.x}\n"
    )
    d = make_flow(tmp_path, "verb-patch", LINEAR.replace("name: linear-flow", "name: verb-patch"), base=base)
    rc, data, out, err = run(tmp_path, "verb-patch")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "PATCHED_PROMPT" in seg


def test_node_verb_error_emits_clean_json(tmp_path):
    base = (
        "version: 1\n"
        "nodes:\n"
        "  patch:\n"
        "    - id: ghost\n"
        "      prompt: nope\n"
    )
    make_flow(tmp_path, "verb-err", LINEAR.replace("name: linear-flow", "name: verb-err"), base=base)
    rc, data, out, err = run(tmp_path, "verb-err")
    assert rc == 1
    assert data is not None and "list-verb error" in data["error"]
    assert "Traceback" not in err


# --- N2: first-class nested workflow node (workflow: <name>) ---

NESTED = """\
version: 1
name: parent-flow
imports: [child-flow]
inputs:
  topic:
    type: string
    required: true
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: build
    workflow: child-flow
    depends_on: [a]
    inputs:
      seed: ${a.output.x}
      topic: ${workflow.inputs.topic}
"""


def _manifest(tmp_path, name):
    return json.loads((tmp_path / name / ".compiled" / "manifest.json").read_text())


def test_nested_workflow_node_is_orchestrator_checkpoint(tmp_path):
    # A workflow: node classifies as an orchestrator checkpoint and forces a
    # split: the preceding agent node `a` is its own segment.
    make_flow(tmp_path, "parent-flow", NESTED)
    rc, data, out, err = run(tmp_path, "parent-flow")
    assert rc == 0, err
    assert data["segments"] == 1
    assert data["sequence"] == ["segment[a]", "nested_workflow"]


def test_nested_workflow_checkpoint_shape(tmp_path):
    # The emitted checkpoint carries checkpoint_type nested_workflow, the child
    # name, the node id, depends_on, and the inputs dict with ${...} refs
    # preserved verbatim (resolved later by the dispatcher).
    make_flow(tmp_path, "parent-flow", NESTED)
    rc, data, out, err = run(tmp_path, "parent-flow")
    assert rc == 0, err
    m = _manifest(tmp_path, "parent-flow")
    chk = [s for s in m["steps"] if s["kind"] == "checkpoint"][0]
    assert chk["checkpoint_type"] == "nested_workflow"
    assert chk["workflow"] == "child-flow"
    assert chk["node"] == "build"
    assert chk["depends_on"] == ["a"]
    assert chk["inputs"] == {"seed": "${a.output.x}", "topic": "${workflow.inputs.topic}"}


def test_nested_workflow_when_for_each_as_copied(tmp_path):
    body = """\
version: 1
name: fe-parent
imports: [child-flow]
inputs:
  items:
    type: array
    required: true
nodes:
  - id: gate_in
    agent: ag
    prompt: gate
  - id: fan
    workflow: child-flow
    depends_on: [gate_in]
    when: ${gate_in.output.ok}
    for_each: ${workflow.inputs.items}
    as: item
    inputs:
      one: ${item}
"""
    make_flow(tmp_path, "fe-parent", body)
    rc, data, out, err = run(tmp_path, "fe-parent")
    assert rc == 0, err
    m = _manifest(tmp_path, "fe-parent")
    chk = [s for s in m["steps"] if s["kind"] == "checkpoint"][0]
    assert chk["checkpoint_type"] == "nested_workflow"
    assert chk["when"] == "${gate_in.output.ok}"
    assert chk["for_each"] == "${workflow.inputs.items}"
    assert chk["as"] == "item"


def test_nested_workflow_deterministic(tmp_path):
    make_flow(tmp_path, "parent-flow", NESTED)
    run(tmp_path, "parent-flow")
    first = {p.name: p.read_text() for p in (tmp_path / "parent-flow" / ".compiled").iterdir()}
    run(tmp_path, "parent-flow")
    second = {p.name: p.read_text() for p in (tmp_path / "parent-flow" / ".compiled").iterdir()}
    assert first == second


# --- S-LOOP: loop-body contiguity is a pre-emit die() in compile too ---

SPLIT_LOOP = """\
version: 1
name: split-loop
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: impl
    agent: ag
    depends_on: [p]
    prompt: impl
  - id: mid
    delegation: orchestrator
    depends_on: [impl]
    prompt: interleaved orchestrator step
  - id: ev
    agent: ag
    depends_on: [mid]
    prompt: ev
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
"""


def test_split_loop_body_dies_before_emit(tmp_path):
    make_flow(tmp_path, "split-loop", SPLIT_LOOP)
    rc, data, out, err = run(tmp_path, "split-loop")
    assert rc == 1
    assert data is not None and "contiguous" in data["error"].lower()
    # Pre-emit: no segment files were written.
    compiled = tmp_path / "split-loop" / ".compiled"
    assert not list(compiled.glob("seg-*.js")) if compiled.exists() else True


# --- S-INFER: union-edge cycle + ref-implied ordering in compile ---

REF_CYCLE = """\
version: 1
name: ref-cycle
nodes:
  - id: a
    agent: ag
    prompt: use ${b.output.x}
  - id: b
    agent: ag
    prompt: use ${a.output.y}
"""


def test_ref_only_cycle_dies_in_compile(tmp_path):
    make_flow(tmp_path, "ref-cycle", REF_CYCLE)
    rc, data, out, err = run(tmp_path, "ref-cycle")
    assert rc == 1
    assert data is not None and "cycle" in data["error"].lower()


REF_ORDER = """\
version: 1
name: ref-order
nodes:
  - id: b
    agent: ag
    prompt: use ${a.output.x}
  - id: a
    agent: ag
    prompt: plan
"""


def test_inferred_edge_orders_segment(tmp_path):
    # `b` is defined before `a` but reads ${a.output.x} (no explicit depends_on).
    # The inferred edge a->b must order `a` before `b` in the single segment.
    d = make_flow(tmp_path, "ref-order", REF_ORDER)
    rc, data, out, err = run(tmp_path, "ref-order")
    assert rc == 0, err
    assert data["sequence"] == ["segment[a,b]"]
    m = json.loads((d / ".compiled" / "manifest.json").read_text())
    assert m["steps"][0]["nodes"] == ["a", "b"]


# --- INF-SCHEMA: a use: node inherits the microskill's resolved output_schema ---
# compile derives skill-root as the sibling `microskills/` of --defs-root, so a
# hermetic world holds BOTH its workflow-defs and its microskills under tmp_path.

ECHO_MS_MD = """\
---
name: echo-ms
description: minimal echo microskill for inheritance tests
---

# Echo

## Purpose

Echo back.

## Steps

1. Return the result.
"""

ECHO_MS_BASE = """\
version: 1
output_schema:
  type: object
  required: [echoed]
  properties:
    echoed: { type: string }
"""


def make_inh_world(tmp_path, wf_yaml, ms_base=ECHO_MS_BASE, extra_profiles=None):
    """Build a hermetic world: <tmp>/workflow-defs/inh-flow + <tmp>/microskills/echo-ms.
    Returns (defs_root, def_dir)."""
    defs_root = tmp_path / "workflow-defs"
    ms_dir = tmp_path / "microskills" / "echo-ms" / "profiles"
    ms_dir.mkdir(parents=True)
    (tmp_path / "microskills" / "echo-ms" / "MICROSKILL.md").write_text(ECHO_MS_MD)
    (ms_dir / "base.yaml").write_text(ms_base)
    for pname, ptext in (extra_profiles or {}).items():
        (ms_dir / f"{pname}.yaml").write_text(ptext)
    d = defs_root / "inh-flow"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(wf_yaml)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    return defs_root, d


INHERIT_WF = """\
version: 1
name: inh-flow
nodes:
  - id: e
    use: echo-ms
"""


def test_use_node_inherits_microskill_output_schema(tmp_path):
    # The use: node declares no output_schema; it must inherit the microskill's
    # resolved schema and bake it into the runMicroskill schema arg.
    defs_root, d = make_inh_world(tmp_path, INHERIT_WF)
    rc, data, out, err = run(defs_root, "inh-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert '"echoed"' in seg                       # inherited property name present
    assert "schema: null" not in seg               # NOT emitted as a schema-less call


EXPLICIT_OVERRIDE_WF = """\
version: 1
name: inh-flow
nodes:
  - id: e
    use: echo-ms
    output_schema:
      type: object
      required: [overridden]
      properties:
        overridden: { type: boolean }
"""


def test_explicit_node_output_schema_wins_over_inherited(tmp_path):
    # An explicit node-level output_schema beats the inherited microskill schema.
    defs_root, d = make_inh_world(tmp_path, EXPLICIT_OVERRIDE_WF)
    rc, data, out, err = run(defs_root, "inh-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert '"overridden"' in seg                   # explicit schema baked in
    assert '"echoed"' not in seg                    # inherited schema NOT used


MS_NOSCHEMA_PROFILE = "version: 1\noutput_schema: null\n"

PROFILE_DELETED_WF = """\
version: 1
name: inh-flow
nodes:
  - id: e
    use: echo-ms
    customize:
      profile: noschema
"""


def test_profile_deleted_schema_is_not_resurrected(tmp_path):
    # GATING #1: inheritance is default-for-ABSENT only. A profile that set
    # output_schema: null resolves to a finalized schema of None, so the node
    # (with no explicit schema) compiles with NO inherited schema — the deleted
    # schema is NOT resurrected.
    defs_root, d = make_inh_world(
        tmp_path, PROFILE_DELETED_WF, extra_profiles={"noschema": MS_NOSCHEMA_PROFILE})
    rc, data, out, err = run(defs_root, "inh-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "schema: null" in seg                   # no schema inherited
    assert '"echoed"' not in seg                    # base schema NOT resurrected


# --- LOOP ERGONOMICS: until / check / max_parallel ---

# A canonical hand-written negated-while loop, and the until-sugar that must
# desugar to byte-identical compiled output.
WHILE_LOOP = """\
version: 1
name: loop-flow
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: impl
    agent: ag
    depends_on: [p]
    prompt: impl
  - id: ev
    agent: ag
    depends_on: [impl]
    prompt: ev
gates:
  - id: g
    after: p
    type: human_approval
    prompt: ok?
loop:
  while: ${!(ev.output.pass)}
  max_iters: 3
  body: [impl, ev]
"""

UNTIL_LOOP = WHILE_LOOP.replace("  while: ${!(ev.output.pass)}\n",
                                "  until: ${ev.output.pass}\n")


def test_until_desugars_byte_identical_to_negated_while(tmp_path):
    # `until: ${EXPR}` is sugar for `while: ${!(EXPR)}` — the compiled segment must
    # be byte-identical to the hand-written negated while.
    make_flow(tmp_path, "loop-flow", WHILE_LOOP)
    rc1, _, _, err1 = run(tmp_path, "loop-flow")
    assert rc1 == 0, err1
    while_js = (tmp_path / "loop-flow" / ".compiled" / "seg-2.js").read_text()

    make_flow(tmp_path / "u", "loop-flow", UNTIL_LOOP)
    rc2, _, _, err2 = run(tmp_path / "u", "loop-flow")
    assert rc2 == 0, err2
    until_js = (tmp_path / "u" / "loop-flow" / ".compiled" / "seg-2.js").read_text()

    assert until_js == while_js


def test_both_while_and_until_blocks(tmp_path):
    body = WHILE_LOOP.replace("  while: ${!(ev.output.pass)}\n",
                              "  while: ${!(ev.output.pass)}\n  until: ${ev.output.pass}\n")
    make_flow(tmp_path, "loop-flow", body)
    rc, data, out, err = run(tmp_path, "loop-flow")
    assert rc == 1
    assert data is not None and "only one of 'while' / 'until'" in data["error"]


def test_neither_while_nor_until_blocks(tmp_path):
    body = WHILE_LOOP.replace("  while: ${!(ev.output.pass)}\n", "")
    make_flow(tmp_path, "loop-flow", body)
    rc, data, out, err = run(tmp_path, "loop-flow")
    assert rc == 1
    assert data is not None and "exactly one of 'while' / 'until'" in data["error"]


def test_loop_emits_do_while(tmp_path):
    # The loop scaffold is always do{ body }while(cond) (>=1 iteration).
    make_flow(tmp_path, "loop-flow", WHILE_LOOP)
    rc, data, out, err = run(tmp_path, "loop-flow")
    assert rc == 0, err
    seg = (tmp_path / "loop-flow" / ".compiled" / "seg-2.js").read_text()
    assert "do {" in seg
    assert "} while ((" in seg


FE_PLAIN = """\
version: 1
name: fe-flow
inputs:
  items:
    type: array
    required: true
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
"""

FE_MAX_PARALLEL = FE_PLAIN.replace("    as: item\n", "    as: item\n    max_parallel: 2\n")


def test_for_each_without_max_parallel_byte_identical(tmp_path):
    # A for_each node WITHOUT max_parallel must emit exactly today's unbounded
    # parallel() and NOT pull in the parallelChunked helper.
    make_flow(tmp_path, "fe-flow", FE_PLAIN)
    rc, data, out, err = run(tmp_path, "fe-flow")
    assert rc == 0, err
    seg = (tmp_path / "fe-flow" / ".compiled" / "seg-1.js").read_text()
    assert "await parallel(((_args.wf_items) || []).map((item) => () => runAgent" in seg
    assert "parallelChunked" not in seg


def test_max_parallel_emits_parallel_chunked(tmp_path):
    # max_parallel: K chunks the fan-out via parallelChunked, which is built only on
    # the existing parallel() global + Array.slice (no Promise.allSettled, no timers).
    make_flow(tmp_path, "fe-flow", FE_MAX_PARALLEL)
    rc, data, out, err = run(tmp_path, "fe-flow")
    assert rc == 0, err
    seg = (tmp_path / "fe-flow" / ".compiled" / "seg-1.js").read_text()
    assert "await parallelChunked(((_args.wf_items) || []).map((item) => () => runAgent" in seg
    assert "function parallelChunked(thunks, k)" in seg          # helper emitted
    assert "await parallel(thunks.slice(i, i + k))" in seg       # built on parallel + slice
    assert "Promise.allSettled" not in seg
    assert "setTimeout" not in seg


def test_max_parallel_without_for_each_blocks(tmp_path):
    body = """\
version: 1
name: bad-mp
nodes:
  - id: a
    agent: ag
    max_parallel: 2
    prompt: do a
"""
    make_flow(tmp_path, "bad-mp", body)
    rc, data, out, err = run(tmp_path, "bad-mp")
    assert rc == 1
    assert data is not None and "max_parallel is only valid on a for_each node" in data["error"]


# --- RANK6: --plan dry-run (writes nothing) + --explain (reason + de-anon) ---


def test_plan_writes_nothing_and_prints_plan(tmp_path):
    # --plan computes + prints the full plan but writes NOTHING to .compiled/
    # (no mkdir, no seg files, no manifest.json).
    make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow", "--plan")
    assert rc == 0, err
    # The plan summary is still printed (segments/checkpoints/sequence).
    assert data["segments"] == 2
    assert data["checkpoints"] == 1
    assert data["sequence"] == ["segment[a]", "gate", "segment[b]"]
    # Nothing on disk: the .compiled/ directory was never created.
    compiled = tmp_path / "gated-flow" / ".compiled"
    assert not compiled.exists()


def test_plan_does_not_clobber_existing_compiled(tmp_path):
    # A prior real compile populated .compiled/. A subsequent --plan must NOT
    # touch those bytes (no stale-clean unlink, no manifest rewrite).
    d = make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, _, _, err = run(tmp_path, "gated-flow")
    assert rc == 0, err
    before = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    rc2, _, _, err2 = run(tmp_path, "gated-flow", "--plan")
    assert rc2 == 0, err2
    after = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    assert before == after


def test_default_compile_byte_identical_to_explain_compiled_bytes(tmp_path):
    # The compiled segment bytes (the runtime artifact) must be byte-identical
    # whether or not --explain is passed: --explain only adds stdout/manifest
    # metadata, never changes the emitted JS.
    d1 = make_flow(tmp_path / "plain", "gated-flow",
                   GATED.replace("name: linear-flow", "name: gated-flow"))
    rc1, _, _, e1 = run(tmp_path / "plain", "gated-flow")
    assert rc1 == 0, e1
    plain = {p.name: p.read_text() for p in (d1 / ".compiled").glob("seg-*.js")}

    d2 = make_flow(tmp_path / "expl", "gated-flow",
                   GATED.replace("name: linear-flow", "name: gated-flow"))
    rc2, _, _, e2 = run(tmp_path / "expl", "gated-flow", "--explain")
    assert rc2 == 0, e2
    expl = {p.name: p.read_text() for p in (d2 / ".compiled").glob("seg-*.js")}
    assert plain == expl


def test_default_summary_byte_identical_no_manifest_hash(tmp_path):
    # With neither flag the stdout summary stays exactly as today: no
    # manifest_hash key, no classification reasons, bare sequence labels.
    make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow")
    assert rc == 0, err
    assert "manifest_hash" not in data
    assert "classification" not in data
    assert data["sequence"] == ["segment[a]", "gate", "segment[b]"]


def test_explain_surfaces_classification_reason(tmp_path):
    # --explain adds a per-node classification with a human reason explaining WHY
    # each node is background vs orchestrator.
    make_flow(tmp_path, "orch-flow", ORCH)
    rc, data, out, err = run(tmp_path, "orch-flow", "--explain")
    assert rc == 0, err
    classification = data["classification"]
    by_node = {c["node"]: c for c in classification}
    assert by_node["a"]["class"] == "background"
    assert by_node["fin"]["class"] == "orchestrator"
    # The orchestrator reason names the explicit delegation override.
    assert "delegation" in by_node["fin"]["reason"].lower()
    # The agent reason explains the background classification.
    assert "agent" in by_node["a"]["reason"].lower()


def test_explain_deanonymizes_sequence(tmp_path):
    # WITHOUT --explain the gate is the bare 'gate'; WITH --explain it carries the
    # gate id so the sequence is no longer lossy.
    make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow", "--explain")
    assert rc == 0, err
    seq = data["sequence"]
    assert any("g1" in s for s in seq), seq
    # The orchestrator-node case is de-anonymized too.
    make_flow(tmp_path, "orch-flow", ORCH)
    rc2, data2, _, err2 = run(tmp_path, "orch-flow", "--explain")
    assert rc2 == 0, err2
    assert any("fin" in s for s in data2["sequence"]), data2["sequence"]


def test_manifest_hash_present_and_deterministic(tmp_path):
    # Under --explain the manifest carries a manifest_hash; two compiles of the
    # same workflow produce the SAME hash (deterministic).
    d = make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow", "--explain")
    assert rc == 0, err
    assert "manifest_hash" in data
    m = json.loads((d / ".compiled" / "manifest.json").read_text())
    assert "manifest_hash" in m
    h1 = m["manifest_hash"]
    assert isinstance(h1, str) and len(h1) == 64    # sha256 hex
    assert data["manifest_hash"] == h1              # summary matches manifest
    rc2, data2, _, err2 = run(tmp_path, "gated-flow", "--explain")
    assert rc2 == 0, err2
    assert data2["manifest_hash"] == h1


def test_manifest_hash_excludes_itself(tmp_path):
    # The hash is computed over the manifest WITHOUT its own manifest_hash key —
    # recomputing the hash over the on-disk manifest (sans manifest_hash) must
    # reproduce the stored value.
    import hashlib
    d = make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow", "--explain")
    assert rc == 0, err
    m = json.loads((d / ".compiled" / "manifest.json").read_text())
    stored = m.pop("manifest_hash")
    recomputed = hashlib.sha256(
        json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert recomputed == stored


def test_manifest_hash_changes_when_workflow_changes(tmp_path):
    make_flow(tmp_path / "one", "gated-flow",
              GATED.replace("name: linear-flow", "name: gated-flow"))
    rc1, data1, _, e1 = run(tmp_path / "one", "gated-flow", "--explain")
    assert rc1 == 0, e1
    # A different workflow (extra node appended to the nodes section, before the
    # gates block) must produce a different hash.
    changed = GATED.replace("name: linear-flow", "name: gated-flow").replace(
        "gates:\n",
        "  - id: c\n    agent: ag\n    depends_on: [b]\n    prompt: use ${b.output.z}\n"
        "gates:\n")
    make_flow(tmp_path / "two", "gated-flow", changed)
    rc2, data2, _, e2 = run(tmp_path / "two", "gated-flow", "--explain")
    assert rc2 == 0, e2
    assert data1["manifest_hash"] != data2["manifest_hash"]


def test_plan_can_emit_manifest_hash_without_writing(tmp_path):
    # --plan --explain prints the manifest_hash but still writes nothing.
    make_flow(tmp_path, "gated-flow", GATED.replace("name: linear-flow", "name: gated-flow"))
    rc, data, out, err = run(tmp_path, "gated-flow", "--plan", "--explain")
    assert rc == 0, err
    assert "manifest_hash" in data
    assert not (tmp_path / "gated-flow" / ".compiled").exists()


# --- RANK15: version default + side_effect alias ---


def test_version_omitted_defaults_to_one_and_compiles(tmp_path):
    body = """\
name: noversion-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
"""
    make_flow(tmp_path, "noversion-flow", body)
    rc, data, out, err = run(tmp_path, "noversion-flow")
    assert rc == 0, err
    assert data["segments"] == 1


def test_explicit_wrong_version_blocks(tmp_path):
    body = """\
version: 2
name: badversion-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
"""
    make_flow(tmp_path, "badversion-flow", body)
    rc, data, out, err = run(tmp_path, "badversion-flow")
    assert rc == 1
    assert data is not None and "schema_errors" in data


def test_side_effect_true_classifies_as_orchestrator(tmp_path):
    # side_effect: true is a readable alias for delegation: orchestrator. The node
    # must classify as an orchestrator checkpoint.
    body = """\
version: 1
name: se-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: fin
    side_effect: true
    depends_on: [a]
    prompt: finalize using ${a.output.x}
"""
    make_flow(tmp_path, "se-flow", body)
    rc, data, out, err = run(tmp_path, "se-flow")
    assert rc == 0, err
    assert data["sequence"] == ["segment[a]", "orchestrator_node"]


def test_side_effect_explain_reason(tmp_path):
    body = """\
version: 1
name: se-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: fin
    side_effect: true
    depends_on: [a]
    prompt: finalize using ${a.output.x}
"""
    make_flow(tmp_path, "se-flow", body)
    rc, data, out, err = run(tmp_path, "se-flow", "--explain")
    assert rc == 0, err
    by_node = {c["node"]: c for c in data["classification"]}
    assert by_node["fin"]["class"] == "orchestrator"


# --- PHASE 5b FEATURE A: ${<id>.items} the per-item-results array ref form ---

# A for_each fan-out producer, then a downstream node that consumes the WHOLE
# per-item-results array via ${scan.items} (no explicit depends_on).
ITEMS_REF = """\
version: 1
name: items-flow
inputs:
  items:
    type: array
    required: true
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
  - id: collect
    agent: ag
    prompt: summarize the results ${scan.items}
"""


def test_items_ref_resolves_to_producer_array_var_in_segment(tmp_path):
    # ${scan.items} resolves to the same in-segment array var the for_each node
    # produces (n_scan) — NOT a .field access. The inferred edge scan->collect
    # keeps both in one ordered segment.
    d = make_flow(tmp_path, "items-flow", ITEMS_REF)
    rc, data, out, err = run(tmp_path, "items-flow")
    assert rc == 0, err
    assert data["sequence"] == ["segment[scan,collect]"]
    seg = (d / ".compiled" / "seg-1.js").read_text()
    # The ref renders as the producer's array var, not n_scan.items / .output.
    assert "${__ref(n_scan)}" in seg
    assert "n_scan.items" not in seg
    assert "n_scan.output" not in seg


def test_output_field_named_items_compiles_correctly(tmp_path):
    # Regression: a producer output field literally named `items`, referenced
    # in-segment as ${prod.output.items}, must translate to the field access
    # n_prod.items — NOT collide with the ${id.items} fan-out form (which would
    # mis-emit _args["n_prod"]). translate_ref resolves .items before .output.
    body = """\
version: 1
name: items-field
nodes:
  - id: prod
    agent: ag
    prompt: make
    output_schema:
      type: object
      properties:
        items: { type: array }
  - id: c
    agent: ag
    depends_on: [prod]
    prompt: use ${prod.output.items}
"""
    d = make_flow(tmp_path, "items-field", body)
    rc, data, out, err = run(tmp_path, "items-field")
    assert rc == 0, err
    assert data["sequence"] == ["segment[prod,c]"]
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "n_prod.items" in seg            # correct field access
    assert '_args["n_prod"]' not in seg     # not the miscompiled fan-out form


ITEMS_REF_REVERSED = """\
version: 1
name: items-rev
inputs:
  items:
    type: array
    required: true
nodes:
  - id: collect
    agent: ag
    prompt: summarize the results ${scan.items}
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
"""


def test_items_ref_infers_dependency_edge(tmp_path):
    # `collect` is defined BEFORE `scan` but reads ${scan.items} (no depends_on).
    # The inferred edge scan->collect must reorder scan before collect — load-bearing
    # (without the edge, definition order would put collect first).
    d = make_flow(tmp_path, "items-rev", ITEMS_REF_REVERSED)
    rc, data, out, err = run(tmp_path, "items-rev")
    assert rc == 0, err
    m = json.loads((d / ".compiled" / "manifest.json").read_text())
    assert m["steps"][0]["nodes"] == ["scan", "collect"]


# A producer in an EARLIER segment (split by an orchestrator node before it),
# consumed cross-segment via ${scan.items}.
ITEMS_REF_XSEG = """\
version: 1
name: items-xseg
inputs:
  items:
    type: array
    required: true
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
  - id: mid
    delegation: orchestrator
    depends_on: [scan]
    prompt: checkpoint
  - id: collect
    agent: ag
    depends_on: [mid]
    prompt: summarize ${scan.items}
"""


def test_items_ref_cross_segment_resolves_to_args(tmp_path):
    # When the producer lives in an earlier segment, ${scan.items} resolves to the
    # cross-segment results[scan] handle (_args["scan"]), same as its segment var.
    d = make_flow(tmp_path, "items-xseg", ITEMS_REF_XSEG)
    rc, data, out, err = run(tmp_path, "items-xseg")
    assert rc == 0, err
    # scan is its own segment, then the orchestrator checkpoint, then collect.
    seg_files = sorted((d / ".compiled").glob("seg-*.js"))
    collect_seg = [p.read_text() for p in seg_files if "n_collect" in p.read_text()][0]
    assert '_args["scan"]' in collect_seg
    assert '_args["scan"].items' not in collect_seg
    # The cross-segment producer is recorded in needs.nodes.
    m = json.loads((d / ".compiled" / "manifest.json").read_text())
    collect_step = [s for s in m["steps"]
                    if s["kind"] == "segment" and "collect" in s["nodes"]][0]
    assert "scan" in collect_step["needs"]["nodes"]


def test_no_items_ref_byte_identical(tmp_path):
    # DETERMINISM: a workflow that uses no .items ref compiles byte-identically.
    # Recompiling the SAME def is byte-identical (manifest included), and the
    # emitted segment JS is identical across two independent worlds (only the
    # manifest's absolute `script` path is path-dependent, so seg-*.js is the
    # path-free determinism surface).
    d = make_flow(tmp_path, "fe-flow", FE_PLAIN)
    run(tmp_path, "fe-flow")
    first = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    run(tmp_path, "fe-flow")
    second = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    assert first == second
    d2 = make_flow(tmp_path / "b", "fe-flow", FE_PLAIN)
    run(tmp_path / "b", "fe-flow")
    segs1 = {p.name: p.read_text() for p in (d / ".compiled").glob("seg-*.js")}
    segs2 = {p.name: p.read_text() for p in (d2 / ".compiled").glob("seg-*.js")}
    assert segs1 == segs2


# --- PHASE 5b FEATURE B: intra-def vars (double-brace {{key}} pre-pass) ---

VARS_FLOW = """\
version: 1
name: vars-flow
vars:
  topic: kubernetes
  depth: 3
  field: ok
nodes:
  - id: a
    agent: ag
    prompt: research {{topic}} at depth {{depth}}
  - id: b
    agent: ag
    when: ${a.output.{{field}}}
    prompt: use {{topic}}
"""


def test_vars_substituted_in_prompt_and_when(tmp_path):
    # {{topic}} / {{depth}} / {{field}} are substituted throughout the merged doc
    # before schema validation, so the compiled segment carries the resolved
    # literals in BOTH the prompt template and the rendered `when` condition.
    d = make_flow(tmp_path, "vars-flow", VARS_FLOW)
    rc, data, out, err = run(tmp_path, "vars-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "research kubernetes at depth 3" in seg     # prompt substituted
    assert "n_a.ok" in seg                              # when ${a.output.{{field}}} -> ok
    assert "{{topic}}" not in seg
    assert "{{field}}" not in seg


def test_vars_overridable_by_profile(tmp_path):
    # A profile overlay deep-merges over vars, so a profile-set var wins (the
    # substitution runs AFTER the merge).
    base = "version: 1\nvars:\n  topic: serverless\n"
    d = make_flow(tmp_path, "vars-flow", VARS_FLOW, base=base)
    rc, data, out, err = run(tmp_path, "vars-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "research serverless at depth 3" in seg


def test_unresolved_var_does_not_crash(tmp_path):
    # An unresolved {{key}} warns (not crash): the token is left intact and the
    # compile still succeeds.
    body = """\
version: 1
name: vars-flow
vars:
  topic: kubernetes
nodes:
  - id: a
    agent: ag
    prompt: research {{topic}} but {{missing}} is unknown
"""
    d = make_flow(tmp_path, "vars-flow", body)
    rc, data, out, err = run(tmp_path, "vars-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "research kubernetes but {{missing}} is unknown" in seg


def test_no_vars_byte_identical(tmp_path):
    # DETERMINISM: a workflow with no vars: compiles byte-identically. Same-def
    # recompile is fully byte-identical; segment JS is identical across worlds.
    src = GATED.replace("name: linear-flow", "name: gated-flow")
    d = make_flow(tmp_path, "gated-flow", src)
    run(tmp_path, "gated-flow")
    first = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    run(tmp_path, "gated-flow")
    second = {p.name: p.read_text() for p in (d / ".compiled").iterdir()}
    assert first == second
    d2 = make_flow(tmp_path / "b", "gated-flow", src)
    run(tmp_path / "b", "gated-flow")
    segs1 = {p.name: p.read_text() for p in (d / ".compiled").glob("seg-*.js")}
    segs2 = {p.name: p.read_text() for p in (d2 / ".compiled").glob("seg-*.js")}
    assert segs1 == segs2


# --- INDEPENDENT-SIBLING PARALLELISM (dependency-rank fan-out within a segment) ---
# Background nodes that share a dependency rank (no edge between them) are mutually
# independent. The compiler now emits each such rank as a single parallel([...]) batch
# with destructuring, instead of one serial `await` per sibling. Single-node ranks stay
# byte-identical to the old serial emission. Loop-body segments are NOT parallelized.

SIBLINGS = """\
version: 1
name: sib-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.x}
  - id: c
    agent: ag
    depends_on: [a]
    prompt: also use ${a.output.x}
  - id: d
    agent: ag
    depends_on: [b, c]
    prompt: combine ${b.output.y} and ${c.output.z}
"""


def test_independent_siblings_emit_single_parallel_batch(tmp_path):
    # b and c both depend only on a (no edge between them) → same rank → one
    # parallel([...]) with destructuring, NOT two serial `const n_x = await`.
    d = make_flow(tmp_path, "sib-flow", SIBLINGS)
    rc, data, out, err = run(tmp_path, "sib-flow")
    assert rc == 0, err
    assert data["sequence"] == ["segment[a,b,c,d]"]
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "const [n_b, n_c] = await parallel([" in seg   # rank emitted as a batch
    assert "() => runAgent" in seg                         # siblings are thunks
    assert "const n_b = await" not in seg                  # b is not a serial await
    assert "const n_c = await" not in seg                  # c is not a serial await
    # the single-node ranks around the batch stay serial awaits
    assert "const n_a = await runAgent" in seg
    assert "const n_d = await runAgent" in seg


def test_serial_chain_stays_byte_identical(tmp_path):
    # A pure dependency chain a -> b has only single-node ranks, so it must compile
    # to sequential awaits with NO parallel([...]) wrapper (byte-stable behavior).
    d = make_flow(tmp_path, "linear-flow", LINEAR)
    rc, data, out, err = run(tmp_path, "linear-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "const n_a = await runAgent" in seg
    assert "const n_b = await runAgent" in seg
    assert "parallel(" not in seg            # no sibling batch, no parallel helper


def test_sibling_parallelism_is_deterministic(tmp_path):
    # Same def compiles byte-identically across recompiles and across worlds.
    d = make_flow(tmp_path, "sib-flow", SIBLINGS)
    run(tmp_path, "sib-flow")
    first = (d / ".compiled" / "seg-1.js").read_text()
    run(tmp_path, "sib-flow")
    second = (d / ".compiled" / "seg-1.js").read_text()
    assert first == second
    d2 = make_flow(tmp_path / "b", "sib-flow", SIBLINGS)
    run(tmp_path / "b", "sib-flow")
    assert (d2 / ".compiled" / "seg-1.js").read_text() == first


SIBLINGS_FE = """\
version: 1
name: sibfe-flow
inputs:
  items:
    type: array
    required: true
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.x}
  - id: c
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    depends_on: [a]
    prompt: scan ${item}
"""


def test_for_each_sibling_nests_inside_rank_batch(tmp_path):
    # A for_each sibling in a multi-node rank keeps its own inner fan-out: its thunk
    # is `() => parallel(...)` (no await) nested inside the rank's outer parallel([...]).
    d = make_flow(tmp_path, "sibfe-flow", SIBLINGS_FE)
    rc, data, out, err = run(tmp_path, "sibfe-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "const [n_b, n_c] = await parallel([" in seg
    assert "() => parallel(((_args.wf_items) || []).map((item) => () => runAgent" in seg


SIBLINGS_WHEN = """\
version: 1
name: sibwhen-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
    output_schema:
      type: object
      properties:
        ok: { type: boolean }
        bad: { type: boolean }
  - id: b
    agent: ag
    when: ${a.output.ok}
    depends_on: [a]
    prompt: ok path
  - id: c
    agent: ag
    when: ${a.output.bad}
    depends_on: [a]
    prompt: bad path
"""


def test_when_guarded_siblings_parallelize_with_ternary_thunks(tmp_path):
    # Opposite-when siblings share a rank → one parallel([...]) of ternary thunks
    # `() => (cond) ? runAgent(...) : null`; exactly one resolves non-null.
    d = make_flow(tmp_path, "sibwhen-flow", SIBLINGS_WHEN)
    rc, data, out, err = run(tmp_path, "sibwhen-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert "const [n_b, n_c] = await parallel([" in seg
    assert "() => (n_a.ok) ?" in seg
    assert ": null" in seg


LOOP_SIBLINGS = """\
version: 1
name: loopsib-flow
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: x
    agent: ag
    depends_on: [p]
    prompt: x
  - id: y
    agent: ag
    depends_on: [p]
    prompt: y
gates:
  - id: g
    after: p
    type: human_approval
    prompt: ok?
loop:
  while: ${!y.output.pass}
  max_iters: 2
  body: [x, y]
  carry:
    last: ${y.output}
"""


def test_loop_body_siblings_stay_serial(tmp_path):
    # SCOPE BOUNDARY: independent siblings inside a loop body are NOT parallelized —
    # the do/while scaffold stays a sequential body (v1 only fans out non-loop segments).
    d = make_flow(tmp_path, "loopsib-flow", LOOP_SIBLINGS)
    rc, data, out, err = run(tmp_path, "loopsib-flow")
    assert rc == 0, err
    seg2 = (d / ".compiled" / "seg-2.js").read_text()   # loop segment, after the gate
    assert "do {" in seg2 and "} while (" in seg2
    assert "n_x = await runAgent" in seg2               # sequential body assignment
    assert "n_y = await runAgent" in seg2
    assert "await parallel([" not in seg2               # no sibling batch in the loop


def test_review_changes_dimension_reviews_parallelize(tmp_path):
    # ACCEPTANCE: review-changes' three dimension reviews all depend only on
    # `summarize`, so they share a rank and compile into a single parallel([...]).
    rc, data, out, err = run(REAL_DEFS, "review-changes")
    assert rc == 0, err
    seg = (REAL_DEFS / "review-changes" / ".compiled" / "seg-1.js").read_text()
    assert ("const [n_review_correctness, n_review_security, n_review_performance] "
            "= await parallel([") in seg
    assert "const n_review_security = await" not in seg   # not a serial sibling


# --- phase_group: cosmetic /workflows grouping (emit-only, manifest_hash-safe) ---
SIBLINGS_PG = """\
version: 1
name: sibpg-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: rb
    agent: ag
    phase_group: review
    depends_on: [a]
    prompt: use ${a.output.x}
  - id: rc
    agent: ag
    phase_group: review
    depends_on: [a]
    prompt: also ${a.output.x}
  - id: rd
    agent: ag
    phase_group: review
    depends_on: [a]
    prompt: more ${a.output.x}
"""


def test_phase_group_dedupes_meta_phases(tmp_path):
    # The three phase_group: review siblings collapse to ONE meta.phases title.
    d = make_flow(tmp_path, "sibpg-flow", SIBLINGS_PG)
    rc, data, out, err = run(tmp_path, "sibpg-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert seg.count('{ title: "review" }') == 1     # deduped to one group
    assert '{ title: "a" }' in seg                    # the upstream node keeps its own
    assert '{ title: "rb" }' not in seg               # individual ids collapsed away
    assert '{ title: "rc" }' not in seg


def test_phase_group_baked_into_each_call(tmp_path):
    # Each sibling's runAgent carries phase:"review" (the per-call opt is what the
    # /workflows engine groups by inside a parallel batch).
    d = make_flow(tmp_path, "sibpg-flow", SIBLINGS_PG)
    run(tmp_path, "sibpg-flow")
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert '"review", "node:rb")' in seg
    assert '"review", "node:rc")' in seg
    assert '"review", "node:rd")' in seg


def test_phase_group_multi_node_rank_no_marker(tmp_path):
    # A multi-node rank emits NO inline phase() marker (race-prone inside parallel);
    # grouping rides the per-call opt. The single-node `a` rank still gets its marker.
    d = make_flow(tmp_path, "sibpg-flow", SIBLINGS_PG)
    run(tmp_path, "sibpg-flow")
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert 'phase("review")' not in seg              # no marker before the batch
    assert 'phase("a")' in seg                        # single-node rank keeps its marker
    assert "const [n_rb, n_rc, n_rd] = await parallel([" in seg


def test_phase_group_manifest_hash_unchanged(tmp_path):
    # phase_group lives only in seg bytes, never the manifest → manifest_hash is
    # identical with vs without it (the determinism guarantee), while seg bytes differ.
    # Mutate the SAME def in place so the (absolute, path-embedding) manifest is held
    # constant except for phase_group.
    d = make_flow(tmp_path, "sibpg-flow", SIBLINGS_PG)
    rc1, data1, *_ = run(tmp_path, "sibpg-flow", "--explain")
    seg_pg = (d / ".compiled" / "seg-1.js").read_text()
    (d / "WORKFLOW.yaml").write_text(SIBLINGS_PG.replace("    phase_group: review\n", ""))
    rc2, data2, *_ = run(tmp_path, "sibpg-flow", "--explain")
    assert rc1 == 0 and rc2 == 0
    assert data1["manifest_hash"] == data2["manifest_hash"]      # manifest untouched
    seg_no = (d / ".compiled" / "seg-1.js").read_text()
    assert seg_pg != seg_no                                       # emit bytes differ


def test_phase_group_byte_deterministic(tmp_path):
    d = make_flow(tmp_path, "sibpg-flow", SIBLINGS_PG)
    run(tmp_path, "sibpg-flow")
    first = (d / ".compiled" / "seg-1.js").read_text()
    run(tmp_path, "sibpg-flow")
    assert (d / ".compiled" / "seg-1.js").read_text() == first
    d2 = make_flow(tmp_path / "b", "sibpg-flow", SIBLINGS_PG)
    run(tmp_path / "b", "sibpg-flow")
    assert (d2 / ".compiled" / "seg-1.js").read_text() == first


SINGLE_PG = """\
version: 1
name: singlepg-flow
nodes:
  - id: a
    agent: ag
    phase_group: foo
    prompt: do a
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.x}
"""


def test_single_node_phase_group_marker(tmp_path):
    # A lone (single-node-rank) node with a phase_group emits phase("<group>") and
    # surfaces the group in meta.phases.
    d = make_flow(tmp_path, "singlepg-flow", SINGLE_PG)
    rc, data, out, err = run(tmp_path, "singlepg-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert 'phase("foo")' in seg
    assert 'phase("a")' not in seg
    assert '{ title: "foo" }' in seg
    assert '"foo", "node:a")' in seg                  # per-call opt also carries the group


# --- review-changes: pure-pipeline strip + lite / comprehensive profiles (real def) ---
def test_review_changes_base_is_pure_pipeline(tmp_path):
    # After the strip, base review-changes is ONE background segment with ZERO
    # checkpoints (no gate, no post) — it ends at synthesize and composes clean when nested.
    rc, data, out, err = run(REAL_DEFS, "review-changes")
    assert rc == 0, err
    assert data["segments"] == 1 and data["checkpoints"] == 0
    assert data["sequence"] == [
        "segment[summarize,review_correctness,review_security,"
        "review_performance,collect,verify,synthesize]"]


def test_review_changes_lite_profile_is_correctness_only(tmp_path):
    # lite removes the security + performance dimension nodes and unwires collect.
    rc, data, out, err = run(REAL_DEFS, "review-changes", "--profile", "lite")
    assert rc == 0, err
    assert data["segments"] == 1 and data["checkpoints"] == 0
    seq = data["sequence"][0]
    assert "review_correctness" in seq
    assert "review_security" not in seq and "review_performance" not in seq


def test_review_changes_comprehensive_profile_six_dimensions(tmp_path):
    # comprehensive adds style/documentation/test_coverage — six dimensions under ONE
    # "review" group — plus the configurable min_coverage input (default 80).
    rc, data, out, err = run(REAL_DEFS, "review-changes", "--profile", "comprehensive", "--explain")
    assert rc == 0, err
    seg = (REAL_DEFS / "review-changes" / ".compiled" / "seg-1.js").read_text()
    assert ("const [n_review_correctness, n_review_security, n_review_performance, "
            "n_review_style, n_review_documentation, n_review_test_coverage] "
            "= await parallel([") in seg
    assert seg.count('{ title: "review" }') == 1          # all six collapse to one group
    assert 'phase("review")' not in seg                    # per-call opt, no racey marker
    assert '"threshold": _args.wf_min_coverage' in seg     # coverage threshold threaded in
    man = json.loads((REAL_DEFS / "review-changes" / ".compiled" / "manifest.json").read_text())
    assert man["input_defaults"].get("min_coverage") == 80


# --- materialize: file — large/multi-shape input passed by reference ---
MATERIALIZE = """\
version: 1
name: mat-flow
inputs:
  big_path:
    type: string
    required: true
    materialize: file
  also_path:
    type: string
    required: false
    materialize: file
  small:
    type: string
    required: false
nodes:
  - id: a
    agent: ag
    prompt: use ${workflow.inputs.big_path} and ${workflow.inputs.also_path} and ${workflow.inputs.small}
"""


def test_manifest_emits_materialize_inputs(tmp_path):
    # Inputs declared `materialize: file` are listed (sorted) in the manifest's
    # materialize_inputs; a plain input is NOT — so the dispatcher knows which
    # gathered values to normalize to a temp file before threading the path.
    d = make_flow(tmp_path, "mat-flow", MATERIALIZE)
    rc, data, out, err = run(tmp_path, "mat-flow")
    assert rc == 0, err
    man = json.loads((d / ".compiled" / "manifest.json").read_text())
    assert man["materialize_inputs"] == ["also_path", "big_path"]   # sorted
    assert "small" not in man["materialize_inputs"]


def test_manifest_materialize_inputs_empty_when_none(tmp_path):
    # A workflow with no materialize inputs still carries the key (empty list),
    # so the dispatcher can iterate unconditionally.
    d = make_flow(tmp_path, "linear-flow", LINEAR)
    rc, data, out, err = run(tmp_path, "linear-flow")
    assert rc == 0, err
    man = json.loads((d / ".compiled" / "manifest.json").read_text())
    assert man["materialize_inputs"] == []


def test_review_changes_threads_diff_path_by_reference(tmp_path):
    # After the diff_path migration, the compiled review-changes segment threads
    # ONLY the short path (_args.wf_diff_path) into every diff consumer — never the
    # inline _args.wf_diff — and the manifest marks diff_path materialize:file.
    rc, data, out, err = run(REAL_DEFS, "review-changes", "--explain")
    assert rc == 0, err
    seg = (REAL_DEFS / "review-changes" / ".compiled" / "seg-1.js").read_text()
    assert "_args.wf_diff_path" in seg
    assert "_args.wf_diff" not in seg.replace("_args.wf_diff_path", "")  # no bare wf_diff
    man = json.loads((REAL_DEFS / "review-changes" / ".compiled" / "manifest.json").read_text())
    assert "diff_path" in man["required_inputs"]
    assert "diff" not in man["required_inputs"]
    assert man["materialize_inputs"] == ["diff_path"]
