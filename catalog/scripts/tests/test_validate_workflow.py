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
    # b restates the ref-implied edge a->b in depends_on, so the redundancy lint
    # warns (2.2) — the only issue, and never a block.
    assert all(i["severity"] == "warn" for i in data["issues"])
    assert [i["location"] for i in data["issues"]] == ["nodes/b/depends_on"]


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
    # The shipped def is lint-clean: no redundant depends_on, no inline schema
    # restating the resolved one (2.1/2.2 catalog cleanup).
    assert not any("redundant" in i["message"] or "output_schema" in i["location"]
                   for i in data["issues"]), data["issues"]


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


# --- nested-workflow customize.profile resolution (needs --defs-root) ---

def _nested_defs(tmp_path):
    """A defs-root with a child 'kid' (base + autonomous profiles) ready to import."""
    root = tmp_path / "defs"
    kid = root / "kid"
    (kid / "profiles").mkdir(parents=True)
    (kid / "WORKFLOW.yaml").write_text(
        "version: 1\nname: kid\n"
        "inputs:\n  q:\n    type: string\n    required: true\n"
        "nodes:\n  - id: c\n    agent: ag\n    prompt: do ${workflow.inputs.q}\n"
        "output:\n  from: c\n")
    (kid / "profiles" / "base.yaml").write_text("version: 1\n")
    (kid / "profiles" / "autonomous.yaml").write_text("version: 1\n")
    return root


_NESTED_PARENT = """\
version: 1
name: parent
imports:
  - kid
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: call
    workflow: kid
    depends_on: [a]
    customize: {{ profile: {prof} }}
    inputs:
      q: ${{a.output.x}}
"""


def _write_parent(root, prof):
    p = root / "parent"
    (p / "profiles").mkdir(parents=True)
    (p / "WORKFLOW.yaml").write_text(_NESTED_PARENT.format(prof=prof))
    (p / "profiles" / "base.yaml").write_text("version: 1\n")
    return p


def test_nested_profile_resolves_passes(tmp_path):
    root = _nested_defs(tmp_path)
    p = _write_parent(root, "autonomous")
    rc, data, _ = run(p / "WORKFLOW.yaml", p / "profiles" / "base.yaml", "--defs-root", root)
    assert data["pass"] is True, [i for i in data["issues"] if i["severity"] == "block"]


def test_nested_profile_missing_blocks(tmp_path):
    root = _nested_defs(tmp_path)
    p = _write_parent(root, "ghost")
    rc, data, _ = run(p / "WORKFLOW.yaml", p / "profiles" / "base.yaml", "--defs-root", root)
    assert data["pass"] is False
    assert any("ghost" in i["message"]
               for i in data["issues"] if i["severity"] == "block")


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


def _mk_ms(tmp_path, name, base_yaml="version: 1\n", md=None):
    d = tmp_path / "microskills" / name
    (d / "profiles").mkdir(parents=True, exist_ok=True)
    (d / "MICROSKILL.md").write_text(md or (
        "---\nname: %s\ndescription: minimal\n---\n\n# %s\n\n"
        "## Purpose\n\nGiven X do Y produce Z.\n\n## Steps\n\n1. Return the result.\n"
        % (name, name)))
    (d / "profiles" / "base.yaml").write_text(base_yaml)
    return d


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


# --- T3: profile-MERGED child read in the nested depth/cycle walk ---
# The depth/cycle helper used to load each child RAW (load_child_doc), so a child
# profile that ADDS a workflow: node (or import) via the parent node's
# customize.profile was invisible — DEPTH=1/cycle was computed on the un-profiled
# graph. The walk now reads each child through _profile_merged_child_doc, threading
# the parent node's customize.profile at the direct parent->child boundary.

def _mk_child_profile(defs_root, child_name, profile_name, text):
    """Write <defs-root>/<child>/profiles/<profile>.yaml (and base.yaml)."""
    pdir = defs_root / child_name / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    if not (pdir / "base.yaml").exists():
        (pdir / "base.yaml").write_text("version: 1\n")
    (pdir / f"{profile_name}.yaml").write_text(text)


# A parent whose workflow: node selects a child profile P on the direct boundary.
PARENT_PROF = """\
version: 1
name: parent-flow
imports: [kid-flow]
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: build
    workflow: kid-flow
    depends_on: [a]
    customize:
      profile: %s
    inputs:
      seed: ${a.output.x}
"""

# A leaf child (renamed CHILD) with no nested workflow: node in its RAW form.
KID = CHILD.replace("name: child-flow", "name: kid-flow")


def test_nested_profile_added_grandchild_blocks_depth(tmp_path):
    # kid-flow's profile `withgrand` ADDS a workflow: grand-flow node (nodes.add).
    # Invisible to a RAW child read; the profile-merged read makes it depth-2.
    make_def(tmp_path, "grand-flow", CHILD.replace("name: child-flow", "name: grand-flow"))
    make_def(tmp_path, "kid-flow", KID)
    _mk_child_profile(tmp_path, "kid-flow", "withgrand", """\
version: 1
nodes:
  add:
    - id: deep
      workflow: grand-flow
      inputs:
        seed: hi
""")
    parent = make_def(tmp_path, "parent-flow", PARENT_PROF % "withgrand")
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False, data
    assert any("depth" in i["message"] for i in data["issues"]), data


def test_nested_profile_added_nonworkflow_node_passes(tmp_path):
    # kid-flow's profile `withagent` ADDS only an agent node — no nested workflow:,
    # so the depth/cycle walk stays clean and the parent validates.
    make_def(tmp_path, "kid-flow", KID)
    _mk_child_profile(tmp_path, "kid-flow", "withagent", """\
version: 1
nodes:
  add:
    - id: extra
      agent: ag2
      prompt: more work
""")
    parent = make_def(tmp_path, "parent-flow", PARENT_PROF % "withagent")
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 0, data
    assert data["pass"] is True


def test_nested_profile_added_import_cycle_blocks(tmp_path):
    # kid-flow's profile `cyc` ADDS parent-flow to imports AND a workflow: parent-flow
    # node — forming parent -> kid -> parent. The cycle edge lives one level below
    # the root, so direct-boundary profile threading surfaces it.
    make_def(tmp_path, "kid-flow", KID)
    _mk_child_profile(tmp_path, "kid-flow", "cyc", """\
version: 1
imports:
  add: [parent-flow]
nodes:
  add:
    - id: back
      workflow: parent-flow
      inputs:
        seed: hi
""")
    parent = make_def(tmp_path, "parent-flow", PARENT_PROF % "cyc")
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False, data
    assert any("cycle" in i["message"] for i in data["issues"]), data


# --- LOOP ERGONOMICS: until / check / max_parallel validation ---

LOOP_WHILE = """\
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
loop:
  while: ${!(ev.output.pass)}
  max_iters: 3
  body: [impl, ev]
"""


def test_until_only_loop_passes(tmp_path):
    body = LOOP_WHILE.replace("  while: ${!(ev.output.pass)}\n", "  until: ${ev.output.pass}\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_both_while_and_until_blocks(tmp_path):
    body = LOOP_WHILE.replace("  while: ${!(ev.output.pass)}\n",
                              "  while: ${!(ev.output.pass)}\n  until: ${ev.output.pass}\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("only one of 'while' / 'until'" in i["message"] for i in data["issues"])


def test_neither_while_nor_until_blocks(tmp_path):
    body = LOOP_WHILE.replace("  while: ${!(ev.output.pass)}\n", "")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("exactly one of 'while' / 'until'" in i["message"] for i in data["issues"])


def test_max_parallel_on_for_each_passes(tmp_path):
    body = """\
version: 1
name: fe
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    max_parallel: 2
    prompt: scan ${item}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


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
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("max_parallel is only valid on a for_each node" in i["message"]
               for i in data["issues"])


# --- RANK15: version default + side_effect alias (validate side) ---


def test_version_omitted_validates(tmp_path):
    # An OMITTED version defaults to 1 and validates clean.
    body = """\
name: noversion-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True


def test_explicit_wrong_version_blocks_validate(tmp_path):
    body = """\
version: 2
name: badversion-flow
nodes:
  - id: a
    agent: ag
    prompt: do a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"].startswith("schema:") for i in data["issues"])


def test_side_effect_node_validates(tmp_path):
    # A node carrying side_effect: true (the orchestrator alias) and neither
    # use nor agent must validate clean — the node-shape check exempts it.
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
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True
    assert not any("neither 'use' nor 'agent'" in i["message"] for i in data["issues"])


def test_max_parallel_below_one_blocks_via_schema(tmp_path):
    body = """\
version: 1
name: fe
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    max_parallel: 0
    prompt: scan ${item}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"].startswith("schema:") and "max_parallel" in i["location"]
               for i in data["issues"])


# --- Phase 4b: gate-choice branching (a gate id is a legal ${...} ref target) ---

def test_gate_choice_ref_passes(tmp_path):
    # A downstream node's `when` reads ${g.output.choice} where g is a GATE id (not
    # a node). It must be ACCEPTED — no 'references output of unknown node' block —
    # and must NOT require g in depends_on (a gate is a checkpoint, not a node).
    body = VALID + """\
  - id: c
    agent: some-agent
    when: ${g.output.choice}
    prompt: branch on the human pick
gates:
  - id: g
    after: a
    type: human_approval
    prompt: approve?
    options: [approve, revise]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True
    # No unknown-node block for the gate id, and no depends_on requirement.
    assert not any("unknown node 'g'" in i["message"] for i in data["issues"])
    assert not any("output of unknown node 'g'" in i["message"] for i in data["issues"])


def test_ghost_output_ref_still_blocks_with_gates_present(tmp_path):
    # A ${ghost.output} ref to a non-existent id STILL blocks, even when real gates
    # exist (the gate-id allowance must not swallow genuine typos).
    body = VALID + """\
  - id: c
    agent: some-agent
    when: ${ghost.output.choice}
    prompt: branch on a non-existent producer
gates:
  - id: g
    after: a
    type: human_approval
    prompt: approve?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("output of unknown node 'ghost'" in i["message"] for i in data["issues"])


def test_gate_id_ref_creates_no_dependency_edge(tmp_path):
    # A gate-id ref must NOT become a dependency edge (a gate isn't in the node set;
    # an edge would break topo_sort). Construct a flow where the only thing that
    # could order `c` before/after anything is a real ref (to `a`) PLUS a gate-id
    # ref (to `g`). The gate-id ref must contribute no ordering/cycle side-effect:
    # the doc validates clean (no cycle), proving the gate ref made no edge.
    body = VALID + """\
  - id: c
    agent: some-agent
    when: ${g.output.choice}
    prompt: use ${a.output.x} and branch on ${g.output.choice}
gates:
  - id: g
    after: b
    type: human_approval
    prompt: approve?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True
    # No cycle reported (a gate-id edge a/b<->g could only manifest as ordering noise;
    # the clean pass with the real a->c edge intact proves the gate ref added no edge).
    assert not any("cycle" in i["message"] for i in data["issues"])


# --- Reserved wf_ node-id prefix (workflow-input args namespace) ---

def test_wf_prefixed_node_id_blocks(tmp_path):
    # `${workflow.inputs.<x>}` rides as `_args.wf_<x>` in compiled segments and
    # the run-step context — a node id matching ^wf_ silently clobbers (or is
    # clobbered by) a workflow input. Fail loud at validate.
    body = VALID.replace("id: b", "id: wf_b")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("reserved 'wf_' prefix" in i["message"] for i in data["issues"]
               if i["severity"] == "block"), data["issues"]


def test_schema_id_pattern_rejects_wf_prefix():
    # Defense in depth at the schema layer too: the closed node-id pattern
    # itself excludes the reserved prefix (negative lookahead), so a consumer
    # validating against the raw schema is covered even without the tools.
    import re as _re
    schema = json.loads(
        (REPO / "templates" / "references" / "workflow-schema.json").read_text())
    pat = schema["properties"]["nodes"]["items"]["properties"]["id"]["pattern"]
    assert _re.match(pat, "wf_x") is None
    assert _re.match(pat, "wfx") is not None
    assert _re.match(pat, "plan_2") is not None


def test_wf_prefix_without_underscore_is_legal(tmp_path):
    # Only the `wf_` prefix is reserved — ids like `wf` or `wfx` collide with
    # nothing (workflow inputs ride as wf_<name>).
    body = VALID.replace("id: b", "id: wfb")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True, data["issues"]


# --- Branch-exclusivity lint (rank12, STRENGTHENED): bounded comparison analysis ---
# Inside the locked design (branch = exclusivity-lint only): provably-both-fire
# guard pairs are now a BLOCK; same-field pairs neither identical nor provably
# disjoint WARN; provably disjoint pairs stay clean.

def _fork(when_x, when_y):
    return f"""\
version: 1
name: fork
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: x
    agent: ag
    depends_on: [p]
    when: {when_x}
    prompt: branch x
  - id: y
    agent: ag
    depends_on: [p]
    when: {when_y}
    prompt: branch y
"""


def test_identical_when_siblings_block(tmp_path):
    # Two sibling nodes with the SAME depends_on set and TEXTUALLY IDENTICAL when
    # conditions PROVABLY both fire — escalated from the old WARN to a BLOCK.
    rc, data, _ = run(write_wf(tmp_path, _fork("${p.output.ok}", "${p.output.ok}")))
    assert rc == 1 and data["pass"] is False
    blocks = [i for i in data["issues"] if i["severity"] == "block"]
    assert any("BOTH fire" in i["message"] for i in blocks), data["issues"]


def test_opposite_when_fork_no_warn(tmp_path):
    # A proper opposite-when fork (cond vs !(cond)) is provably exclusive
    # (truthy vs falsy on the same field) — NO warn, NO block.
    rc, data, _ = run(write_wf(tmp_path, _fork("${p.output.ok}", "${!(p.output.ok)}")))
    assert rc == 0 and data["pass"] is True
    assert not any("fire" in i["message"] or "disjoint" in i["message"]
                   for i in data["issues"]), data["issues"]


def test_equivalent_guards_via_negation_block(tmp_path):
    # `x != 1` vs `!(x == 1)` — textually different, canonically EQUAL: the bounded
    # comparison parse folds the negation, so the pair provably both fires → block.
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${p.output.n != 1}", "${!(p.output.n == 1)}")))
    assert rc == 1 and data["pass"] is False
    assert any("BOTH fire" in i["message"] for i in data["issues"]
               if i["severity"] == "block"), data["issues"]


def test_bare_negated_comparison_outside_grammar_no_false_block(tmp_path):
    # REGRESSION: JS precedence — unary ! binds tighter than any comparison,
    # so `!a.output.f == 'y'` means `(!a.output.f) == 'y'`, NOT
    # `a.output.f != 'y'`. Folding the negation across the operator is only
    # sound for `!(<comparison>)`; the bare form parses to None (outside the
    # bounded grammar). This pair must NOT be reported provably-both-fire and
    # must not block.
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${!p.output.f == 'y'}",
                                     "${p.output.f != 'y'}")))
    assert rc == 0 and data["pass"] is True, data["issues"]
    assert not any("BOTH fire" in i["message"] or "disjoint" in i["message"]
                   for i in data["issues"]), data["issues"]


def test_bare_negated_field_truthiness_still_folds(tmp_path):
    # `!<field>` with NO comparison stays inside the grammar (JS `!x` is the
    # truthiness negation): vs the bare `x` guard it is provably disjoint.
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${p.output.ok}", "${!p.output.ok}")))
    assert rc == 0 and data["pass"] is True, data["issues"]
    assert not any("fire" in i["message"] or "disjoint" in i["message"]
                   for i in data["issues"]), data["issues"]


def test_identical_bare_negated_comparisons_still_block_textually(tmp_path):
    # The bare-negated form is outside the bounded grammar, but two textually
    # identical copies still provably both fire via the identity fallback.
    w = "${!p.output.f == 'y'}"
    rc, data, _ = run(write_wf(tmp_path, _fork(w, w)))
    assert rc == 1 and data["pass"] is False
    assert any("BOTH fire" in i["message"] for i in data["issues"]
               if i["severity"] == "block"), data["issues"]


def test_disjoint_comparison_forks_stay_clean(tmp_path):
    # Provably disjoint same-field pairs raise NOTHING: == null vs != null,
    # > 0 vs == 0 (touching open/closed interval bounds), distinct == literals.
    cases = [
        ("${p.output.adv == null}", "${p.output.adv != null}"),
        ("${p.output.gaps > 0}", "${p.output.gaps == 0}"),
        ("${p.output.choice == 'approve'}", "${p.output.choice == 'revise'}"),
        ("${p.output.n >= 5}", "${p.output.n < 5}"),
    ]
    for wx, wy in cases:
        rc, data, _ = run(write_wf(tmp_path, _fork(wx, wy)))
        assert rc == 0 and data["pass"] is True, (wx, wy, data["issues"])
        assert not any("fire" in i["message"] or "disjoint" in i["message"]
                       for i in data["issues"]), (wx, wy, data["issues"])


def test_same_field_overlapping_guards_warn(tmp_path):
    # Same-field guards that are neither identical nor provably disjoint — e.g.
    # overlapping numeric ranges — WARN (both branches may fire); pass stays true.
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${p.output.n > 0}", "${p.output.n > 1}")))
    assert rc == 0 and data["pass"] is True
    warns = [i for i in data["issues"] if i["severity"] == "warn"]
    assert any("neither identical nor provably disjoint" in i["message"]
               for i in warns), data["issues"]


def test_different_field_guards_stay_clean(tmp_path):
    # Guards on DIFFERENT fields are outside the bounded analysis — no warn/block
    # (unchanged behavior; only textual identity or same-field analysis fires).
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${p.output.a}", "${p.output.b}")))
    assert rc == 0 and data["pass"] is True
    assert not any("fire" in i["message"] or "disjoint" in i["message"]
                   for i in data["issues"]), data["issues"]


def test_unparseable_identical_text_still_blocks(tmp_path):
    # Guards beyond the bounded grammar (compound expressions) fall back to the
    # textual-identity check: identical compound guards still provably both fire.
    w = "${p.output.a && p.output.b}"
    rc, data, _ = run(write_wf(tmp_path, _fork(w, w)))
    assert rc == 1 and data["pass"] is False
    assert any("BOTH fire" in i["message"] for i in data["issues"]
               if i["severity"] == "block"), data["issues"]


def test_unparseable_different_text_stays_clean(tmp_path):
    # Different unparseable guards: no analysis possible → unchanged (clean).
    rc, data, _ = run(write_wf(tmp_path,
                               _fork("${p.output.a && p.output.b}",
                                     "${p.output.a || p.output.b}")))
    assert rc == 0 and data["pass"] is True
    assert not any("fire" in i["message"] or "disjoint" in i["message"]
                   for i in data["issues"]), data["issues"]


# --- PHASE 5b FEATURE A: ${<id>.items} the per-item-results array ref form ---

ITEMS_REF = """\
version: 1
name: items-flow
inputs:
  items: { type: array, required: true }
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
  - id: collect
    agent: ag
    prompt: summarize ${scan.items}
"""


def test_items_ref_on_for_each_producer_passes(tmp_path):
    # ${scan.items} where scan IS a for_each node is accepted and infers the edge
    # scan->collect (no explicit depends_on required, no unknown-node/misuse block).
    rc, data, _ = run(write_wf(tmp_path, ITEMS_REF))
    assert rc == 0, data
    assert data["pass"] is True
    assert not any("not in depends_on" in i["message"] for i in data["issues"])
    # Specifically: no 'for_each' misuse block and no unknown-node block for scan.
    assert not any("for_each" in i["message"] and "scan" in i["message"]
                   for i in data["issues"] if i["severity"] == "block")
    assert not any("unknown node 'scan'" in i["message"] for i in data["issues"])


def test_items_ref_on_non_for_each_producer_blocks(tmp_path):
    # ${a.items} where a is NOT a for_each node is a clear misuse — there is no
    # per-item array to consume. Block it.
    body = """\
version: 1
name: items-bad
nodes:
  - id: a
    agent: ag
    prompt: plan
  - id: b
    agent: ag
    prompt: consume ${a.items}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("a.items" in i["message"] or
               ("'a'" in i["message"] and "for_each" in i["message"])
               for i in data["issues"]), data["issues"]


def test_items_ref_unknown_node_blocks(tmp_path):
    # ${ghost.items} names a node that does not exist — still a typo guard block.
    body = """\
version: 1
name: items-ghost
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
  - id: collect
    agent: ag
    prompt: summarize ${ghost.items}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("ghost" in i["message"] and
               ("unknown" in i["message"] or "does not" in i["message"])
               for i in data["issues"]), data["issues"]


def test_items_ref_only_cycle_blocks(tmp_path):
    # A ref-only cycle through .items must be caught by the union-cycle check: both
    # are for_each producers, each consuming the other's .items array.
    body = """\
version: 1
name: items-cyc
inputs:
  items: { type: array, required: true }
nodes:
  - id: a
    agent: ag
    for_each: ${b.items}
    as: item
    prompt: scan ${item}
  - id: b
    agent: ag
    for_each: ${a.items}
    as: item
    prompt: scan ${item}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("cycle" in i["message"] for i in data["issues"])


def test_items_field_check_not_triggered(tmp_path):
    # The typed-producer field-check (NODE_OUTPUT_FIELD_RE) must not interfere:
    # .items is not .output.<field>, so a for_each producer with a typed
    # output_schema is never field-flagged for a .items ref.
    body = """\
version: 1
name: items-typed
inputs:
  items: { type: array, required: true }
nodes:
  - id: scan
    agent: ag
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
    output_schema:
      type: object
      properties:
        ok: { type: boolean }
  - id: collect
    agent: ag
    prompt: summarize ${scan.items}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True
    assert not any("does not declare" in i["message"] for i in data["issues"])


# --- PHASE 5b FEATURE B: intra-def vars (double-brace {{key}} pre-pass) ---

def test_vars_substituted_before_validation(tmp_path):
    # {{name}} tokens are substituted from vars BEFORE schema validation; a var
    # resolving the workflow name yields a clean pass.
    body = """\
version: 1
name: vars-flow
vars:
  who: alice
nodes:
  - id: a
    agent: ag
    prompt: greet {{who}}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True


def test_vars_overridable_by_profile(tmp_path):
    # A profile overlay can override a var; the substitution runs after the merge so
    # the overridden value is what gets validated.
    body = """\
version: 1
name: vars-flow
vars:
  who: alice
nodes:
  - id: a
    agent: ag
    when: ${a.output.{{who}}}
    prompt: greet {{who}}
"""
    over = _overlay(tmp_path, "vars:\n  who: bob\n")
    # With who=bob the when-ref becomes ${a.output.bob}; a is its own producer so
    # there is no unknown-node block — what we assert is that the merge+substitution
    # ran (no leftover {{who}} causing a schema/ref anomaly) and it passes.
    rc, data, _ = run(write_wf(tmp_path, body), over)
    # `a` references its own output which is a self-edge dropped by union_edges
    # (r != nid), so this validates clean.
    assert rc == 0, data
    assert data["pass"] is True


def test_unresolved_var_does_not_crash(tmp_path):
    # An unresolved {{missing}} warns (not crash): the token is left intact and
    # validation still completes (here cleanly).
    body = """\
version: 1
name: vars-flow
vars:
  who: alice
nodes:
  - id: a
    agent: ag
    prompt: greet {{who}} but {{missing}} stays
"""
    rc, data, err = run(write_wf(tmp_path, body))
    assert "Traceback" not in err
    assert rc == 0, data
    assert data["pass"] is True


def test_no_vars_validate_unchanged(tmp_path):
    # A workflow with no vars: validates exactly as before (the only issue is
    # VALID's redundant-depends_on warn — no vars-related issue appears).
    rc, data, _ = run(write_wf(tmp_path, VALID))
    assert rc == 0 and data["pass"] is True
    assert not any(i["location"] == "vars" for i in data["issues"])


PHASE_GROUP = """\
version: 1
name: pg-flow
nodes:
  - id: a
    agent: some-agent
    prompt: do a
  - id: rb
    agent: some-agent
    phase_group: review
    prompt: use ${a.output.x}
  - id: rc
    agent: some-agent
    phase_group: review
    prompt: also ${a.output.x}
"""


def test_phase_group_validates_clean(tmp_path):
    # phase_group is an accepted optional node field (schema) with no DAG effect.
    rc, data, _ = run(write_wf(tmp_path, PHASE_GROUP))
    assert rc == 0 and data["pass"] is True
    assert data["issues"] == []


def test_phase_group_id_collision_warns(tmp_path):
    # A phase_group equal to ANOTHER node's id warns (boxes would merge) but does not block.
    body = PHASE_GROUP.replace("phase_group: review", "phase_group: a")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert any(i["severity"] == "warn" and "collides with node id" in i["message"]
               for i in data["issues"])


def test_phase_group_equal_to_own_id_does_not_warn(tmp_path):
    # A node whose phase_group equals its OWN id is a no-op (its default group already
    # IS its id) — nothing merges, so it must NOT warn (no false positive).
    body = """\
version: 1
name: pg-self
nodes:
  - id: a
    agent: some-agent
    phase_group: a
    prompt: do a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert not any("collides with node id" in i["message"] for i in data["issues"])


# --- FAIL-LOUD CLASSIFICATION (validate-side static checks) ---
# Mirrors compile-workflow's hard die paths: use:-target existence (behind
# --defs-root), delegation: auto contradictions, and the statically-detectable
# orchestrator loop-body members.

USE_MS_MD = """\
---
name: real-ms
description: minimal microskill for use:-existence tests
---

# real-ms

## Purpose

Do the thing.

## Steps

1. Return the result.
"""

USE_WF = """\
version: 1
name: use-flow
nodes:
  - id: u
    use: real-ms
"""


def make_use_world(tmp_path, wf_body, with_ms=True):
    """<tmp>/workflow-defs/use-flow + (optionally) <tmp>/microskills/real-ms —
    the sibling-skill-root layout compile derives from --defs-root."""
    defs_root = tmp_path / "workflow-defs"
    if with_ms:
        mdir = tmp_path / "microskills" / "real-ms" / "profiles"
        mdir.mkdir(parents=True)
        (tmp_path / "microskills" / "real-ms" / "MICROSKILL.md").write_text(USE_MS_MD)
        (mdir / "base.yaml").write_text("version: 1\n")
    return make_def(defs_root, "use-flow", wf_body), defs_root


def test_use_target_present_passes_with_defs_root(tmp_path):
    wf, defs_root = make_use_world(tmp_path, USE_WF, with_ms=True)
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True


def test_use_target_missing_blocks_with_defs_root(tmp_path):
    # The target microskill does not exist under the sibling microskills/ root →
    # block (compile fails loud on the same condition).
    wf, defs_root = make_use_world(tmp_path, USE_WF, with_ms=False)
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 1 and data["pass"] is False
    assert any("use: 'real-ms' does not resolve" in i["message"] for i in data["issues"])


def test_use_target_missing_without_defs_root_passes(tmp_path):
    # Without --defs-root the existence check is skipped — hermetic single-file
    # validation stays backward-compatible.
    wf, _ = make_use_world(tmp_path, USE_WF, with_ms=False)
    rc, data, _ = run(wf)
    assert rc == 0, data
    assert data["pass"] is True


def test_use_target_missing_orchestrator_escape_hatch_passes(tmp_path):
    # Explicit delegation: orchestrator skips resolution in compile, so the
    # existence check skips it too.
    body = USE_WF + "    delegation: orchestrator\n    prompt: by hand\n"
    wf, defs_root = make_use_world(tmp_path, body, with_ms=False)
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True


def test_delegation_auto_on_workflow_node_blocks(tmp_path):
    make_def(tmp_path, "child-flow", CHILD)
    body = PARENT.replace("    workflow: child-flow\n",
                          "    workflow: child-flow\n    delegation: auto\n")
    parent = make_def(tmp_path, "parent-flow", body)
    rc, data, _ = run_defs(tmp_path, parent)
    assert rc == 1 and data["pass"] is False
    assert any("delegation: auto on a workflow: node" in i["message"]
               for i in data["issues"])


def test_side_effect_with_delegation_auto_blocks(tmp_path):
    body = """\
version: 1
name: contra-flow
nodes:
  - id: s
    agent: ag
    prompt: do it
    side_effect: true
    delegation: auto
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("contradicts delegation: auto" in i["message"] for i in data["issues"])


LOOP_ORCH_BODY = """\
version: 1
name: loop-orch-flow
nodes:
  - id: p
    agent: ag
    prompt: plan
  - id: impl
    delegation: orchestrator
    depends_on: [p]
    prompt: impl by hand
  - id: ev
    agent: ag
    depends_on: [impl]
    prompt: ev
loop:
  while: ${!ev.output.pass}
  max_iters: 2
  body: [impl, ev]
"""


def test_loop_body_explicit_orchestrator_member_blocks(tmp_path):
    # Statically-detectable subset of compile's loop-body fail-loud: an explicit
    # delegation: orchestrator member blocks (no resolution needed).
    rc, data, _ = run(write_wf(tmp_path, LOOP_ORCH_BODY))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "loop/body" and "do/while" in i["message"]
               for i in data["issues"])


def test_loop_body_side_effect_member_blocks(tmp_path):
    body = LOOP_ORCH_BODY.replace("    delegation: orchestrator\n",
                                  "    agent: ag\n    side_effect: true\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "loop/body" and "orchestrator" in i["message"]
               for i in data["issues"])


def test_loop_body_background_members_pass(tmp_path):
    # Control: an all-background body (use/agent, no orchestrator markers) passes.
    body = LOOP_ORCH_BODY.replace("    delegation: orchestrator\n", "    agent: ag\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True


# =============================================================================
# 3.2 / 3.6 — customize closed to {profile?, overrides?}; overrides + retry
# placement blocks (mirror compile's hard dies, statically-detectable subset).
# =============================================================================

USE_OVERRIDES = """\
version: 1
name: ov-flow
nodes:
  - id: e
    use: some-ms
    customize:
      profile: fast
      overrides:
        runtime.model: haiku
"""


def test_customize_profile_and_overrides_on_use_node_pass(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, USE_OVERRIDES))
    assert rc == 0, data
    assert data["pass"] is True


def test_customize_unknown_key_blocks_schema(tmp_path):
    body = USE_OVERRIDES.replace("      profile: fast\n", "      profilee: oops\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("profilee" in i["message"] for i in data["issues"]
               if i["severity"] == "block")


def test_customize_overrides_on_agent_node_blocks(tmp_path):
    body = """\
version: 1
name: ov-flow
nodes:
  - id: a
    agent: ag
    customize:
      overrides:
        runtime.model: haiku
    prompt: do a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("customize.overrides is only valid on a use:" in i["message"]
               for i in data["issues"])


def test_customize_overrides_on_workflow_node_blocks(tmp_path):
    body = """\
version: 1
name: ov-flow
imports: [child-flow]
nodes:
  - id: w
    workflow: child-flow
    customize:
      overrides:
        runtime.model: haiku
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("customize.overrides is only valid on a use:" in i["message"]
               for i in data["issues"])


def test_customize_overrides_under_delegation_orchestrator_blocks(tmp_path):
    body = USE_OVERRIDES.replace("    use: some-ms\n",
                                 "    use: some-ms\n    delegation: orchestrator\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("resolution is skipped" in i["message"] for i in data["issues"])


def test_customize_overrides_under_side_effect_alias_blocks(tmp_path):
    body = USE_OVERRIDES.replace("    use: some-ms\n",
                                 "    use: some-ms\n    side_effect: true\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("resolution is skipped" in i["message"] for i in data["issues"])


RETRY_OK = """\
version: 1
name: rt-flow
nodes:
  - id: a
    agent: ag
    retry: { max_attempts: 3 }
    prompt: do a
  - id: e
    use: some-ms
    retry: { max_attempts: 2 }
    depends_on: [a]
"""


def test_retry_on_use_and_agent_nodes_passes(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, RETRY_OK))
    assert rc == 0, data
    assert data["pass"] is True


def test_retry_max_attempts_below_two_blocks_schema(tmp_path):
    body = RETRY_OK.replace("max_attempts: 2", "max_attempts: 1")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("minimum" in i["message"] or "less than" in i["message"]
               for i in data["issues"] if i["severity"] == "block")


def test_retry_on_workflow_node_blocks(tmp_path):
    body = """\
version: 1
name: rt-flow
imports: [child-flow]
nodes:
  - id: w
    workflow: child-flow
    retry: { max_attempts: 2 }
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("retry is only valid on a background use:/agent: node" in i["message"]
               for i in data["issues"])


def test_retry_on_orchestrator_native_node_blocks(tmp_path):
    body = """\
version: 1
name: rt-flow
nodes:
  - id: fin
    delegation: orchestrator
    retry: { max_attempts: 2 }
    prompt: finalize
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("retry is only valid on a background use:/agent: node" in i["message"]
               for i in data["issues"])


def test_retry_under_explicit_delegation_orchestrator_blocks(tmp_path):
    body = """\
version: 1
name: rt-flow
nodes:
  - id: a
    agent: ag
    delegation: orchestrator
    retry: { max_attempts: 2 }
    prompt: do a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("explicit orchestrator checkpoint" in i["message"]
               for i in data["issues"])


# --- {{snippet:NAME}} includes (validate mirrors compile's pre-pass) ---

SNIP_WF = """\
version: 1
name: snip-flow
vars:
  topic: kubernetes
nodes:
  - id: a
    agent: ag
    prompt: "{{snippet:greet}} now"
"""


def _snip_world(tmp_path, snippet_text=None):
    """Standard <defs-root>/<name>/WORKFLOW.yaml layout + optional _snippets/greet.md."""
    defs = tmp_path / "defs"
    d = defs / "snip-flow"
    d.mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(SNIP_WF)
    if snippet_text is not None:
        (defs / "_snippets").mkdir()
        (defs / "_snippets" / "greet.md").write_text(snippet_text)
    return d / "WORKFLOW.yaml", defs


def test_snippet_resolves_via_derived_defs_root(tmp_path):
    # Without --defs-root, the snippets root derives from the standard
    # <defs-root>/<name>/WORKFLOW.yaml layout (parent of the def dir).
    wf, _ = _snip_world(tmp_path, "research {{topic}} thoroughly\n")
    rc, data, _ = run(wf)
    assert rc == 0, data
    assert data["pass"] is True
    # the snippet's {{topic}} var resolved (no unresolved-var warn for it)
    assert not any("topic" in i["message"] for i in data["issues"]), data["issues"]


def test_snippet_resolves_via_explicit_defs_root(tmp_path):
    wf, defs = _snip_world(tmp_path, "research {{topic}} thoroughly\n")
    rc, data, _ = run(wf, "--defs-root", str(defs))
    assert rc == 0, data
    assert data["pass"] is True


def test_missing_snippet_blocks_validate(tmp_path):
    # Unresolvable snippet → HARD block (mirrors compile's die), never a warn.
    wf, _ = _snip_world(tmp_path, snippet_text=None)
    rc, data, _ = run(wf)
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "snippets" and "greet" in i["message"]
               for i in data["issues"] if i["severity"] == "block"), data["issues"]


# --- compile-time expand: (validate mirrors compile's shared desugar) ---

EXPAND_WF = """\
version: 1
name: fan-flow
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: scan
    agent: ag
    expand:
      over: [alpha, beta]
    prompt: scan {{each.item}} with ${seed.output.x}
  - id: gather
    agent: ag
    inputs_each: scan
    prompt: gather everything
"""


def test_expand_desugars_and_validates(tmp_path):
    # The sugar never reaches the closed node schema: the template is replaced by
    # ordinary generated siblings pre-validation, the fan-in wires real node ids,
    # and the result passes clean.
    rc, data, _ = run(write_wf(tmp_path, EXPAND_WF))
    assert rc == 0, data
    assert data["pass"] is True
    assert data["issues"] == []


def test_expand_bad_shape_blocks_validate(tmp_path):
    body = EXPAND_WF.replace("over: [alpha, beta]", "over: []")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "expand" and "non-empty" in i["message"]
               for i in data["issues"]), data["issues"]


def test_inputs_each_unknown_template_blocks_validate(tmp_path):
    body = EXPAND_WF.replace("inputs_each: scan", "inputs_each: seed")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "expand" and "inputs_each" in i["message"]
               for i in data["issues"]), data["issues"]


def test_expand_leftover_each_token_blocks_validate(tmp_path):
    body = EXPAND_WF.replace("{{each.item}}", "{{each.profile}}")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any(i["location"] == "expand" and "each" in i["message"]
               for i in data["issues"]), data["issues"]


def test_real_review_changes_validates_with_expand():
    # The flagship adoption validates clean under all three profiles.
    rc_dir = REPO / "catalog" / "workflow-defs" / "review-changes"
    base = rc_dir / "profiles" / "base.yaml"
    for overlays in ([], ["comprehensive.yaml"], ["lite.yaml"]):
        paths = [rc_dir / "WORKFLOW.yaml", base] + [rc_dir / "profiles" / o for o in overlays]
        rc, data, _ = run(*paths)
        assert rc == 0, (overlays, data)
        assert data["pass"] is True, (overlays, data["issues"])


# =============================================================================
# 2.1 — validator-side schema inheritance: the typed-ref (S-FIELD) check falls
# back to the use: target's RESOLVED output_schema (same resolve-microskill
# subprocess compile uses); two-tier inline-schema redundancy lint; unresolvable
# use: is WARN by default (hermetic standalone validation keeps passing) and
# escalates to a block under --strict.
# =============================================================================

SCHEMA_MS_MD = """\
---
name: schema-ms
description: minimal microskill with a resolved output_schema
---

# Schema MS

## Purpose

Emit a typed result.

## Steps

1. Return the result.
"""

SCHEMA_MS_BASE = """\
version: 1
output_schema:
  type: object
  required: [echoed]
  properties:
    echoed: { type: string }
    score: { type: number }
"""


def make_schema_world(tmp_path, wf_body, ms_base=SCHEMA_MS_BASE, with_ms=True):
    """<tmp>/workflow-defs/sch-flow + <tmp>/microskills/schema-ms — the
    sibling-skill-root layout both tools derive from the defs root."""
    defs_root = tmp_path / "workflow-defs"
    if with_ms:
        mdir = tmp_path / "microskills" / "schema-ms" / "profiles"
        mdir.mkdir(parents=True)
        (tmp_path / "microskills" / "schema-ms" / "MICROSKILL.md").write_text(SCHEMA_MS_MD)
        (mdir / "base.yaml").write_text(ms_base)
    return make_def(defs_root, "sch-flow", wf_body), defs_root


SFIELD_FALLBACK_WF = """\
version: 1
name: sch-flow
nodes:
  - id: u
    use: schema-ms
  - id: c
    agent: ag
    prompt: use ${u.output.%s}
"""


def test_sfield_falls_back_to_resolved_schema_blocks_unknown_field(tmp_path):
    # `u` declares NO inline output_schema; the field check must fall back to the
    # RESOLVED microskill schema and block the undeclared field.
    wf, defs_root = make_schema_world(tmp_path, SFIELD_FALLBACK_WF % "ghost")
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 1 and data["pass"] is False
    assert any("ghost" in i["message"] and "does not declare" in i["message"]
               for i in data["issues"]), data["issues"]


def test_sfield_fallback_known_field_passes(tmp_path):
    wf, defs_root = make_schema_world(tmp_path, SFIELD_FALLBACK_WF % "echoed")
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True


def test_sfield_fallback_engages_without_defs_root(tmp_path):
    # Standalone validation derives the defs root from the standard
    # <defs-root>/<name>/WORKFLOW.yaml layout, so resolution still engages.
    wf, _ = make_schema_world(tmp_path, SFIELD_FALLBACK_WF % "ghost")
    rc, data, _ = run(wf)
    assert rc == 1 and data["pass"] is False
    assert any("ghost" in i["message"] and "does not declare" in i["message"]
               for i in data["issues"]), data["issues"]


def test_inline_schema_deep_equal_resolved_warns_omit(tmp_path):
    # Inline schema deep-equal to the resolved one → redundancy lint tier 1: warn
    # 'omit it' (never a block).
    body = """\
version: 1
name: sch-flow
nodes:
  - id: u
    use: schema-ms
    output_schema:
      type: object
      required: [echoed]
      properties:
        echoed: { type: string }
        score: { type: number }
"""
    wf, defs_root = make_schema_world(tmp_path, body)
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True
    assert any(i["severity"] == "warn" and "omit" in i["message"]
               and i["location"] == "nodes/u/output_schema"
               for i in data["issues"]), data["issues"]


def test_inline_schema_divergent_warns_reconcile(tmp_path):
    # Inline schema diverging from the resolved one → tier 2: warn 'reconcile or
    # document the narrowing'.
    body = """\
version: 1
name: sch-flow
nodes:
  - id: u
    use: schema-ms
    output_schema:
      type: object
      required: [echoed]
      properties:
        echoed: { type: string }
        extra_field: { type: string }
"""
    wf, defs_root = make_schema_world(tmp_path, body)
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True
    assert any(i["severity"] == "warn" and "reconcile" in i["message"]
               and i["location"] == "nodes/u/output_schema"
               for i in data["issues"]), data["issues"]


def test_unresolvable_use_warns_by_default_standalone(tmp_path):
    # No microskills/ sibling at all and no --defs-root: hermetic standalone
    # validation must keep passing — unresolvable use: is a WARN.
    wf, _ = make_schema_world(tmp_path, SFIELD_FALLBACK_WF % "anything", with_ms=False)
    rc, data, _ = run(wf)
    assert rc == 0, data
    assert data["pass"] is True
    assert any(i["severity"] == "warn" and "does not resolve" in i["message"]
               for i in data["issues"]), data["issues"]


def test_strict_escalates_unresolvable_use_to_block(tmp_path):
    wf, _ = make_schema_world(tmp_path, SFIELD_FALLBACK_WF % "anything", with_ms=False)
    rc, data, _ = run(wf, "--strict")
    assert rc == 1 and data["pass"] is False
    assert any(i["severity"] == "block" and "does not resolve" in i["message"]
               for i in data["issues"]), data["issues"]


def test_resolution_failure_with_defs_root_blocks(tmp_path):
    # MICROSKILL.md exists but its base.yaml is unparseable: with --defs-root
    # (full-registry validation) a failed resolution is a block, mirroring
    # compile's hard die. (A missing customize.profile is NOT a failure — the
    # resolver falls back to base with a warning.)
    wf, defs_root = make_schema_world(
        tmp_path, SFIELD_FALLBACK_WF % "echoed",
        ms_base="version: 1\n  bad_indent: [\n")
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 1 and data["pass"] is False
    assert any("failed to resolve" in i["message"] for i in data["issues"]
               if i["severity"] == "block"), data["issues"]


def test_explicit_delegation_orchestrator_skips_resolution_lints(tmp_path):
    # The escape hatch skips resolution in compile, so validate skips the
    # resolution-driven lints too — no unresolvable warn even though the target
    # is missing.
    body = """\
version: 1
name: sch-flow
nodes:
  - id: u
    use: schema-ms
    delegation: orchestrator
"""
    wf, _ = make_schema_world(tmp_path, body, with_ms=False)
    rc, data, _ = run(wf)
    assert rc == 0, data
    assert data["pass"] is True
    assert not any("does not resolve" in i["message"] for i in data["issues"])


# =============================================================================
# 2.2 — lint pack: redundant depends_on (refs already imply the edge) with fix
# text; every ${workflow.inputs.x} ref must appear in the declared inputs map.
# =============================================================================

def test_redundant_depends_on_warns_with_fix_text(tmp_path):
    # VALID's node b restates the ref-implied edge a->b in depends_on → warn.
    rc, data, _ = run(write_wf(tmp_path, VALID))
    assert rc == 0 and data["pass"] is True
    w = [i for i in data["issues"]
         if i["severity"] == "warn" and i["location"] == "nodes/b/depends_on"]
    assert len(w) == 1, data["issues"]
    assert "redundant" in w[0]["message"] and "drop" in w[0]["message"]


def test_pure_ordering_depends_on_does_not_warn(tmp_path):
    # An explicit edge with NO matching ref is the legitimate use of depends_on.
    body = """\
version: 1
name: tiny-flow
nodes:
  - id: a
    agent: some-agent
    prompt: do a
  - id: b
    agent: some-agent
    depends_on: [a]
    prompt: do b after a
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert not any("redundant" in i["message"] for i in data["issues"])


def test_items_ref_redundant_depends_on_warns(tmp_path):
    # A ${id.items} ref implies the edge exactly like ${id.output}.
    body = """\
version: 1
name: items-flow
nodes:
  - id: fan
    agent: ag
    for_each: ${workflow.inputs.xs}
    as: x
    prompt: do ${x}
  - id: join
    agent: ag
    depends_on: [fan]
    prompt: join ${fan.items}
inputs:
  xs: { type: array }
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0 and data["pass"] is True
    assert any("redundant" in i["message"] and i["location"] == "nodes/join/depends_on"
               for i in data["issues"]), data["issues"]


def test_inputs_each_generated_fanin_not_linted(tmp_path):
    # inputs_each desugars to inputs + an explicit depends_on per generated
    # sibling BY CONSTRUCTION — the lint runs on the hand-authored (pre-expand)
    # shape only, so the generated fan-in never warns.
    body = """\
version: 1
name: fan-flow
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: scan
    agent: ag
    expand:
      over: [alpha, beta]
    prompt: scan {{each.item}} with ${seed.output.x}
  - id: gather
    agent: ag
    inputs_each: scan
    prompt: gather everything
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True
    assert not any("redundant" in i["message"] for i in data["issues"]), data["issues"]


def test_hand_authored_template_redundant_depends_on_warns(tmp_path):
    # ...but a hand-authored redundant depends_on ON the template itself warns.
    body = """\
version: 1
name: fan-flow
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: scan
    agent: ag
    depends_on: [seed]
    expand:
      over: [alpha, beta]
    prompt: scan {{each.item}} with ${seed.output.x}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert any("redundant" in i["message"] and i["location"] == "nodes/scan/depends_on"
               for i in data["issues"]), data["issues"]


def test_undeclared_workflow_input_ref_blocks(tmp_path):
    body = """\
version: 1
name: wf-in
inputs:
  diff_path: { type: string }
nodes:
  - id: a
    agent: ag
    prompt: read ${workflow.inputs.dif_path}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("dif_path" in i["message"] and "not declared" in i["message"]
               for i in data["issues"] if i["severity"] == "block"), data["issues"]


def test_declared_workflow_input_ref_passes(tmp_path):
    body = """\
version: 1
name: wf-in
inputs:
  diff_path: { type: string }
nodes:
  - id: a
    agent: ag
    prompt: read ${workflow.inputs.diff_path}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True


def test_undeclared_input_with_no_inputs_map_blocks(tmp_path):
    body = """\
version: 1
name: wf-in
nodes:
  - id: a
    agent: ag
    prompt: read ${workflow.inputs.anything}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("anything" in i["message"] and "not declared" in i["message"]
               for i in data["issues"]), data["issues"]


def test_input_declared_by_profile_overlay_passes(tmp_path):
    # comprehensive.yaml pattern: the overlay declares the input the patched
    # node references — membership is checked on the MERGED doc.
    wf = write_wf(tmp_path, """\
version: 1
name: wf-in
inputs:
  base_in: { type: string }
nodes:
  - id: a
    agent: ag
    prompt: read ${workflow.inputs.base_in} and ${workflow.inputs.extra_in}
""")
    overlay = tmp_path / "extra.yaml"
    overlay.write_text("version: 1\ninputs:\n  extra_in: { type: string }\n")
    rc, data, _ = run(wf, overlay)
    assert rc == 0, data
    assert data["pass"] is True


def test_undeclared_input_in_gate_prompt_blocks(tmp_path):
    body = VALID + """\
gates:
  - id: g1
    after: a
    type: human_approval
    prompt: approve ${workflow.inputs.nope}?
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("nope" in i["message"] and "not declared" in i["message"]
               for i in data["issues"]), data["issues"]


# ---------------------------------------------------------------------------
# Declared gate policy (default / on_headless / gate_mode), present:, and the
# gate-choice literal cross-check
# ---------------------------------------------------------------------------

CHOICE_FLOW = """\
version: 1
name: choice-flow
nodes:
  - id: plan
    agent: ag
    prompt: do plan
    output_schema:
      type: object
      properties:
        name: { type: string }
        plan_path: { type: string }
      required: [name]
  - id: deep
    agent: ag
    when: ${review.output.choice == 'deep_review'}
    prompt: deep using ${plan.output.name}
gates:
  - id: review
    after: plan
    type: human_approval
    prompt: Approve, or send to deep review?
    options: [approve, deep_review]
"""


def test_choice_literal_in_options_passes(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, CHOICE_FLOW))
    assert rc == 0, data
    assert data["pass"] is True


def test_choice_literal_typo_blocks(tmp_path):
    body = CHOICE_FLOW.replace("== 'deep_review'", "== 'Deep_Review'")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("'Deep_Review'" in i["message"] and "review" in i["message"]
               for i in data["issues"]), data["issues"]


def test_choice_literal_neq_nonoption_blocks(tmp_path):
    # != against a label the gate never offers is ALWAYS true — equally dead.
    body = CHOICE_FLOW.replace("== 'deep_review'", "!= 'nope'")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("'nope'" in i["message"] for i in data["issues"])


def test_choice_literal_reversed_operands_checked(tmp_path):
    body = CHOICE_FLOW.replace(
        "${review.output.choice == 'deep_review'}",
        "${'Deep_Review' == review.output.choice}")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("'Deep_Review'" in i["message"] for i in data["issues"])


def test_choice_literal_optionless_gate_uses_confirm_stop(tmp_path):
    # An options-less gate offers the implicit confirm/stop pair.
    base = CHOICE_FLOW.replace("    options: [approve, deep_review]\n", "")
    ok = base.replace("== 'deep_review'", "== 'confirm'")
    rc, data, _ = run(write_wf(tmp_path, ok))
    assert rc == 0, data
    bad = base.replace("== 'deep_review'", "== 'approve'")
    rc2, data2, _ = run(write_wf(tmp_path, bad))
    assert rc2 == 1
    assert any("confirm/stop" in i["message"] for i in data2["issues"])


def test_gate_default_member_checked(tmp_path):
    ok = CHOICE_FLOW + "    default: approve\n"
    rc, data, _ = run(write_wf(tmp_path, ok))
    assert rc == 0, data
    bad = CHOICE_FLOW + "    default: yes-do-it\n"
    rc2, data2, _ = run(write_wf(tmp_path, bad))
    assert rc2 == 1
    assert any("yes-do-it" in i["message"] and i["location"] == "gates/review"
               for i in data2["issues"]), data2["issues"]


def test_on_headless_take_default_requires_default(tmp_path):
    body = CHOICE_FLOW + "    on_headless: take_default\n"
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("take_default" in i["message"] for i in data["issues"])


def test_gate_mode_auto_blocks_defaultless_hard_gate(tmp_path):
    body = "gate_mode: auto\n" + CHOICE_FLOW
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("headless" in i["message"] and i["location"] == "gates/review"
               for i in data["issues"]), data["issues"]
    # A declared default clears it; so does an explicit on_headless: fail.
    rc2, data2, _ = run(write_wf(tmp_path, body + "    default: approve\n"))
    assert rc2 == 0, data2
    rc3, data3, _ = run(write_wf(tmp_path, body + "    on_headless: fail\n"))
    assert rc3 == 0, data3


def test_gate_mode_bad_value_fails_schema(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, "gate_mode: yolo\n" + CHOICE_FLOW))
    assert rc == 1
    assert any(i["location"].startswith("schema:gate_mode") for i in data["issues"])


def test_warn_gate_branched_on_forces_default(tmp_path):
    # The dispatcher records a warn gate's DECLARED default (or {choice: null}) —
    # never a fabricated pick — so a branched-on warn gate must declare one.
    body = CHOICE_FLOW.replace("    prompt: Approve, or send to deep review?\n",
                               "    severity: warn\n"
                               "    prompt: Approve, or send to deep review?\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("default" in i["message"] and i["location"] == "gates/review"
               for i in data["issues"]), data["issues"]
    rc2, data2, _ = run(write_wf(tmp_path, body + "    default: approve\n"))
    assert rc2 == 0, data2


def test_warn_gate_not_branched_on_needs_no_default(tmp_path):
    # Without any ${review.output.choice} ref, a defaultless warn gate is fine.
    body = CHOICE_FLOW.replace("    when: ${review.output.choice == 'deep_review'}\n", "") \
                      .replace("    prompt: Approve, or send to deep review?\n",
                               "    severity: warn\n"
                               "    prompt: Approve, or send to deep review?\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data


def test_warn_nonapproval_gate_branched_on_blocks(tmp_path):
    # A warn verification gate emits NO checkpoint at all — its choice is never
    # recorded, so branching on it is always broken.
    body = CHOICE_FLOW.replace("    type: human_approval\n",
                               "    type: verification\n    severity: warn\n") \
                      .replace("    prompt: Approve, or send to deep review?\n", "") \
                      .replace("    options: [approve, deep_review]\n", "") \
                      .replace("== 'deep_review'", "== 'confirm'")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("no checkpoint" in i["message"] for i in data["issues"]), data["issues"]


PRESENT_FLOW = CHOICE_FLOW.replace("    options: [approve, deep_review]\n", """\
    options: [approve, deep_review]
    present:
      - plan.output.name
      - read_file: plan.output.plan_path
""")


def test_present_valid_paths_pass(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, PRESENT_FLOW))
    assert rc == 0, data
    assert data["pass"] is True


def test_present_unknown_node_blocks(tmp_path):
    body = PRESENT_FLOW.replace("- plan.output.name", "- ghost.output.name")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any(i["location"] == "gates/review/present"
               and "unknown node 'ghost'" in i["message"] for i in data["issues"])


def test_present_undeclared_field_blocks(tmp_path):
    body = PRESENT_FLOW.replace("- plan.output.name", "- plan.output.bogus")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("does not declare property 'bogus'" in i["message"]
               for i in data["issues"]), data["issues"]


def test_present_later_node_blocks(tmp_path):
    # 'deep' runs after the gate's anchor 'plan' — not yet produced when the
    # gate fires.
    body = PRESENT_FLOW.replace("- plan.output.name", "- deep.output.name")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("not yet produced" in i["message"] for i in data["issues"]), data["issues"]


def test_present_malformed_entry_fails_schema(tmp_path):
    body = PRESENT_FLOW.replace("- plan.output.name", "- Plan Output Name")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any(i["location"].startswith("schema:gates") for i in data["issues"])


def test_present_gate_self_and_field_rules(tmp_path):
    # A gate may present an EARLIER gate's recorded choice; its own (not yet
    # recorded) and any non-choice field are blocks.
    body = PRESENT_FLOW.replace("- plan.output.name", "- review.output.choice")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert any("own choice" in i["message"] for i in data["issues"]), data["issues"]
    two_gates = PRESENT_FLOW.replace("- plan.output.name", "- review.output.choice") + """\
  - id: early
    after: plan
    type: human_approval
    prompt: early check?
"""
    # 'early' is declared AFTER 'review' on the same anchor, so review still
    # cannot present it; flip the reference to the earlier-declared gate instead.
    body2 = two_gates.replace("- review.output.choice", "- early.output.choice")
    rc2, data2, _ = run(write_wf(tmp_path, body2))
    assert rc2 == 1
    assert any("not yet recorded" in i["message"] for i in data2["issues"]), data2["issues"]


# ---------------------------------------------------------------------------
# spill_outputs — declared node-output-by-reference (shared helpers with
# compile-workflow, so the two tools can never disagree): placement (no
# for_each fan-out producer), schema coherence (spilled fields must exist in
# the producer's EFFECTIVE output schema — inline wins, else the resolved
# microskill schema), and the HARD-BLOCK on guards (when / for_each / loop
# while/until) referencing a spilled field — across a checkpoint the
# dispatcher threads it as a handoff file PATH, and a path is not the value.


SPILL_VWF = """\
version: 1
name: spill-flow
nodes:
  - id: review
    agent: some-agent
    prompt: review it
    output_schema:
      type: object
      required: [report, count]
      properties:
        report: { type: string }
        count: { type: integer }
    spill_outputs: [report]
  - id: post
    agent: some-agent
    prompt: post ${review.output.report} (${review.output.count} findings)
gates:
  - id: g1
    after: review
    type: human_approval
    prompt: ok?
"""


def test_spill_clean_def_passes(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, SPILL_VWF))
    assert rc == 0, data
    assert data["pass"] is True


def test_spill_field_in_when_guard_blocks(tmp_path):
    body = SPILL_VWF.replace(
        "    prompt: post ${review.output.report} (${review.output.count} findings)\n",
        "    when: ${review.output.report == 'ok'}\n"
        "    prompt: post it\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert "nodes/post/when" in locs(data)
    assert any("spill_outputs" in i["message"] and "PATH" in i["message"]
               for i in data["issues"])


def test_spill_unspilled_field_in_when_guard_passes(tmp_path):
    # Branching on a small UNSPILLED field of the same producer is the
    # documented pattern — never flagged.
    body = SPILL_VWF.replace(
        "    prompt: post ${review.output.report} (${review.output.count} findings)\n",
        "    when: ${review.output.count > 0}\n"
        "    prompt: post ${review.output.report}\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True


def test_spill_field_in_for_each_blocks(tmp_path):
    body = """\
version: 1
name: sp-fe
nodes:
  - id: collect
    agent: some-agent
    prompt: collect
    output_schema:
      type: object
      properties:
        findings: { type: array }
    spill_outputs: [findings]
  - id: verify
    agent: some-agent
    for_each: ${collect.output.findings}
    as: f
    prompt: verify ${f}
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert "nodes/verify/for_each" in locs(data)


def test_spill_field_in_loop_until_blocks(tmp_path):
    # The helper scans `until` as well as `while`, so the block is
    # desugar-order-independent.
    body = """\
version: 1
name: sp-loop
nodes:
  - id: impl
    agent: some-agent
    prompt: impl
  - id: ev
    agent: some-agent
    depends_on: [impl]
    prompt: ev
    output_schema:
      type: object
      properties:
        report: { type: string }
    spill_outputs: [report]
loop:
  until: ${ev.output.report == 'done'}
  max_iters: 2
  body: [impl, ev]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert "loop/until" in locs(data)


def test_spill_on_for_each_fan_out_node_blocks(tmp_path):
    body = """\
version: 1
name: sp-fan
inputs:
  items:
    type: array
    required: true
nodes:
  - id: scan
    agent: some-agent
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
    spill_outputs: [report]
"""
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert "nodes/scan/spill_outputs" in locs(data)
    assert any("ARRAY" in i["message"] for i in data["issues"])


def test_spill_field_not_in_inline_schema_blocks(tmp_path):
    body = SPILL_VWF.replace("spill_outputs: [report]",
                             "spill_outputs: [reprot]")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1
    assert "nodes/review/spill_outputs" in locs(data)
    assert any("reprot" in i["message"] for i in data["issues"])


def test_spill_schema_less_producer_is_any_and_passes(tmp_path):
    body = SPILL_VWF.replace("""\
    output_schema:
      type: object
      required: [report, count]
      properties:
        report: { type: string }
        count: { type: integer }
""", "")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0, data
    assert data["pass"] is True


SPILL_RESOLVED_WF = """\
version: 1
name: sch-flow
nodes:
  - id: u
    use: schema-ms
    spill_outputs: [%s]
  - id: c
    agent: ag
    prompt: use ${u.output.echoed}
"""


def test_spill_field_checked_against_resolved_microskill_schema(tmp_path):
    # Validator-side schema inheritance: a use: node with no inline schema
    # falls back to the RESOLVED microskill output_schema for the spill
    # coherence check — a spilled field the microskill never produces blocks.
    wf, defs_root = make_schema_world(tmp_path, SPILL_RESOLVED_WF % "transcript")
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 1 and data["pass"] is False
    assert "nodes/u/spill_outputs" in locs(data)
    assert any("transcript" in i["message"] for i in data["issues"])


def test_spill_field_in_resolved_microskill_schema_passes(tmp_path):
    wf, defs_root = make_schema_world(tmp_path, SPILL_RESOLVED_WF % "echoed")
    rc, data, _ = run_defs(defs_root, wf)
    assert rc == 0, data
    assert data["pass"] is True


# ================================================================== loop.on_exhaust

V_OX_BASE = """\
version: 1
name: ox-flow
description: cap-exhaustion policy fixture
inputs:
  req:
    type: string
    required: true
nodes:
  - id: plan
    agent: ag
    prompt: plan ${workflow.inputs.req}
  - id: impl
    agent: ag
    depends_on: [plan]
    prompt: impl ${plan.output.spec} notes ${loop.carry.last}
  - id: ev
    agent: ag
    depends_on: [impl]
    prompt: eval ${impl.output.art}
    output_schema: {type: object, required: [pass], properties: {pass: {type: boolean}}}
  - id: done
    delegation: orchestrator
    when: ${loop.output.converged || loop_exhaust.output.choice == 'accept'}
    prompt: finalize ${ev.output}
gates:
  - id: approve
    after: plan
    type: human_approval
    prompt: ok?
    options: [approve, abandon]
loop:
  while: ${!ev.output.pass}
  max_iters: 3
  body: [impl, ev]
  carry:
    last: ${ev.output}
output:
  from: done
"""

def _append_to_loop(body, extra):
    """Append an indented block to the loop: section (before output:)."""
    return body.replace("output:\n  from: done\n", "") + extra + "output:\n  from: done\n"


V_OX_ESCALATE = _append_to_loop(V_OX_BASE, """\
  on_exhaust:
    action: escalate
    notes_input: req
    on_headless: fail
""")


def test_on_exhaust_escalate_accepts_pseudo_producers(tmp_path):
    rc, data, err = run(write_wf(tmp_path, V_OX_ESCALATE))
    assert data["pass"] is True, data["issues"]


def test_loop_output_ref_without_escalate_blocks(tmp_path):
    rc, data, err = run(write_wf(tmp_path, V_OX_BASE))
    assert data["pass"] is False
    assert any("unknown node 'loop'" in i["message"] for i in data["issues"]
               if i["severity"] == "block")


def test_on_exhaust_default_extend_blocks(tmp_path):
    body = _append_to_loop(V_OX_BASE, "  on_exhaust:\n    action: escalate\n    default: extend\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("default 'extend' is refused" in i["message"] for i in data["issues"])


def test_on_exhaust_undeclared_notes_input_blocks(tmp_path):
    body = _append_to_loop(V_OX_BASE, "  on_exhaust:\n    action: escalate\n    notes_input: nope\n    on_headless: fail\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("notes_input 'nope'" in i["message"] for i in data["issues"])


def test_on_exhaust_gate_fields_on_continue_block(tmp_path):
    body = _append_to_loop(V_OX_BASE, "  on_exhaust:\n    action: continue\n    options: [a]\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("only meaningful under action: escalate" in i["message"]
               for i in data["issues"])


def test_on_exhaust_escalate_reserved_ids_block(tmp_path):
    body = V_OX_ESCALATE.replace(
        "nodes:\n", "nodes:\n  - id: loop\n    agent: ag\n    prompt: shadow\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "nodes/loop" in locs(data)
    body2 = V_OX_ESCALATE.replace(
        "gates:\n", "gates:\n  - id: loop_exhaust\n    after: plan\n"
        "    type: human_approval\n    prompt: clash\n    options: [a, b]\n")
    rc2, data2, err2 = run(write_wf(tmp_path, body2))
    assert "gates/loop_exhaust" in locs(data2)


def test_on_exhaust_doc_gate_mode_auto_needs_headless_policy(tmp_path):
    # doc-declared gate_mode: auto + escalate without default/on_headless
    # blocks (same rails as authored gates); the authored approve gate gets a
    # default so the synthetic gate is the only offender
    body = V_OX_BASE.replace("version: 1\n", "version: 1\ngate_mode: auto\n") \
                    .replace("    options: [approve, abandon]\n",
                             "    options: [approve, abandon]\n    default: approve\n")
    body = _append_to_loop(body, "  on_exhaust:\n    action: escalate\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("loop_exhaust" in i["message"] for i in data["issues"]
               if i["location"] == "loop/on_exhaust")


def test_loop_exhaust_choice_literal_membership_checked(tmp_path):
    body = V_OX_ESCALATE.replace("== 'accept'", "== 'acept'")
    rc, data, err = run(write_wf(tmp_path, body))
    assert data["pass"] is False
    assert any("'acept'" in i["message"] and "loop_exhaust" in i["message"]
               for i in data["issues"] if i["severity"] == "block")


def test_on_exhaust_escalate_empty_body_blocks(tmp_path):
    body = _append_to_loop(
        V_OX_BASE.replace("  body: [impl, ev]\n", "  body: []\n"),
        "  on_exhaust:\n    action: escalate\n    on_headless: fail\n")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("escalate requires a non-empty loop.body" in i["message"]
               for i in data["issues"])


def test_on_exhaust_adjacent_background_node_blocks(tmp_path):
    # an agent: node between the gate and the body merges into the loop
    # segment (provably background, no checkpoint between) — the policy
    # cannot attach; validate blocks the static subset
    body = V_OX_ESCALATE.replace(
        "gates:\n",
        "  - id: prep\n    agent: ag\n    depends_on: [plan]\n"
        "    prompt: prep ${plan.output.spec}\ngates:\n").replace(
        "    depends_on: [plan]\n    prompt: impl",
        "    depends_on: [prep]\n    prompt: impl")
    rc, data, err = run(write_wf(tmp_path, body))
    assert "loop/on_exhaust" in locs(data)
    assert any("merges into the loop segment" in i["message"]
               for i in data["issues"])


def test_loop_pseudo_result_field_typo_blocks(tmp_path):
    body = V_OX_ESCALATE.replace(
        "when: ${loop.output.converged || loop_exhaust.output.choice == 'accept'}",
        "when: ${loop.output.choice == 'accept'}")
    rc, data, err = run(write_wf(tmp_path, body))
    assert data["pass"] is False
    assert any("declares only converged/rounds/carry" in i["message"]
               for i in data["issues"] if i["severity"] == "block")


def test_present_path_on_loop_pseudo_result_accepted_and_typed(tmp_path):
    # an authored post-loop gate may present the loop pseudo-result; a field
    # outside the fixed contract blocks
    base = V_OX_ESCALATE.replace(
        "gates:\n",
        "gates:\n  - id: ship\n    after: ev\n    type: human_approval\n"
        "    prompt: ship?\n    options: [confirm, stop]\n"
        "    present: [loop.output.rounds]\n")
    rc, data, err = run(write_wf(tmp_path, base))
    assert data["pass"] is True, data["issues"]
    bad = base.replace("present: [loop.output.rounds]",
                       "present: [loop.output.choice]")
    rc2, data2, err2 = run(write_wf(tmp_path, bad))
    assert any("records only {converged, rounds, carry}" in i["message"]
               for i in data2["issues"] if i["severity"] == "block")


# --- Task 1: optional human-readable name on nodes and gates ---

def test_node_name_accepted_by_schema(tmp_path):
    body = VALID.replace(
        "    agent: some-agent\n    prompt: do a\n",
        "    agent: some-agent\n    name: Do The Thing\n    prompt: do a\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0
    assert data["pass"] is True
    assert not any("name" in i["location"] for i in data["issues"] if i["severity"] == "block")


GATE_NAME_OK = VALID + """\
gates:
  - id: g1
    name: Plan approval
    after: b
    type: human_approval
    prompt: approve?
"""


def test_gate_name_accepted_by_schema(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, GATE_NAME_OK))
    assert rc == 0
    assert data["pass"] is True


def test_unknown_node_key_still_blocks(tmp_path):
    # additionalProperties:false must remain intact: a typo'd key is a hard block.
    body = VALID.replace(
        "    agent: some-agent\n    prompt: do a\n",
        "    agent: some-agent\n    nme: typo\n    prompt: do a\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert any(i["severity"] == "block" and i["location"].startswith("schema:nodes/")
               for i in data["issues"])


def test_grant_tools_warns_inert(tmp_path):
    wf = write_wf(tmp_path, """
version: 1
name: gt
description: d
nodes:
  - id: a
    agent: ag
    grant_tools: [Bash]
    prompt: do a
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "nodes/a"
               and "grant_tools" in i["message"] for i in data["issues"]), data


def test_materialize_name_not_path_warns(tmp_path):
    wf = write_wf(tmp_path, """
version: 1
name: mz
description: d
inputs:
  blob:
    type: string
    materialize: file
nodes:
  - id: a
    agent: ag
    prompt: do ${workflow.inputs.blob}
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "inputs"
               and "blob" in i["message"] and "_path" in i["message"]
               for i in data["issues"]), data


def test_materialize_name_ending_path_clean(tmp_path):
    wf = write_wf(tmp_path, """
version: 1
name: mz2
description: d
inputs:
  blob_path:
    type: string
    materialize: file
nodes:
  - id: a
    agent: ag
    prompt: do ${workflow.inputs.blob_path}
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("materialize" in i["message"] and "_path" in i["message"]
                   for i in data["issues"]), data


def test_warn_nonapproval_gate_dropped_warns(tmp_path):
    # severity:warn + type != human_approval, NOT branched on -> warn (dead checkpoint)
    wf = write_wf(tmp_path, """
version: 1
name: wg
description: d
nodes:
  - id: a
    agent: ag
    prompt: do a
gates:
  - id: g1
    after: a
    type: verification
    severity: warn
    prompt: check it
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "gates/g1"
               and "no checkpoint" in i["message"] for i in data["issues"]), data


LOOP_TYPED = """
version: 1
name: loopt
description: d
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: work
    agent: ag
    depends_on: [seed]
    output_schema:
      type: object
      properties:
        pass: {type: boolean}
      required: [pass]
    prompt: work
loop:
  body: [work]
  max_iters: 3
  while: {WHILE}
"""

def test_loop_while_bad_field_blocks(tmp_path):
    wf = write_wf(tmp_path, LOOP_TYPED.replace("{WHILE}", "${work.output.nope}"))
    rc, data, err = run(wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "loop"
               and "nope" in i["message"] and "work" in i["message"]
               for i in data["issues"]), data


def test_loop_while_good_field_clean(tmp_path):
    wf = write_wf(tmp_path, LOOP_TYPED.replace("{WHILE}", "${work.output.pass}"))
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any(i["severity"] == "block" and i["location"] == "loop"
                   for i in data["issues"]), data


def test_loop_carry_bad_field_blocks(tmp_path):
    body = LOOP_TYPED.replace("{WHILE}", "${work.output.pass}").rstrip() + \
        "\n  carry:\n    keep: ${work.output.ghost}\n"
    wf = write_wf(tmp_path, body)
    rc, data, err = run(wf)
    assert rc == 1, err
    assert any("ghost" in i["message"] for i in data["issues"]), data


def test_loop_while_non_converging_warns(tmp_path):
    # while references a NON-body node's output and no carry -> can't converge early
    body = """
version: 1
name: nc
description: d
nodes:
  - id: cfg
    agent: ag
    output_schema: {type: object, properties: {go: {type: boolean}}}
    prompt: cfg
  - id: work
    agent: ag
    depends_on: [cfg]
    prompt: work
loop:
  body: [work]
  max_iters: 3
  while: ${cfg.output.go}
"""
    wf = write_wf(tmp_path, body)
    rc, data, err = run(wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "loop"
               and "iteration-local" in i["message"] for i in data["issues"]), data


FE = """
version: 1
name: fe
description: d
nodes:
  - id: src
    agent: ag
    output_schema:
      type: object
      properties:
        items: {type: array}
        name: {type: string}
    prompt: src
  - id: fan
    agent: ag
    depends_on: [src]
    for_each: {SRC}
    as: it
    prompt: handle ${it}
"""

def test_for_each_non_array_field_warns(tmp_path):
    wf = write_wf(tmp_path, FE.replace("{SRC}", "${src.output.name}"))
    rc, data, err = run(wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "nodes/fan/for_each"
               and "name" in i["message"] and "array" in i["message"]
               for i in data["issues"]), data


def test_for_each_array_field_clean(tmp_path):
    wf = write_wf(tmp_path, FE.replace("{SRC}", "${src.output.items}"))
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("for_each" in i["location"] and "array" in i["message"]
                   for i in data["issues"]), data


def test_for_each_method_chain_no_warn(tmp_path):
    # a .filter(...) expression is not statically typeable -> NO warn (catalog pattern)
    wf = write_wf(tmp_path, FE.replace("{SRC}", "${src.output.items.filter(x => x)}"))
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any(i["location"] == "nodes/fan/for_each" and "array" in i["message"]
                   for i in data["issues"]), data


ENUMG = """
version: 1
name: eg
description: d
nodes:
  - id: judge
    agent: ag
    output_schema:
      type: object
      properties:
        verdict: {type: string, enum: [approve, reject]}
    prompt: judge
  - id: act
    agent: ag
    depends_on: [judge]
    when: {WHEN}
    prompt: act
"""

def test_dead_enum_literal_blocks(tmp_path):
    wf = write_wf(tmp_path, ENUMG.replace("{WHEN}", "${judge.output.verdict == 'reqect'}"))
    rc, data, err = run(wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/act"
               and "reqect" in i["message"] and "enum" in i["message"]
               for i in data["issues"]), data


def test_valid_enum_literal_clean(tmp_path):
    wf = write_wf(tmp_path, ENUMG.replace("{WHEN}", "${judge.output.verdict == 'approve'}"))
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("enum" in i["message"] for i in data["issues"]), data


def test_non_enum_field_literal_clean(tmp_path):
    # field has no enum -> any literal is fine
    body = ENUMG.replace(
        "verdict: {type: string, enum: [approve, reject]}",
        "verdict: {type: string}").replace("{WHEN}", "${judge.output.verdict == 'whatever'}")
    wf = write_wf(tmp_path, body)
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("enum" in i["message"] for i in data["issues"]), data


def test_bare_ref_unknown_blocks(tmp_path):
    wf = write_wf(tmp_path, """
version: 1
name: br
description: d
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: b
    agent: ag
    depends_on: [a]
    prompt: use ${mystery}
""")
    rc, data, err = run(wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/b"
               and "mystery" in i["message"] for i in data["issues"]), data


def test_bare_ref_own_as_var_clean(tmp_path):
    # LOUD #2 regression guard: ${finding} == this node's own `as: finding`
    wf = write_wf(tmp_path, """
version: 1
name: br2
description: d
nodes:
  - id: src
    agent: ag
    output_schema: {type: object, properties: {findings: {type: array}}}
    prompt: src
  - id: fan
    agent: ag
    depends_on: [src]
    for_each: ${src.output.findings}
    as: finding
    prompt: verify ${finding}
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("resolves to no" in i["message"] for i in data["issues"]), data


def test_bare_ref_carry_dotted_clean(tmp_path):
    # the CORRECT carry-ref form is dotted ${loop.carry.<v>} (DAG-RULES §loop) — it
    # has a dot, so the bare-ref check skips it and never false-blocks.
    wf = write_wf(tmp_path, """
version: 1
name: br3
description: d
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: work
    agent: ag
    depends_on: [seed]
    prompt: continue from ${loop.carry.prev}
loop:
  body: [work]
  max_iters: 3
  while: ${work.output.go}
  carry:
    prev: ${work.output}
""")
    rc, data, err = run(wf)
    assert rc == 0, err
    assert not any("resolves to no" in i["message"] for i in data["issues"]), data


def test_bare_ref_carry_bare_blocks(tmp_path):
    # a BARE ${prev} is undefined at runtime even when `prev` is a carry key — carry
    # resolves only via ${loop.carry.prev}, so the bare form must block.
    wf = write_wf(tmp_path, """
version: 1
name: br4
description: d
nodes:
  - id: seed
    agent: ag
    prompt: seed
  - id: work
    agent: ag
    depends_on: [seed]
    prompt: continue from ${prev}
loop:
  body: [work]
  max_iters: 3
  while: ${work.output.go}
  carry:
    prev: ${work.output}
""")
    rc, data, err = run(wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/work"
               and "prev" in i["message"] for i in data["issues"]), data


def test_use_node_missing_profile_blocks(tmp_path):
    _mk_ms(tmp_path, "real-ms")  # has profiles/base.yaml only
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: a
    use: real-ms
    customize:
      profile: ghost
    prompt: x
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/a"
               and "ghost" in i["message"] for i in data["issues"]), data


def test_use_node_missing_required_input_blocks(tmp_path):
    _mk_ms(tmp_path, "needy", base_yaml=(
        "version: 1\ninputs:\n  seed:\n    required: true\n"))
    # MICROSKILL.md must list `seed` in its Inputs table for required_inputs to include it
    md = ("---\nname: needy\ndescription: d\n---\n\n# needy\n\n## Purpose\n\n"
          "Given seed do work produce out.\n\n## Inputs\n\n"
          "| name | required | type | description | default |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| seed | yes | string | the seed | — |\n\n## Steps\n\n1. Use ${seed}.\n")
    (tmp_path / "microskills" / "needy" / "MICROSKILL.md").write_text(md)
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: a
    use: needy
    prompt: x
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/a"
               and "seed" in i["message"] and "required" in i["message"]
               for i in data["issues"]), data


def test_use_node_required_input_supplied_clean(tmp_path):
    _mk_ms(tmp_path, "needy", base_yaml=(
        "version: 1\ninputs:\n  seed:\n    required: true\n"))
    md = ("---\nname: needy\ndescription: d\n---\n\n# needy\n\n## Purpose\n\n"
          "Given seed do work produce out.\n\n## Inputs\n\n"
          "| name | required | type | description | default |\n"
          "| --- | --- | --- | --- | --- |\n"
          "| seed | yes | string | the seed | — |\n\n## Steps\n\n1. Use ${seed}.\n")
    (tmp_path / "microskills" / "needy" / "MICROSKILL.md").write_text(md)
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: src
    agent: ag
    prompt: src
  - id: a
    use: needy
    depends_on: [src]
    inputs:
      seed: ${src.output.x}
    prompt: x
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 0, err
    assert not any("required" in i["message"] and "seed" in i["message"]
                   for i in data["issues"]), data


def _mk_ms_orch(tmp_path, name):
    # a microskill whose runtime exposes AskUserQuestion -> classifies orchestrator
    base = ("version: 1\nruntime:\n  allowed_tools: [AskUserQuestion]\n")
    _mk_ms(tmp_path, name, base_yaml=base)


def test_loop_body_use_orchestrator_blocks(tmp_path):
    _mk_ms_orch(tmp_path, "asker")
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: ask
    use: asker
    prompt: x
loop:
  body: [ask]
  max_iters: 2
  while: ${ask.output.go}
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "loop/body"
               and "ask" in i["message"] and "orchestrator" in i["message"]
               for i in data["issues"]), data


def test_max_parallel_orchestrator_foreach_warns(tmp_path):
    _mk_ms_orch(tmp_path, "asker")
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: src
    agent: ag
    output_schema: {type: object, properties: {items: {type: array}}}
    prompt: src
  - id: fan
    use: asker
    depends_on: [src]
    for_each: ${src.output.items}
    as: it
    max_parallel: 4
    inputs:
      it: ${it}
    prompt: x
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and i["location"] == "nodes/fan/max_parallel"
               and "orchestrator" in i["message"] for i in data["issues"]), data


def _mk_ms_hardgate(tmp_path, name):
    # a microskill carrying a hard gate -> _use_resolves_orchestrator's gates branch
    # (classify case 5b) -> orchestrator. Covers the non-AskUserQuestion path.
    base = ("version: 1\ngates:\n  add:\n    - id: checkit\n      after: \"1\"\n"
            "      type: verification\n      severity: hard\n")
    _mk_ms(tmp_path, name, base_yaml=base)


def test_loop_body_hardgate_orchestrator_blocks(tmp_path):
    _mk_ms_hardgate(tmp_path, "gated")
    defs_root = tmp_path / "workflow-defs"
    wf = make_def(defs_root, "f", """
version: 1
name: f
description: d
nodes:
  - id: g
    use: gated
    prompt: x
loop:
  body: [g]
  max_iters: 2
  while: ${g.output.go}
""")
    rc, data, err = run_defs(defs_root, wf)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "loop/body"
               and "g" in i["message"] and "orchestrator" in i["message"]
               for i in data["issues"]), data


def _child_with_profile(defs_root, name="child-flow"):
    make_def(defs_root, name, """
version: 1
name: child-flow
imports: []
inputs:
  seed:
    type: string
    required: false
nodes:
  - id: work
    agent: ag
    prompt: work ${workflow.inputs.seed}
output:
  from: work
""")
    pdir = defs_root / name / "profiles"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "strict.yaml").write_text(
        "inputs:\n  seed:\n    required: true\n")


def test_child_required_input_via_profile_blocks(tmp_path):
    defs_root = tmp_path / "workflow-defs"
    _child_with_profile(defs_root)
    parent = make_def(defs_root, "parent", """
version: 1
name: parent
imports: [child-flow]
nodes:
  - id: a
    agent: ag
    prompt: a
  - id: build
    workflow: child-flow
    depends_on: [a]
    customize:
      profile: strict
""")
    rc, data, err = run_defs(defs_root, parent)
    assert rc == 1, err
    assert any(i["severity"] == "block" and i["location"] == "nodes/build"
               and "seed" in i["message"] for i in data["issues"]), data


def test_child_required_input_base_not_required_clean(tmp_path):
    # same child WITHOUT the strict profile -> seed is required:false -> no block
    defs_root = tmp_path / "workflow-defs"
    _child_with_profile(defs_root)
    parent = make_def(defs_root, "parent", """
version: 1
name: parent
imports: [child-flow]
nodes:
  - id: a
    agent: ag
    prompt: a
  - id: build
    workflow: child-flow
    depends_on: [a]
""")
    rc, data, err = run_defs(defs_root, parent)
    assert rc == 0, err
    assert not any("seed" in i["message"] and "requires input" in i["message"]
                   for i in data["issues"]), data


def test_child_output_field_via_resolved_schema_warns(tmp_path):
    # child output.from is a use: node with NO inline schema; its resolved schema
    # declares {result}; a parent ref to ${build.output.ghost} should warn.
    defs_root = tmp_path / "workflow-defs"
    _mk_ms(tmp_path, "producer", base_yaml=(
        "version: 1\noutput_schema:\n  type: object\n  properties:\n    result: {type: string}\n"))
    make_def(defs_root, "child-flow", """
version: 1
name: child-flow
imports: []
nodes:
  - id: make
    use: producer
    prompt: make
output:
  from: make
""")
    parent = make_def(defs_root, "parent", """
version: 1
name: parent
imports: [child-flow]
nodes:
  - id: build
    workflow: child-flow
    inputs: {}
  - id: read
    agent: ag
    depends_on: [build]
    prompt: got ${build.output.ghost}
""")
    rc, data, err = run_defs(defs_root, parent)
    assert rc == 0, err
    assert any(i["severity"] == "warn" and "ghost" in i["message"]
               and "child workflow" in i["message"] for i in data["issues"]), data


# --- subgraph: parity — validate desugars via the SAME shared desugar_subgraphs
# as compile (imported by validate via SourceFileLoader), so the SAME fixtures
# accept/reject identically. The fixtures are imported from the compile test
# module so there is ONE definition (no parity drift between the two suites).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compile_workflow import SG_HOST, SG_REVIEW, SG_LOOP_HOST  # noqa: E402


def _setup_sg(tmp_path, host=SG_HOST, sg=SG_REVIEW, sg_name="adversarial-review"):
    (tmp_path / "host-flow").mkdir()
    (tmp_path / "host-flow" / "WORKFLOW.yaml").write_text(host)
    d = tmp_path / "_subgraphs" / sg_name
    d.mkdir(parents=True)
    (d / "SUBGRAPH.yaml").write_text(sg)
    return tmp_path / "host-flow" / "WORKFLOW.yaml"


def _sg_blocks(data):
    return [i["message"] for i in (data or {}).get("issues", [])
            if i["severity"] == "block" and i["location"] == "subgraph"]


def test_subgraph_valid_passes(tmp_path):
    wf = _setup_sg(tmp_path)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 0, data
    assert data["pass"] is True
    assert not any(i["severity"] == "block" for i in data["issues"]), data


def test_subgraph_unresolved_name_blocks(tmp_path):
    # No _subgraphs dir → the named subgraph does not resolve.
    (tmp_path / "host-flow").mkdir()
    wf = tmp_path / "host-flow" / "WORKFLOW.yaml"
    wf.write_text(SG_HOST)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("not found" in m for m in _sg_blocks(data)), data


def test_subgraph_bounded_convergence_blocks(tmp_path):
    sg = SG_REVIEW.replace("convergence: { mode: single_pass }",
                           "convergence: { mode: bounded, max_iters: 3 }")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("bounded" in m and "PR3" in m for m in _sg_blocks(data)), data


def test_subgraph_missing_required_param_blocks(tmp_path):
    host = SG_HOST.replace(
        "    with:\n      artifact_kind: high-level design document\n", "")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("required param" in m and "artifact_kind" in m
               for m in _sg_blocks(data)), data


def test_subgraph_unknown_with_key_blocks(tmp_path):
    host = SG_HOST.replace(
        "      artifact_kind: high-level design document\n",
        "      artifact_kind: high-level design document\n      bogus: x\n")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("unknown with key" in m and "bogus" in m for m in _sg_blocks(data)), data


def test_subgraph_missing_required_input_blocks(tmp_path):
    host = SG_HOST.replace(
        "    inputs:\n      artifact_path: ${author.output.document_path}\n", "")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("required input" in m and "artifact_path" in m
               for m in _sg_blocks(data)), data


def test_subgraph_unknown_inputs_key_blocks(tmp_path):
    host = SG_HOST.replace(
        "      artifact_path: ${author.output.document_path}\n",
        "      artifact_path: ${author.output.document_path}\n"
        "      bogus: ${author.output.x}\n")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("unknown inputs key" in m and "bogus" in m for m in _sg_blocks(data)), data


def test_subgraph_inner_orchestrator_blocks(tmp_path):
    sg = SG_REVIEW.replace("  - id: synthesize\n    agent: synthesizer\n",
                           "  - id: synthesize\n    delegation: orchestrator\n")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("orchestrator" in m for m in _sg_blocks(data)), data


def test_subgraph_nested_subgraph_blocks(tmp_path):
    sg = SG_REVIEW.replace(
        "  - id: synthesize\n    agent: synthesizer\n    prompt: synthesize the review\n",
        "  - id: synthesize\n    subgraph: other\n")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("nested" in m for m in _sg_blocks(data)), data


def test_subgraph_author_id_with_double_underscore_blocks(tmp_path):
    host = SG_HOST.replace("author", "au__thor")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("__" in m and "reserved" in m for m in _sg_blocks(data)), data


def test_subgraph_non_self_contained_ref_blocks(tmp_path):
    sg = SG_REVIEW.replace("      artifact_path: ${inputs.artifact_path}\n",
                           "      artifact_path: ${workflow.inputs.secret}\n")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("self-contained" in m and "workflow.inputs.secret" in m
               for m in _sg_blocks(data)), data


def test_subgraph_no_nodes_blocks(tmp_path):
    sg = "version: 1\noutput: synthesize\n"
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("schema" in m and "nodes" in m for m in _sg_blocks(data)), data


def test_subgraph_output_names_no_inner_node_blocks(tmp_path):
    sg = SG_REVIEW.replace("output: synthesize", "output: ghost")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("output 'ghost' names no inner node" in m for m in _sg_blocks(data)), data


def test_subgraph_surviving_undeclared_token_blocks(tmp_path):
    sg = SG_REVIEW.replace("prompt: review this {{artifact_kind}}",
                           "prompt: review this {{artifact_kind}} with {{undeclared_tok}}")
    wf = _setup_sg(tmp_path, sg=sg)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("undeclared_tok" in m and "unresolved" in m for m in _sg_blocks(data)), data


# PR1-remediation parity (same shared desugar -> same blocks in validate):

def test_subgraph_in_top_level_loop_blocks(tmp_path):
    wf = _setup_sg(tmp_path, host=SG_LOOP_HOST)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("top-level loop" in m for m in _sg_blocks(data)), data


def test_subgraph_non_string_input_binding_blocks(tmp_path):
    host = SG_HOST.replace("      artifact_path: ${author.output.document_path}\n",
                           "      artifact_path: 42\n")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("binding must" in m and "string" in m for m in _sg_blocks(data)), data


def test_subgraph_convergence_scalar_shorthand_blocks(tmp_path):
    host = SG_HOST.replace(
        "    with:\n      artifact_kind: high-level design document\n",
        "    with:\n      artifact_kind: high-level design document\n"
        "      convergence: bounded\n")
    wf = _setup_sg(tmp_path, host=host)
    rc, data, _ = run(wf, "--defs-root", tmp_path)
    assert rc == 1, data
    assert any("convergence must be a mapping" in m for m in _sg_blocks(data)), data
