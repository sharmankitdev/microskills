"""
Tests for validate-workflow. Run: python3 -m pytest .claude/scripts/tests/ -v

Covers schema + DAG checks via subprocess against tmp_path fixtures, plus an
end-to-end check against the real microskill-create definition.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "validate-workflow"
REAL_FLOW = REPO / "catalog" / "workflow-defs" / "microskill-create"
# Pin the in-repo source schema (committed templates/) so tests don't read a
# possibly-stale .claude/templates copy.
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}


def run(*paths):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(p) for p in paths]],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def write_wf(tmp_path, body):
    p = tmp_path / "WORKFLOW.yaml"
    p.write_text(body)
    return p


def locs(data):
    return {i["location"] for i in data["issues"] if i["severity"] == "block"}


VALID = """\
version: 1
name: tiny-flow
description: two background nodes
nodes:
  - id: a
    agent: some-agent
    prompt: do a
  - id: b
    agent: some-agent
    depends_on: [a]
    prompt: use ${a.output.x}
"""


def test_valid_passes(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, VALID))
    assert rc == 0
    assert data["pass"] is True
    assert data["issues"] == []


def test_undeclared_output_ref_now_passes(tmp_path):
    # S-INFER: a ${a.output.x} ref implies an edge a->b, so dropping the explicit
    # depends_on no longer blocks — the edge is inferred from the ref.
    body = VALID.replace("    depends_on: [a]\n", "")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert not any("not in depends_on" in i["message"] for i in data["issues"])


def test_depends_on_unknown_blocks(tmp_path):
    body = VALID.replace("depends_on: [a]", "depends_on: [ghost]")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("unknown node 'ghost'" in i["message"] for i in data["issues"])


def test_node_without_use_or_agent_blocks(tmp_path):
    body = """\
version: 1
name: bad-flow
nodes:
  - id: a
    prompt: orphan step with no use/agent and not orchestrator
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("neither 'use' nor 'agent'" in i["message"] for i in data["issues"])


def test_orchestrator_native_node_ok(tmp_path):
    body = """\
version: 1
name: ok-flow
nodes:
  - id: a
    delegation: orchestrator
    prompt: an orchestrator-native step
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_gate_after_unknown_blocks(tmp_path):
    body = VALID + """\
gates:
  - id: g1
    after: ghost
    type: human_approval
    prompt: approve?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("unknown node 'ghost'" in i["message"] for i in data["issues"])


def test_cycle_blocks(tmp_path):
    body = """\
version: 1
name: cyc
nodes:
  - id: a
    agent: x
    depends_on: [b]
  - id: b
    agent: x
    depends_on: [a]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("cycle" in i["message"] for i in data["issues"])


def test_for_each_requires_as(tmp_path):
    body = """\
version: 1
name: fe
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    for_each: ${workflow.inputs.items}
    prompt: scan
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("for_each requires" in i["message"] for i in data["issues"])


def test_bad_as_identifier_blocks(tmp_path):
    body = """\
version: 1
name: fe
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    for_each: ${workflow.inputs.items}
    as: "Bad-Name"
    prompt: scan
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("safe identifier" in i["message"] for i in data["issues"])


def test_for_each_in_loop_body_blocks(tmp_path):
    body = """\
version: 1
name: fe
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    prompt: plan
  - id: b
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    depends_on: [a]
    prompt: scan ${item}
loop:
  while: ${!b.output.done}
  max_iters: 2
  body: [b]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("fan-out inside a loop body" in i["message"] for i in data["issues"])


def test_when_ref_infers_edge_passes(tmp_path):
    # S-INFER: a ${a.output.ok} ref in `when` implies the edge a->b, so no explicit
    # depends_on is required — this now passes.
    body = """\
version: 1
name: wf
nodes:
  - id: a
    agent: ag
    prompt: plan
  - id: b
    agent: ag
    when: ${a.output.ok}
    prompt: go
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert not any("not in depends_on" in i["message"] for i in data["issues"])


# --- S-INFER: ref-implied edges + union-edge cycle detection ---

def test_unknown_node_ref_still_blocks(tmp_path):
    # A ${ghost.output.x} ref names a node that does not exist — still a typo guard.
    body = """\
version: 1
name: wf
nodes:
  - id: a
    agent: ag
    prompt: use ${ghost.output.x}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("output of unknown node 'ghost'" in i["message"] for i in data["issues"])


def test_ref_only_cycle_blocks(tmp_path):
    # A reads ${b.output}, B reads ${a.output}; neither lists depends_on. The cycle
    # must be detected over the UNION edge set (refs + depends_on), not depends_on alone.
    body = """\
version: 1
name: cyc
nodes:
  - id: a
    agent: ag
    prompt: use ${b.output.x}
  - id: b
    agent: ag
    prompt: use ${a.output.y}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("cycle" in i["message"] for i in data["issues"])


def test_explicit_depends_on_still_honored(tmp_path):
    # An explicit depends_on edge with no matching ref still passes (and is still
    # checked for unknown targets, exercised elsewhere).
    body = """\
version: 1
name: wf
nodes:
  - id: a
    agent: ag
    prompt: plan
  - id: b
    agent: ag
    depends_on: [a]
    prompt: go
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


# --- S-FIELD: typed-producer field-ref check ---

FIELD_PRODUCER = """\
version: 1
name: field-flow
nodes:
  - id: a
    agent: ag
    prompt: plan
    output_schema:
      type: object
      properties:
        ok: { type: boolean }
        score: { type: number }
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.%s}
"""


def test_unknown_field_on_typed_producer_blocks(tmp_path):
    body = FIELD_PRODUCER % "ghostfield"
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("ghostfield" in i["message"] and "does not declare" in i["message"]
               for i in data["issues"])


def test_known_field_on_typed_producer_passes(tmp_path):
    body = FIELD_PRODUCER % "ok"
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_field_on_schemaless_producer_not_flagged(tmp_path):
    # Producer `a` carries no output_schema → treated as 'any' → never flagged.
    body = """\
version: 1
name: field-flow
nodes:
  - id: a
    agent: ag
    prompt: plan
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${a.output.whatever}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_nested_field_path_checks_first_segment_only(tmp_path):
    # ${a.output.ok.deeper.deepest} — only the FIRST segment (`ok`) is checked
    # against the producer's declared properties; deeper paths are not.
    body = FIELD_PRODUCER % "ok.deeper.deepest"
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    # And a bad first segment on a nested path DOES block.
    body2 = FIELD_PRODUCER % "nope.deeper"
    rc2, data2, _ = run(write_wf(tmp_path, body2))
    assert rc2 == 1 and data2["pass"] is False
    assert any("nope" in i["message"] and "does not declare" in i["message"]
               for i in data2["issues"])


# --- S-LOOP: loop-body contiguity ---

def test_loop_body_split_by_orchestrator_blocks(tmp_path):
    # An orchestrator node interleaved between the two loop-body nodes breaks the
    # do/while scaffold silently in compile; validate must block it.
    body = """\
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
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("contiguous" in i["message"].lower() for i in data["issues"])


def test_loop_body_split_by_gate_blocks(tmp_path):
    # A human_approval gate anchored between the loop-body nodes also splits the
    # body across a checkpoint — block.
    body = """\
version: 1
name: split-loop-gate
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
    after: impl
    type: human_approval
    prompt: ok?
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("contiguous" in i["message"].lower() for i in data["issues"])


def test_contiguous_loop_body_passes(tmp_path):
    # The classic contiguous loop body (impl, ev adjacent, gate before the loop)
    # still passes.
    body = """\
version: 1
name: ok-loop
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
    prompt: use ${impl.output.x}
gates:
  - id: g
    after: p
    type: human_approval
    prompt: ok?
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_real_flow_passes():
    rc, data, _ = run(REAL_FLOW / "WORKFLOW.yaml", REAL_FLOW / "profiles" / "base.yaml")
    assert rc == 0, data
    assert data["pass"] is True


def test_real_workflow_create_passes():
    wc = REPO / "catalog" / "workflow-defs" / "workflow-create"
    rc, data, _ = run(wc / "WORKFLOW.yaml", wc / "profiles" / "base.yaml")
    assert rc == 0, data
    assert data["pass"] is True


def test_real_decompose_nested_validates_with_defs_root():
    # decompose's `build` is now a first-class workflow: node. Validate WITH --defs-root so the
    # nested checks engage (import allowlist, target resolution, import-cycle, depth=1,
    # required-child-input) against the real catalog defs. decompose is committed source but not
    # materialized in this harness's .claude selection, so point --defs-root at catalog/.
    catalog_defs = REPO / "catalog" / "workflow-defs"
    d = catalog_defs / "decompose-monolith-orchestrator"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(d / "WORKFLOW.yaml"),
         str(d / "profiles" / "base.yaml"), "--defs-root", str(catalog_defs)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout)
    assert data["pass"] is True, [i for i in data["issues"] if i["severity"] == "block"]


# --- gate-0: gate-id uniqueness + gate/node-id disjointness ---

def test_duplicate_gate_id_blocks(tmp_path):
    body = VALID + """\
gates:
  - id: g1
    after: a
    type: human_approval
    prompt: approve?
  - id: g1
    after: b
    type: human_approval
    prompt: approve again?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("duplicate gate id 'g1'" in i["message"] for i in data["issues"])


def test_gate_node_id_collision_blocks(tmp_path):
    body = VALID + """\
gates:
  - id: a
    after: b
    type: human_approval
    prompt: approve?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("collides with a node id" in i["message"] for i in data["issues"])


def test_distinct_gate_ids_pass(tmp_path):
    body = VALID + """\
gates:
  - id: g1
    after: a
    type: human_approval
    prompt: approve?
  - id: g2
    after: b
    type: human_approval
    prompt: approve more?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


# --- P1.2/P1.3: profile-driven node/gate verbs (add/patch/remove) through validate ---

def _overlay(tmp_path, text):
    p = tmp_path / "over.yaml"
    p.write_text(text)
    return p


def test_node_verb_add_and_patch_passes(tmp_path):
    wf = write_wf(tmp_path, VALID)
    over = _overlay(tmp_path, """\
nodes:
  patch:
    - id: b
      prompt: patched ${a.output.x}
  add:
    - id: c
      agent: some-agent
      depends_on: [b]
      prompt: use ${b.output.y}
""")
    rc, data, _ = run(wf, over)
    assert rc == 0 and data["pass"] is True


def test_node_verb_remove_with_dangling_ref_blocks(tmp_path):
    wf = write_wf(tmp_path, VALID)  # b depends on a and reads ${a.output.x}
    over = _overlay(tmp_path, "nodes:\n  remove: [a]\n")
    rc, data, _ = run(wf, over)
    assert rc == 1 and data["pass"] is False
    assert any("unknown node 'a'" in i["message"] for i in data["issues"])


def test_node_verb_missing_patch_id_clean_error(tmp_path):
    wf = write_wf(tmp_path, VALID)
    over = _overlay(tmp_path, "nodes:\n  patch:\n    - id: ghost\n      prompt: nope\n")
    rc, data, err = run(wf, over)
    assert rc == 1 and data["pass"] is False
    assert any("list-verb error" in i["message"] for i in data["issues"])
    assert "Traceback" not in err


def test_gate_verb_patch_via_profile_passes(tmp_path):
    body = VALID + """\
gates:
  - id: g1
    after: a
    type: human_approval
    prompt: approve?
"""
    wf = write_wf(tmp_path, body)
    over = _overlay(tmp_path, "gates:\n  patch:\n    - id: g1\n      prompt: PATCHED approve?\n")
    rc, data, _ = run(wf, over)
    assert rc == 0 and data["pass"] is True


# --- N3 / N4: first-class nested workflow node (workflow: <name>) ---

def run_defs(defs_root, wf_path, *extra):
    """Run validate-workflow with an explicit --defs-root flag."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(wf_path), "--defs-root", str(defs_root), *[str(p) for p in extra]],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def make_def(defs_root, name, body):
    """Write a synthetic <defs-root>/<name>/WORKFLOW.yaml; return its path."""
    d = defs_root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "WORKFLOW.yaml"
    p.write_text(body)
    return p


# A simple leaf child with one required input and no nested workflow: node.
CHILD = """\
version: 1
name: child-flow
imports: []
inputs:
  seed:
    type: string
    required: true
nodes:
  - id: work
    agent: ag
    prompt: work on ${workflow.inputs.seed}
output:
  from: work
"""

# A valid parent referencing child-flow, satisfying its required `seed` input.
PARENT = """\
version: 1
name: parent-flow
imports: [child-flow]
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: build
    workflow: child-flow
    depends_on: [a]
    inputs:
      seed: ${a.output.x}
"""


def test_workflow_and_use_together_blocks(tmp_path):
    # A workflow: node may not also carry use: (or agent:). Pure shape check —
    # fires WITHOUT --defs-root.
    body = """\
version: 1
name: bad-flow
nodes:
  - id: build
    workflow: child-flow
    use: some-microskill
    prompt: nope
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("more than one of use/agent/workflow" in i["message"] for i in data["issues"])


def test_lone_workflow_node_passes_without_defs_root(tmp_path):
    # Without --defs-root the nested-resolution checks are skipped, so a lone
    # parent carrying a workflow: node still passes schema + shape (backward
    # compatible single-file invocation).
    rc, data, _ = run(write_wf(tmp_path, PARENT))
    assert rc == 0, data
    assert data["pass"] is True


def test_valid_parent_and_child_passes_with_defs_root(tmp_path):
    make_def(tmp_path, "child-flow", CHILD)
    parent = make_def(tmp_path, "parent-flow", PARENT)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 0, data
    assert data["pass"] is True


def test_workflow_target_not_in_imports_blocks(tmp_path):
    make_def(tmp_path, "child-flow", CHILD)
    body = PARENT.replace("imports: [child-flow]\n", "imports: []\n")
    parent = make_def(tmp_path, "parent-flow", body)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("imports" in i["message"] and "child-flow" in i["message"] for i in data["issues"])


def test_workflow_unknown_child_blocks(tmp_path):
    # imports lists the target but no <defs-root>/child-flow/WORKFLOW.yaml exists.
    parent = make_def(tmp_path, "parent-flow", PARENT)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("does not resolve" in i["message"] or "not found" in i["message"]
               for i in data["issues"])


def test_workflow_import_cycle_blocks(tmp_path):
    # parent -> child -> parent forms an import cycle.
    child = CHILD.replace("imports: []\n", "imports: [parent-flow]\n").replace(
        "  - id: work\n    agent: ag\n    prompt: work on ${workflow.inputs.seed}\n",
        "  - id: work\n    workflow: parent-flow\n    inputs:\n      seed: hi\n")
    make_def(tmp_path, "child-flow", child)
    parent = make_def(tmp_path, "parent-flow", PARENT)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("cycle" in i["message"] for i in data["issues"])


def test_workflow_depth_two_blocks(tmp_path):
    # The child itself contains a workflow: node (grandchild) → depth-2 blocked.
    grandchild = CHILD.replace("name: child-flow", "name: grand-flow").replace(
        "imports: []", "imports: []")
    make_def(tmp_path, "grand-flow", grandchild)
    child = CHILD.replace("imports: []\n", "imports: [grand-flow]\n").replace(
        "  - id: work\n    agent: ag\n    prompt: work on ${workflow.inputs.seed}\n",
        "  - id: work\n    workflow: grand-flow\n    inputs:\n      seed: hi\n")
    make_def(tmp_path, "child-flow", child)
    parent = make_def(tmp_path, "parent-flow", PARENT)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("depth" in i["message"] or "grandchild" in i["message"]
               for i in data["issues"])


def test_workflow_missing_required_child_input_blocks(tmp_path):
    # The child requires `seed`, but the parent's workflow: node omits it.
    make_def(tmp_path, "child-flow", CHILD)
    body = PARENT.replace("    inputs:\n      seed: ${a.output.x}\n", "")
    parent = make_def(tmp_path, "parent-flow", body)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("requires input 'seed'" in i["message"] for i in data["issues"])
