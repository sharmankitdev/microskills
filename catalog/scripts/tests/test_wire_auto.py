"""
Tests for `wire: auto` — opt-in declared pass-through forwarding.

Feature contract (report item 2.3):
- At pre-emit time, any TARGET-declared input the node leaves unsupplied whose
  name exactly matches a declared workflow input materializes as
  `inputs.<name>: ${workflow.inputs.<name>}` — emitted JS byte-identical to
  the hand-written forward (THE correctness proof, pinned below for the
  hermetic world AND the adopted real catalog defs).
- Explicit inputs always win; wired pairs append after explicit pairs in
  sorted name order.
- `--explain` lists every auto-wired pair.
- Guardrails (verifier): shared helper across compile and validate; BLOCK
  (not fail-safe) when a wire: auto node's target won't resolve; WARN when
  auto-wiring satisfies a REQUIRED target input.

Hermetic fixtures use --defs-root under tmp_path (skill root = the sibling
microskills/, exactly like compile derives it); the adoption-pin tests copy
the REAL catalog into tmp_path (intentional real-catalog exception) and
compare wire-sugar output against a hand-desugared rewrite of the same def.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
COMPILE = REPO / "catalog" / "scripts" / "compile-workflow"
VALIDATE = REPO / "catalog" / "scripts" / "validate-workflow"
REAL_CATALOG_DEFS = REPO / "catalog" / "workflow-defs"
REAL_CATALOG_MS = REPO / "catalog" / "microskills"
# Pin the in-repo source schema so tests exercise the committed templates/.
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}


def compile_wf(defs_root, name, *args):
    proc = subprocess.run(
        [sys.executable, str(COMPILE), name, "--defs-root", str(defs_root), *args],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def validate_wf(wf_path, *args):
    proc = subprocess.run(
        [sys.executable, str(VALIDATE), str(wf_path), *args],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


# --- hermetic world ---------------------------------------------------------
# A microskill declaring one REQUIRED and two optional inputs, so the wire set
# and the required-input WARN are both exercised.

WIRE_MS_MD = """\
---
name: wire-ms
description: minimal microskill for wire auto tests
---

# Wire MS

## Purpose

Echo back.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| alpha | yes | string | required input | — |
| beta | no | string | optional input | — |
| gamma | no | string | optional input | — |

## Steps

1. Return the result.
"""

WIRE_MS_BASE = """\
version: 1
inputs:
  alpha:
    required: true
  beta:
    required: false
  gamma:
    required: false
output_schema:
  type: object
  required: [echoed]
  properties:
    echoed: { type: string }
"""


def make_world(tmp_path, wf_yaml, name="wire-flow"):
    """<tmp>/workflow-defs/<name> + <tmp>/microskills/wire-ms. Returns
    (defs_root, def_dir)."""
    defs_root = tmp_path / "workflow-defs"
    ms_dir = tmp_path / "microskills" / "wire-ms" / "profiles"
    ms_dir.mkdir(parents=True)
    (tmp_path / "microskills" / "wire-ms" / "MICROSKILL.md").write_text(WIRE_MS_MD)
    (ms_dir / "base.yaml").write_text(WIRE_MS_BASE)
    d = defs_root / name
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(wf_yaml)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    return defs_root, d


def recompile_with(defs_root, def_dir, name, wf_yaml, *args):
    """Rewrite the SAME def in place and recompile — same paths, same name, so
    compiled bytes are directly comparable across variants."""
    (def_dir / "WORKFLOW.yaml").write_text(wf_yaml)
    return compile_wf(defs_root, name, *args)


WF_HEAD = """\
version: 1
name: wire-flow
inputs:
  alpha:
    type: string
    required: true
  beta:
    type: string
    required: false
    default: b
  gamma:
    type: string
    required: false
    default: g
  unrelated:
    type: string
    required: false
"""

HAND_WRITTEN = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    inputs:
      alpha: ${workflow.inputs.alpha}
      beta: ${workflow.inputs.beta}
      gamma: ${workflow.inputs.gamma}
"""

WIRE_SUGAR = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    wire: auto
    inputs:
      alpha: ${workflow.inputs.alpha}
"""


def test_wire_auto_emits_handwritten_bytes(tmp_path):
    # THE correctness proof: the sugar's emitted JS + manifest are byte-identical
    # to the hand-written pass-through forwards (alpha explicit; beta/gamma wired,
    # appended in sorted order — exactly the hand-written suffix).
    defs_root, d = make_world(tmp_path, HAND_WRITTEN)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 0, err
    hand = {p.name: p.read_text() for p in (d / ".compiled").rglob("*") if p.is_file()}

    rc, data, out, err = recompile_with(defs_root, d, "wire-flow", WIRE_SUGAR)
    assert rc == 0, err
    sugar = {p.name: p.read_text() for p in (d / ".compiled").rglob("*") if p.is_file()}

    assert sugar == hand  # seg JS, manifest.json AND frozen resolutions identical


def test_wire_auto_explicit_wins(tmp_path):
    # An explicitly supplied key is never touched — even when its value is NOT
    # the same-named pass-through.
    wf = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    wire: auto
    inputs:
      alpha: ${workflow.inputs.alpha}
      beta: ${workflow.inputs.unrelated}
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow", "--explain")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert '"beta": _args.wf_unrelated' in seg      # explicit value kept
    assert '"gamma": _args.wf_gamma' in seg         # only gamma auto-wired
    assert data["auto_wired"] == {"n": ["gamma"]}


def test_wire_auto_appends_sorted_after_explicit(tmp_path):
    # Wired pairs append AFTER the authored entries, sorted by name — the
    # authored entries keep their order.
    wf = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    wire: auto
    inputs:
      alpha: ${workflow.inputs.alpha}
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 0, err
    seg = (d / ".compiled" / "seg-1.js").read_text()
    assert ('inputs: { "alpha": _args.wf_alpha, '
            '"beta": _args.wf_beta, "gamma": _args.wf_gamma }') in seg


def test_wire_auto_ignores_undeclared_workflow_inputs(tmp_path):
    # A target input with NO same-named declared workflow input is never wired;
    # a workflow input the target does not declare is never wired either.
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha:
    type: string
    required: true
  unrelated:
    type: string
    required: false
nodes:
  - id: n
    use: wire-ms
    wire: auto
    inputs: {}
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow", "--explain")
    assert rc == 0, err
    # beta/gamma undeclared on the workflow -> not wired; unrelated undeclared
    # on the target -> not wired; only alpha matches.
    assert data["auto_wired"] == {"n": ["alpha"]}
    manifest = json.loads((d / ".compiled" / "manifest.json").read_text())
    seg_step = manifest["steps"][0]
    assert seg_step["needs"]["wf_inputs"] == ["alpha"]


def test_wire_auto_explain_lists_pairs_and_plain_compile_does_not(tmp_path):
    defs_root, d = make_world(tmp_path, WIRE_SUGAR)
    rc, data, out, err = compile_wf(defs_root, "wire-flow", "--explain")
    assert rc == 0, err
    assert data["auto_wired"] == {"n": ["beta", "gamma"]}
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 0, err
    assert "auto_wired" not in data


def test_wire_auto_required_satisfaction_warns_on_stderr(tmp_path):
    # alpha is required:true in the target — auto-satisfying it is the 'name'
    # collision risk: warn (never block), and still wire it.
    wf = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    wire: auto
    inputs:
      beta: ${workflow.inputs.beta}
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow", "--explain")
    assert rc == 0, err
    assert data["auto_wired"] == {"n": ["alpha", "gamma"]}
    assert "REQUIRED target input 'alpha'" in err
    assert "gamma" not in err  # optional wire stays quiet


def test_wire_auto_on_agent_node_dies(tmp_path):
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha: { type: string }
nodes:
  - id: n
    agent: some-agent
    wire: auto
    prompt: do it
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 1
    assert "wire: auto is only valid" in data["error"]


def test_wire_auto_on_orchestrator_native_dies(tmp_path):
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha: { type: string }
nodes:
  - id: n
    delegation: orchestrator
    wire: auto
    prompt: do it
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 1
    assert "wire: auto is only valid" in data["error"]


def test_wire_auto_under_delegation_orchestrator_dies(tmp_path):
    # The use: escape hatch SKIPS resolution, so the declared inputs are
    # unknowable — contradiction, not silence.
    wf = WF_HEAD + """\
nodes:
  - id: n
    use: wire-ms
    wire: auto
    delegation: orchestrator
    prompt: do it
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 1
    assert "delegation: orchestrator" in data["error"]


def test_wire_auto_unresolvable_use_target_dies(tmp_path):
    # BLOCK, not fail-safe: classify's hard die fires before any wiring.
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha: { type: string }
nodes:
  - id: n
    use: no-such-ms
    wire: auto
"""
    defs_root, d = make_world(tmp_path, wf)
    rc, data, out, err = compile_wf(defs_root, "wire-flow")
    assert rc == 1
    assert "failed to resolve" in data["error"]


# --- workflow: node targets -------------------------------------------------

CHILD_WF = """\
version: 1
name: wire-child
inputs:
  harness_root:
    type: string
    required: false
    default: harness
  payload:
    type: string
    required: true
nodes:
  - id: work
    agent: ag
    prompt: do ${workflow.inputs.payload} under ${workflow.inputs.harness_root}
"""

PARENT_WF = """\
version: 1
name: wire-parent
inputs:
  harness_root:
    type: string
    required: false
    default: harness
imports:
  - wire-child
nodes:
  - id: a
    agent: ag
    prompt: produce
    output_schema:
      type: object
      required: [x]
      properties:
        x: { type: string }
  - id: call
    workflow: wire-child
    wire: auto
    inputs:
      payload: ${a.output.x}
"""


def make_parent_world(tmp_path, parent_yaml=PARENT_WF, with_child=True):
    defs_root = tmp_path / "workflow-defs"
    (tmp_path / "microskills").mkdir(parents=True)
    d = defs_root / "wire-parent"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(parent_yaml)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    if with_child:
        c = defs_root / "wire-child"
        (c / "profiles").mkdir(parents=True)
        (c / "WORKFLOW.yaml").write_text(CHILD_WF)
        (c / "profiles" / "base.yaml").write_text("version: 1\n")
    return defs_root, d


def test_wire_auto_workflow_node_wires_child_optional_input(tmp_path):
    # The nested checkpoint's manifest inputs carry the wired forward (the
    # dispatcher threads it into the child); payload stays the explicit ref.
    defs_root, d = make_parent_world(tmp_path)
    rc, data, out, err = compile_wf(defs_root, "wire-parent", "--explain")
    assert rc == 0, err
    assert data["auto_wired"] == {"call": ["harness_root"]}
    manifest = json.loads((d / ".compiled" / "manifest.json").read_text())
    chk = next(s for s in manifest["steps"]
               if s.get("checkpoint_type") == "nested_workflow")
    assert chk["inputs"] == {"payload": "${a.output.x}",
                             "harness_root": "${workflow.inputs.harness_root}"}
    assert err == ""  # harness_root is optional in the child -> no warn


def test_wire_auto_workflow_node_missing_child_dies(tmp_path):
    defs_root, d = make_parent_world(tmp_path, with_child=False)
    rc, data, out, err = compile_wf(defs_root, "wire-parent")
    assert rc == 1
    assert "wire: auto needs the child workflow's declared inputs" in data["error"]


# --- validate-workflow parity -----------------------------------------------

def test_validate_wire_auto_counts_wired_child_required_input_as_supplied(tmp_path):
    # The wired forward satisfies the child's required input in the
    # nested-workflow contract check — exactly like a hand-written forward —
    # and the required satisfaction surfaces as the collision-risk WARN.
    parent = """\
version: 1
name: wire-parent
inputs:
  payload:
    type: string
    required: true
  harness_root:
    type: string
    required: false
imports:
  - wire-child
nodes:
  - id: call
    workflow: wire-child
    wire: auto
"""
    defs_root, d = make_parent_world(tmp_path, parent_yaml=parent)
    rc, data, stderr = validate_wf(
        d / "WORKFLOW.yaml", "--defs-root", str(defs_root))
    assert data["pass"] is True, data
    warns = [i for i in data["issues"] if i["severity"] == "warn"]
    assert any("REQUIRED target input 'payload'" in i["message"] for i in warns)
    # No 'requires input … but the node's inputs omit it' block: the wire counted.
    assert not any("omit" in i["message"] for i in data["issues"]
                   if i["severity"] == "block")


def test_validate_wire_auto_placement_blocks(tmp_path):
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha: { type: string }
nodes:
  - id: n
    agent: some-agent
    wire: auto
    prompt: do it
"""
    p = tmp_path / "WORKFLOW.yaml"
    p.write_text(wf)
    rc, data, stderr = validate_wf(p)
    assert data["pass"] is False
    assert any("wire: auto is only valid" in i["message"]
               for i in data["issues"] if i["severity"] == "block")


def test_validate_wire_auto_unresolvable_use_blocks_even_standalone(tmp_path):
    # Standalone validation normally WARNS on an unresolvable use: target; a
    # wire: auto node escalates it to a BLOCK (the wire set is uncomputable).
    wf = """\
version: 1
name: wire-flow
inputs:
  alpha: { type: string }
nodes:
  - id: n
    use: no-such-ms
    wire: auto
  - id: m
    use: also-missing
"""
    d = tmp_path / "defs" / "wire-flow"
    d.mkdir(parents=True)
    p = d / "WORKFLOW.yaml"
    p.write_text(wf)
    rc, data, stderr = validate_wf(p)
    assert data["pass"] is False
    by_node = {i["location"]: i["severity"] for i in data["issues"]
               if "does not resolve" in i["message"]}
    assert by_node["nodes/n"] == "block"   # wire: auto -> escalated
    assert by_node["nodes/m"] == "warn"    # plain node keeps the hermetic warn


def test_validate_wire_auto_missing_child_blocks(tmp_path):
    defs_root, d = make_parent_world(tmp_path, with_child=False)
    rc, data, stderr = validate_wf(
        d / "WORKFLOW.yaml", "--defs-root", str(defs_root))
    assert data["pass"] is False
    assert any("wire: auto needs the child workflow's declared inputs"
               in i["message"] for i in data["issues"]
               if i["severity"] == "block")


def test_validate_wire_auto_materializes_like_compile(tmp_path):
    # Parity: the same world passes validate clean (no undeclared-ref noise from
    # the machine-generated forwards) and compiles to the wired set.
    defs_root, d = make_world(tmp_path, WIRE_SUGAR)
    rc, data, stderr = validate_wf(
        d / "WORKFLOW.yaml", "--defs-root", str(defs_root))
    assert data["pass"] is True, data
    assert data["issues"] == []


# --- adoption pin: the real catalog defs ------------------------------------
# THE feature's correctness proof on the shipped defs: each adopted def's
# wire: auto output must be byte-identical (segments AND manifest, incl.
# manifest_hash) to the hand-desugared rewrite of the same def. Intentionally
# points at the real catalog/.

# 2026-06-21 production rewire: NO shipped def currently adopts `wire: auto`.
# workflow-create's `build` (workflow: implement-rvs) now binds its inputs
# EXPLICITLY and is compile-time INLINED as a guarded loop region — keeping
# wire: auto would SUPPRESS that inlining (the node would stay a nested_workflow
# checkpoint). decompose-monolith-orchestrator (the other former adopter) was
# retired. The hermetic test_wire_auto_emits_handwritten_bytes remains the
# byte-identity correctness proof for the feature; the real-catalog adoption pin
# below is skipped until a shipped def adopts wire: auto again.
ADOPTED = {}


def copy_catalog_world(tmp_path):
    defs_root = tmp_path / "workflow-defs"
    shutil.copytree(REAL_CATALOG_DEFS, defs_root,
                    ignore=shutil.ignore_patterns(".compiled"))
    shutil.copytree(REAL_CATALOG_MS, tmp_path / "microskills")
    return defs_root


@pytest.mark.parametrize("name", [
    pytest.param("workflow-create", marks=pytest.mark.skip(
        reason="post-2026-06-21 rewire: no shipped def adopts wire: auto "
               "(workflow-create's build is inlined with explicit inputs); "
               "hermetic test_wire_auto_emits_handwritten_bytes covers byte-identity")),
])
def test_real_adopted_def_wire_auto_equals_handwritten(tmp_path, name):
    defs_root = copy_catalog_world(tmp_path)
    d = defs_root / name

    rc, data, out, err = compile_wf(defs_root, name, "--explain")
    assert rc == 0, err
    assert data["auto_wired"] == ADOPTED[name]
    assert "REQUIRED target input" not in err  # adoption wires optional inputs only
    sugar_segs = {p.name: p.read_text() for p in (d / ".compiled").glob("seg-*.js")}
    sugar_manifest = (d / ".compiled" / "manifest.json").read_text()
    assert sugar_segs, "expected at least one compiled segment"

    # Hand-desugar: drop wire: auto, restate the wired forwards explicitly (at
    # the end, in sorted order — the documented materialization rule).
    doc = yaml.safe_load((d / "WORKFLOW.yaml").read_text())
    desugared = set()
    for node in doc["nodes"]:
        if node.pop("wire", None) != "auto":
            continue
        wired = ADOPTED[name].get(node["id"])
        assert wired, f"unexpected wire: auto on node '{node['id']}'"
        for w in wired:
            assert w not in (node.get("inputs") or {})
            node.setdefault("inputs", {})[w] = "${workflow.inputs." + w + "}"
        desugared.add(node["id"])
    assert desugared == set(ADOPTED[name])
    (d / "WORKFLOW.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))

    rc, data, out, err = compile_wf(defs_root, name)
    assert rc == 0, err
    hand_segs = {p.name: p.read_text() for p in (d / ".compiled").glob("seg-*.js")}
    hand_manifest = (d / ".compiled" / "manifest.json").read_text()

    assert sugar_segs == hand_segs          # segments byte-identical
    assert sugar_manifest == hand_manifest  # manifest (incl. manifest_hash) too
