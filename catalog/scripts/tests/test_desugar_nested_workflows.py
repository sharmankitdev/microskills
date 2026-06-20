"""Hermetic tests for the desugar_nested_workflows splice pass (sub-PR 1).

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


def _compiled_dir(defs_root: Path, name: str) -> Path:
    return defs_root / name / ".compiled"


def _all_segment_js(defs_root: Path, name: str) -> str:
    return "\n".join(
        p.read_text() for p in sorted(_compiled_dir(defs_root, name).glob("seg-*.js"))
    )


def _manifest(defs_root: Path, name: str) -> dict:
    return json.loads((_compiled_dir(defs_root, name) / "manifest.json").read_text())


def _node_check(defs_root: Path, name: str):
    """Parse-gate every emitted segment with `node --check` (the codegen lesson — a
    green substring suite hides emit bugs). Asserts each seg-*.js is valid JS."""
    import shutil
    if shutil.which("node") is None:
        return  # node unavailable in this environment — skip the parse gate
    for seg in sorted(_compiled_dir(defs_root, name).glob("seg-*.js")):
        chk = subprocess.run(["node", "--check", str(seg)], capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg.name} failed node --check: {chk.stderr}"


# ---------------------------------------------------------------- Task 1.1


def test_no_static_workflow_node_compiles_clean(tmp_path):
    # A leaf workflow with no workflow: node must compile cleanly with the new
    # pass present (the pass returns early — byte-identity floor).
    _write(tmp_path / "leaf" / "WORKFLOW.yaml", """
version: 1
name: leaf
inputs: { x: { required: true } }
nodes:
  - id: only
    agent: scratch
    prompt: "do ${workflow.inputs.x}"
output: { from: only }
""")
    r = _compile(tmp_path, "leaf")
    assert r.returncode == 0, r.stderr
    assert (_compiled_dir(tmp_path, "leaf") / "seg-1.js").exists()
    # No nested_workflow checkpoint in a workflow that has no workflow: node.
    kinds = [s.get("checkpoint_type") for s in _manifest(tmp_path, "leaf")["steps"]]
    assert "nested_workflow" not in kinds


# ---------------------------------------------------------------- Task 1.2


def test_loopless_child_splices_namespaced(tmp_path):
    _write(tmp_path / "kid" / "WORKFLOW.yaml", """
version: 1
name: kid
inputs: { thing: { required: true } }
nodes:
  - id: step1
    agent: scratch
    prompt: "a ${workflow.inputs.thing}"
  - id: step2
    agent: scratch
    prompt: "b ${step1.output}"
output: { from: step2 }
""")
    _write(tmp_path / "host" / "WORKFLOW.yaml", """
version: 1
name: host
imports: [kid]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: kid
    inputs:
      thing: ${seed.output}
  - id: tail
    agent: scratch
    prompt: "tail ${nest.output}"
output: { from: tail }
""")
    r = _compile(tmp_path, "host")
    assert r.returncode == 0, r.stderr
    js = _all_segment_js(tmp_path, "host")
    # Child nodes spliced under the host node id "nest" (<splice>__<inner>).
    assert "n_nest__step1" in js
    assert "n_nest__step2" in js
    # The STATIC nested workflow is inlined — no nested_workflow checkpoint.
    kinds = [s.get("checkpoint_type") for s in _manifest(tmp_path, "host")["steps"]]
    assert "nested_workflow" not in kinds
    # Child input "thing" resolved to the host binding (seed.output): the first
    # spliced node's prompt reads n_seed, not an undeclared workflow input.
    assert "workflow.inputs.thing" not in js
    assert "_args.wf_thing" not in js
    # Downstream "tail" reads the spliced child exit (step2).
    assert "n_tail" in js
    _node_check(tmp_path, "host")


# ---------------------------------------------------------------- Task 1.3


def test_grandchild_inlines_transitively(tmp_path):
    _write(tmp_path / "gc" / "WORKFLOW.yaml", """
version: 1
name: gc
inputs: { v: { required: true } }
nodes:
  - id: leaf
    agent: scratch
    prompt: "leaf ${workflow.inputs.v}"
output: { from: leaf }
""")
    _write(tmp_path / "mid" / "WORKFLOW.yaml", """
version: 1
name: mid
imports: [gc]
inputs: { v: { required: true } }
nodes:
  - id: g
    workflow: gc
    inputs:
      v: ${workflow.inputs.v}
output: { from: g }
""")
    _write(tmp_path / "top" / "WORKFLOW.yaml", """
version: 1
name: top
imports: [mid]
inputs: { v: { required: true } }
nodes:
  - id: m
    workflow: mid
    inputs:
      v: ${workflow.inputs.v}
output: { from: m }
""")
    r = _compile(tmp_path, "top")
    assert r.returncode == 0, r.stderr
    js = _all_segment_js(tmp_path, "top")
    # Bottom-up recursion: mid inlines g->gc first, then top splices under m.
    assert "n_m__g__leaf" in js


def test_static_inline_cycle_rejected(tmp_path):
    _write(tmp_path / "a" / "WORKFLOW.yaml", """
version: 1
name: a
imports: [b]
inputs: { v: { required: true } }
nodes:
  - id: tob
    workflow: b
    inputs:
      v: ${workflow.inputs.v}
output: { from: tob }
""")
    _write(tmp_path / "b" / "WORKFLOW.yaml", """
version: 1
name: b
imports: [a]
inputs: { v: { required: true } }
nodes:
  - id: toa
    workflow: a
    inputs:
      v: ${workflow.inputs.v}
output: { from: toa }
""")
    r = _compile(tmp_path, "a")
    assert r.returncode != 0
    assert "cycle" in (r.stdout + r.stderr).lower()


# ---------------------------------------------------------------- Task 1.4


def test_foreach_child_with_static_grandchild_is_legal(tmp_path):
    # A for_each child that STATICALLY nests a leaf grandchild flattens when the
    # child is compiled, so the for_each re-entry is depth-1 → legal. The retargeted
    # depth check inlines the child's static nests before checking for grandchildren.
    _write(tmp_path / "gc2" / "WORKFLOW.yaml", """
version: 1
name: gc2
inputs: { one: { required: true } }
nodes:
  - id: leaf
    agent: scratch
    prompt: "leaf ${workflow.inputs.one}"
output: { from: leaf }
""")
    _write(tmp_path / "kid2" / "WORKFLOW.yaml", """
version: 1
name: kid2
imports: [gc2]
inputs: { one: { required: true } }
nodes:
  - id: g
    workflow: gc2
    inputs:
      one: ${workflow.inputs.one}
output: { from: g }
""")
    _write(tmp_path / "fan2" / "WORKFLOW.yaml", """
version: 1
name: fan2
imports: [kid2]
inputs: { many: { type: array, required: true } }
nodes:
  - id: each
    workflow: kid2
    for_each: ${workflow.inputs.many}
    as: it
    inputs:
      one: ${it}
output: { from: each }
""")
    r = _compile(tmp_path, "fan2")
    assert r.returncode == 0, r.stderr
    # The for_each workflow node stays a runtime checkpoint (NOT inlined).
    kinds = [s.get("checkpoint_type") for s in _manifest(tmp_path, "fan2")["steps"]]
    assert "nested_workflow" in kinds


def test_foreach_child_with_foreach_grandchild_blocks(tmp_path):
    # A for_each child whose grandchild is ALSO for_each is genuinely non-flat → the
    # retargeted depth check still blocks it (depth-2 runtime nesting).
    _write(tmp_path / "gc3" / "WORKFLOW.yaml", """
version: 1
name: gc3
inputs: { one: { required: true } }
nodes:
  - id: leaf
    agent: scratch
    prompt: "leaf ${workflow.inputs.one}"
output: { from: leaf }
""")
    _write(tmp_path / "kid3" / "WORKFLOW.yaml", """
version: 1
name: kid3
imports: [gc3]
inputs: { many: { type: array, required: true } }
nodes:
  - id: inner
    workflow: gc3
    for_each: ${workflow.inputs.many}
    as: it
    inputs:
      one: ${it}
output: { from: inner }
""")
    _write(tmp_path / "fan3" / "WORKFLOW.yaml", """
version: 1
name: fan3
imports: [kid3]
inputs: { many: { type: array, required: true } }
nodes:
  - id: outer
    workflow: kid3
    for_each: ${workflow.inputs.many}
    as: it
    inputs:
      many: ${it}
output: { from: outer }
""")
    r = _compile(tmp_path, "fan3")
    assert r.returncode != 0
    assert "depth" in (r.stdout + r.stderr).lower()


# -------------------------------------------------- seam-leak fail-loud (§10.3)


def test_child_ref_to_undeclared_input_is_seam_leak(tmp_path):
    # A child node that references ${workflow.inputs.X} for an X the child does NOT
    # declare would silently re-resolve to the PARENT's input X after splicing — a
    # hard error (the child's declared inputs are the only seam).
    _write(tmp_path / "leaky" / "WORKFLOW.yaml", """
version: 1
name: leaky
inputs: { declared_one: { required: true } }
nodes:
  - id: n
    agent: scratch
    prompt: "uses ${workflow.inputs.declared_one} and ${workflow.inputs.sneaky}"
output: { from: n }
""")
    _write(tmp_path / "lhost" / "WORKFLOW.yaml", """
version: 1
name: lhost
imports: [leaky]
inputs: { sneaky: { required: true } }
nodes:
  - id: nest
    workflow: leaky
    inputs:
      declared_one: "literal"
output: { from: nest }
""")
    r = _compile(tmp_path, "lhost")
    assert r.returncode != 0
    assert "seam leak" in (r.stdout + r.stderr).lower()


def test_unbound_optional_child_input_binds_null(tmp_path):
    # An optional child input with no host binding and no default is absent in the
    # inline — its ${workflow.inputs.X} ref must resolve to null, NOT the parent's
    # same-named input (which may not even exist).
    _write(tmp_path / "opt" / "WORKFLOW.yaml", """
version: 1
name: opt
inputs:
  needed: { required: true }
  maybe: { required: false }
nodes:
  - id: n
    agent: scratch
    prompt: "needed=${workflow.inputs.needed} maybe=${workflow.inputs.maybe}"
output: { from: n }
""")
    _write(tmp_path / "ohost" / "WORKFLOW.yaml", """
version: 1
name: ohost
imports: [opt]
inputs: { req: { required: true } }
nodes:
  - id: nest
    workflow: opt
    inputs:
      needed: ${workflow.inputs.req}
output: { from: nest }
""")
    r = _compile(tmp_path, "ohost")
    assert r.returncode == 0, r.stderr
    js = _all_segment_js(tmp_path, "ohost")
    # The unbound optional `maybe` becomes a literal null; no dangling workflow input.
    assert "maybe=null" in js
    assert "wf_maybe" not in js
    _node_check(tmp_path, "ohost")


# ----------------------------------------------- field-tailed / embedded refs (M1)


def test_field_tailed_input_ref_rebinds_with_tail(tmp_path):
    # A child ref ${workflow.inputs.X.field} must rebind to the host binding's inner
    # expression WITH the .field tail riding along (the anchored regex used to miss
    # this, silently leaving a dangling ${workflow.inputs.X.field}).
    _write(tmp_path / "fchild" / "WORKFLOW.yaml", """
version: 1
name: fchild
inputs: { cfg: { required: true } }
nodes:
  - id: n
    agent: scratch
    prompt: "dir=${workflow.inputs.cfg.output_dir}"
output: { from: n }
""")
    _write(tmp_path / "fhost" / "WORKFLOW.yaml", """
version: 1
name: fhost
imports: [fchild]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: fchild
    inputs:
      cfg: ${seed.output}
output: { from: nest }
""")
    r = _compile(tmp_path, "fhost")
    assert r.returncode == 0, r.stderr
    js = _all_segment_js(tmp_path, "fhost")
    # ${workflow.inputs.cfg.output_dir} -> ${seed.output.output_dir} -> n_seed.output_dir
    assert "n_seed.output_dir" in js
    assert "workflow.inputs.cfg" not in js
    assert "wf_cfg" not in js
    _node_check(tmp_path, "fhost")


def test_undeclared_field_form_ref_seam_leaks(tmp_path):
    # A field-tailed ref to an UNDECLARED input must seam-leak (the token-form guard
    # catches it; the old anchored guard missed the .field form).
    _write(tmp_path / "uchild" / "WORKFLOW.yaml", """
version: 1
name: uchild
inputs: { declared: { required: true } }
nodes:
  - id: n
    agent: scratch
    prompt: "a=${workflow.inputs.declared} b=${workflow.inputs.sneaky.field}"
output: { from: n }
""")
    _write(tmp_path / "uhost" / "WORKFLOW.yaml", """
version: 1
name: uhost
imports: [uchild]
inputs: { req: { required: true } }
nodes:
  - id: nest
    workflow: uchild
    inputs:
      declared: ${workflow.inputs.req}
output: { from: nest }
""")
    r = _compile(tmp_path, "uhost")
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "seam leak" in out and "sneaky" in out


# ------------------------------------------- guarded static node stays a checkpoint (M3)


def test_guarded_static_node_is_not_inlined(tmp_path):
    # A `when`-guarded static workflow node must NOT be spliced (that would silently
    # drop the guard). It stays a runtime checkpoint until the region-guard engine.
    _write(tmp_path / "gchild" / "WORKFLOW.yaml", """
version: 1
name: gchild
inputs: { thing: { required: true } }
nodes:
  - id: w
    agent: scratch
    prompt: "w ${workflow.inputs.thing}"
output: { from: w }
""")
    _write(tmp_path / "ghost2" / "WORKFLOW.yaml", """
version: 1
name: ghost2
imports: [gchild]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: gchild
    when: ${seed.output.go == true}
    inputs:
      thing: ${seed.output}
output: { from: nest }
""")
    r = _compile(tmp_path, "ghost2")
    assert r.returncode == 0, r.stderr
    # Guarded static node stays a nested_workflow checkpoint (guard preserved).
    chk = [s for s in _manifest(tmp_path, "ghost2")["steps"]
           if s.get("checkpoint_type") == "nested_workflow"]
    assert len(chk) == 1
    assert chk[0].get("when") == "${seed.output.go == true}"
