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
    # workflow-create is now a single background segment (plan) followed by the
    # provision/advise orchestrator checkpoints and the `build` nested-workflow
    # checkpoint (a first-class workflow: node invoking the shared build half).
    rc, data, out, err = run(REAL_DEFS, "workflow-create")
    assert rc == 0, err
    assert data["segments"] == 1
    assert data["sequence"] == [
        "segment[plan]", "gate", "orchestrator_node", "orchestrator_node",
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
