"""Hermetic tests for sub-PR 3 — region guards + child-gate-to-checkpoint.

Build throwaway parent + child WORKFLOW.yaml worlds under tmp_path, compile with
--defs-root pointed at that world, and assert on emitted JS + manifest. No test
touches the real catalog. Segment files are named seg-<N>.js (1-indexed).
"""
import json
import subprocess
import sys
from pathlib import Path

COMPILE = Path(__file__).resolve().parents[1] / "compile-workflow"


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _compile(defs_root: Path, name: str, *extra):
    return subprocess.run(
        [sys.executable, str(COMPILE), name, "--defs-root", str(defs_root), *extra],
        capture_output=True, text=True,
    )


def _manifest(defs_root: Path, name: str) -> dict:
    return json.loads((defs_root / name / ".compiled" / "manifest.json").read_text())


def _all_segment_js(defs_root: Path, name: str) -> str:
    return "\n".join(
        p.read_text()
        for p in sorted((defs_root / name / ".compiled").glob("seg-*.js")))


def _node_check_all(defs_root: Path, name: str):
    for seg in (defs_root / name / ".compiled").glob("seg-*.js"):
        chk = subprocess.run(["node", "--check", str(seg)],
                             capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg}: {chk.stderr}"


# --- Task 3.1: an inlined child human gate falls out as a parent checkpoint ---

def test_child_human_gate_becomes_parent_checkpoint(tmp_path):
    _write(tmp_path / "gk" / "WORKFLOW.yaml", """
version: 1
name: gk
inputs: { thing: { required: true } }
nodes:
  - id: draft
    agent: scratch
    prompt: "draft ${workflow.inputs.thing}"
  - id: after_gate
    agent: scratch
    prompt: "post ${draft.output}"
gates:
  - id: approve_draft
    type: human_approval
    after: draft
    prompt: "approve the draft?"
output: { from: after_gate }
""")
    _write(tmp_path / "gh" / "WORKFLOW.yaml", """
version: 1
name: gh
imports: [gk]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: gk
    inputs:
      thing: ${seed.output}
output: { from: nest }
""")
    r = _compile(tmp_path, "gh")
    assert r.returncode == 0, r.stderr
    manifest = _manifest(tmp_path, "gh")
    gate_ids = [s.get("gate", {}).get("id")
                for s in manifest["steps"] if s.get("kind") == "checkpoint"]
    assert "nest__approve_draft" in gate_ids, gate_ids
    # No surviving STATIC nested_workflow checkpoint (the child was inlined).
    assert not any(s.get("checkpoint_type") == "nested_workflow"
                   for s in manifest["steps"])


# --- Task 3.2: a guarded static workflow node → region-level if-wrap ---

def test_guarded_inlined_region_if_wraps_unguarded_body(tmp_path):
    # Child is a loop whose body holds a for_each fan-out (a parallel map that is
    # only legal in an UNGUARDED body — a per-node guard would re-block it).
    _write(tmp_path / "rk" / "WORKFLOW.yaml", """
version: 1
name: rk
inputs: { thing: { required: true } }
nodes:
  - id: make
    agent: scratch
    prompt: "make ${workflow.inputs.thing}"
  - id: checks
    agent: scratch
    for_each: ${make.output.items}
    as: item
    prompt: "check ${item}"
loop:
  body: [make, checks]
  until: ${make.output.done == true}
  max_iters: 2
output: { from: make }
""")
    _write(tmp_path / "rh" / "WORKFLOW.yaml", """
version: 1
name: rh
imports: [rk]
inputs: { req: { required: true } }
nodes:
  - id: plan
    agent: scratch
    prompt: "plan ${workflow.inputs.req}"
  - id: impl
    workflow: rk
    when: ${plan.output.scope_advisory == null}
    inputs:
      thing: ${plan.output}
  - id: done
    agent: scratch
    when: ${plan.output.scope_advisory == null}
    prompt: "done ${impl.output}"
output: { from: done }
""")
    r = _compile(tmp_path, "rh")
    assert r.returncode == 0, r.stderr
    blob = _all_segment_js(tmp_path, "rh")
    # The guard wraps the do/while REGION (one if-block), not each body node.
    assert "scope_advisory == null) {" in blob, blob
    # The body must contain a do/while (the loop survived the inline).
    assert "do {" in blob
    # The for_each fan-out survives inside the UNGUARDED loop body.
    assert "parallel(" in blob
    # No surviving static nested_workflow checkpoint.
    manifest = _manifest(tmp_path, "rh")
    assert not any(s.get("checkpoint_type") == "nested_workflow"
                   for s in manifest["steps"])
    _node_check_all(tmp_path, "rh")


def test_guarded_region_with_on_exhaust_ands_guard_into_gate(tmp_path):
    # A guarded loop-bearing child carrying on_exhaust: escalate gets a synthetic
    # exhaust gate whose `when` must AND the region guard (else a skipped region
    # would dereference a loop pseudo-result no step produced).
    _write(tmp_path / "ek" / "WORKFLOW.yaml", """
version: 1
name: ek
inputs: { thing: { required: true } }
nodes:
  - id: body
    agent: scratch
    prompt: "body ${workflow.inputs.thing}"
loop:
  body: [body]
  until: ${body.output.done == true}
  max_iters: 2
  on_exhaust: { action: escalate, on_headless: fail }
output: { from: body }
""")
    _write(tmp_path / "eh" / "WORKFLOW.yaml", """
version: 1
name: eh
imports: [ek]
inputs: { req: { required: true } }
nodes:
  - id: plan
    agent: scratch
    prompt: "plan ${workflow.inputs.req}"
  - id: impl
    workflow: ek
    when: ${plan.output.scope_advisory == null}
    inputs:
      thing: ${plan.output}
output: { from: impl }
""")
    r = _compile(tmp_path, "eh")
    assert r.returncode == 0, r.stderr
    manifest = _manifest(tmp_path, "eh")
    exhaust = next(
        (s for s in manifest["steps"]
         if s.get("kind") == "checkpoint"
         and s.get("gate", {}).get("id") == "loop_exhaust_impl"),
        None)
    assert exhaust is not None, [s.get("gate", {}).get("id")
                                 for s in manifest["steps"]
                                 if s.get("kind") == "checkpoint"]
    when = exhaust.get("when") or ""
    assert "scope_advisory == null" in when, when
    assert "&&" in when, when
    assert "converged" in when, when
    _node_check_all(tmp_path, "eh")


def test_guarded_loop_region_with_internal_checkpoint_falls_back(tmp_path):
    # A guarded loop-bearing child that ALSO carries an internal (authored) human
    # gate cannot be if-wrapped (the guard wraps only the loop segment, not a
    # separate checkpoint). It FALLS BACK to a nested_workflow checkpoint — the
    # pre-sub-PR-3 behavior, where the dispatcher evaluates the guard at runtime —
    # rather than hard-blocking (workflow-create's build node relies on this). The
    # non-inline is warned, never silent.
    _write(tmp_path / "bk" / "WORKFLOW.yaml", """
version: 1
name: bk
inputs: { thing: { required: true } }
nodes:
  - id: lbody
    agent: scratch
    prompt: "loop ${workflow.inputs.thing}"
gates:
  - id: mid_gate
    type: human_approval
    after: lbody
    prompt: "approve mid?"
loop:
  body: [lbody]
  until: ${lbody.output.done == true}
  max_iters: 2
output: { from: lbody }
""")
    _write(tmp_path / "bh" / "WORKFLOW.yaml", """
version: 1
name: bh
imports: [bk]
inputs: { req: { required: true } }
nodes:
  - id: plan
    agent: scratch
    prompt: "plan ${workflow.inputs.req}"
  - id: impl
    workflow: bk
    when: ${plan.output.ok == true}
    inputs:
      thing: ${plan.output}
output: { from: impl }
""")
    r = _compile(tmp_path, "bh")
    assert r.returncode == 0, r.stderr
    manifest = _manifest(tmp_path, "bh")
    assert any(s.get("checkpoint_type") == "nested_workflow"
               for s in manifest["steps"]), manifest["steps"]
    # Non-inline is warned (never silent).
    out = (r.stdout + r.stderr).lower()
    assert "guarded" in out and "checkpoint" in out and "not inlined" in out, out


def test_guarded_loopless_child_stays_checkpoint(tmp_path):
    # Non-breaking floor: a guarded LOOP-LESS static workflow node is NOT inlined
    # (no region to if-wrap) — it rides on as a nested_workflow checkpoint exactly
    # as before sub-PR 3.
    _write(tmp_path / "lk" / "WORKFLOW.yaml", """
version: 1
name: lk
inputs: { thing: { required: true } }
nodes:
  - id: only
    agent: scratch
    prompt: "only ${workflow.inputs.thing}"
output: { from: only }
""")
    _write(tmp_path / "lh" / "WORKFLOW.yaml", """
version: 1
name: lh
imports: [lk]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: lk
    when: ${seed.output.go == true}
    inputs:
      thing: ${seed.output}
output: { from: nest }
""")
    r = _compile(tmp_path, "lh")
    assert r.returncode == 0, r.stderr
    manifest = _manifest(tmp_path, "lh")
    assert any(s.get("checkpoint_type") == "nested_workflow"
               for s in manifest["steps"]), manifest["steps"]


# --- sub-PR 3 remediation (adversarial-review confirmed findings) ---

def test_guarded_child_nonloop_node_is_guarded(tmp_path):
    # FINDING 1 (blocker): a guarded loop-bearing child's NON-loop nodes must inherit
    # the guard (a per-node ternary) — else they run unconditionally when the guard is
    # false. Only the LOOP body stays unguarded (the region if-wrap is its gate).
    _write(tmp_path / "pk" / "WORKFLOW.yaml", """
version: 1
name: pk
inputs: { thing: { required: true } }
nodes:
  - id: prep
    agent: scratch
    prompt: "prep ${workflow.inputs.thing}"
  - id: loopbody
    agent: scratch
    prompt: "body ${prep.output}"
loop:
  body: [loopbody]
  until: ${loopbody.output.done == true}
  max_iters: 2
output: { from: loopbody }
""")
    _write(tmp_path / "ph" / "WORKFLOW.yaml", """
version: 1
name: ph
imports: [pk]
inputs: { req: { required: true } }
nodes:
  - id: setup
    agent: scratch
    prompt: "setup ${workflow.inputs.req}"
  - id: maybe
    workflow: pk
    when: ${setup.output.needed == true}
    inputs:
      thing: ${setup.output}
  - id: after
    agent: scratch
    when: ${setup.output.needed == true}
    prompt: "after ${maybe.output}"
output: { from: after }
""")
    r = _compile(tmp_path, "ph")
    assert r.returncode == 0, r.stderr
    blob = _all_segment_js(tmp_path, "ph")
    # The pre-loop node maybe__prep carries the guard ternary (not an unconditional call).
    assert "n_maybe__prep = (" in blob and "needed == true) ?" in blob, blob
    # The loop body still survived (do/while present, if-wrapped).
    assert "do {" in blob
    _node_check_all(tmp_path, "ph")


def _hash_for_guard(tmp_path, guard_expr, tag):
    """Compile an rh-like world whose inlined region carries `guard_expr`, return its
    manifest_hash."""
    root = tmp_path / tag
    _write(root / "rk" / "WORKFLOW.yaml", """
version: 1
name: rk
inputs: { thing: { required: true } }
nodes:
  - id: body
    agent: scratch
    prompt: "body ${workflow.inputs.thing}"
loop:
  body: [body]
  until: ${body.output.done == true}
  max_iters: 2
output: { from: body }
""")
    _write(root / "rh" / "WORKFLOW.yaml", f"""
version: 1
name: rh
imports: [rk]
inputs: {{ req: {{ required: true }} }}
nodes:
  - id: plan
    agent: scratch
    prompt: "plan ${{workflow.inputs.req}}"
  - id: impl
    workflow: rk
    when: ${{plan.output.scope_advisory {guard_expr}}}
    inputs:
      thing: ${{plan.output}}
output: {{ from: impl }}
""")
    r = _compile(root, "rh")
    assert r.returncode == 0, r.stderr
    import json as _json
    return _json.loads(r.stdout)["manifest_hash"]


def test_region_guard_edit_moves_manifest_hash(tmp_path):
    # FINDING 2 (major): a region-guard SEMANTIC edit must move manifest_hash (the resume
    # axis), not only the seg-JS digests. `== null` vs `!= null` are opposite guards.
    h_eq = _hash_for_guard(tmp_path, "== null", "eq")
    h_ne = _hash_for_guard(tmp_path, "!= null", "ne")
    assert h_eq != h_ne


def test_inlined_region_max_iters_zero_dies(tmp_path):
    # FINDING 3 (minor): an inlined region's loop bypasses the authored max_iters>=1
    # schema check — enforce it (a max_iters:0 region would deref undefined on the
    # guard-false skip path).
    _write(tmp_path / "zk" / "WORKFLOW.yaml", """
version: 1
name: zk
inputs: { thing: { required: true } }
nodes:
  - id: b
    agent: scratch
    prompt: "b ${workflow.inputs.thing}"
loop:
  body: [b]
  until: ${b.output.done == true}
  max_iters: 0
output: { from: b }
""")
    _write(tmp_path / "zh" / "WORKFLOW.yaml", """
version: 1
name: zh
imports: [zk]
inputs: { req: { required: true } }
nodes:
  - id: nest
    workflow: zk
    inputs:
      thing: ${workflow.inputs.req}
output: { from: nest }
""")
    r = _compile(tmp_path, "zh")
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "max_iters" in out and ">= 1" in out, out


def test_validate_catches_inlined_region_max_iters(tmp_path):
    # FINDING 4 (parity): validate must catch the same per-region error compile does
    # (validate previously discarded the inlined regions).
    VALIDATE = Path(__file__).resolve().parents[1] / "validate-workflow"
    _write(tmp_path / "zk" / "WORKFLOW.yaml", """
version: 1
name: zk
inputs: { thing: { required: true } }
nodes:
  - id: b
    agent: scratch
    prompt: "b ${workflow.inputs.thing}"
loop:
  body: [b]
  until: ${b.output.done == true}
  max_iters: 0
output: { from: b }
""")
    _write(tmp_path / "zh" / "WORKFLOW.yaml", """
version: 1
name: zh
imports: [zk]
inputs: { req: { required: true } }
nodes:
  - id: nest
    workflow: zk
    inputs:
      thing: ${workflow.inputs.req}
output: { from: nest }
""")
    r = subprocess.run(
        [sys.executable, str(VALIDATE), str(tmp_path / "zh" / "WORKFLOW.yaml"),
         "--defs-root", str(tmp_path)],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "max_iters" in (r.stdout + r.stderr).lower()
