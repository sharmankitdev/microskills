"""Tests for multiple loop regions (sub-PR 2).

A single top-level loop must stay byte-identical (legacy __iter/__max var names);
spliced child loops union into additional regions, each its own do/while segment
with isolated __<origin>_iter vars + per-region on_exhaust gates + carry.
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


def _compiled(defs_root: Path, name: str) -> Path:
    return defs_root / name / ".compiled"


def _all_js(defs_root: Path, name: str) -> str:
    return "\n".join(p.read_text() for p in sorted(_compiled(defs_root, name).glob("seg-*.js")))


def _manifest(defs_root: Path, name: str) -> dict:
    return json.loads((_compiled(defs_root, name) / "manifest.json").read_text())


# ---------------------------------------------------------------- Task 2.1


def test_single_loop_emits_legacy_iter_vars(tmp_path):
    # A plain top-level loop must keep __iter/__max (byte-identical to the
    # pre-region-refactor emit — the __top origin must NOT leak into var names).
    _write(tmp_path / "lp" / "WORKFLOW.yaml", """
version: 1
name: lp
inputs: { x: { required: true } }
nodes:
  - id: pre
    agent: scratch
    prompt: "pre ${workflow.inputs.x}"
  - id: body
    agent: scratch
    prompt: "body ${pre.output}"
gates:
  - id: g_pre
    type: human_approval
    after: pre
    prompt: "go?"
loop:
  body: [body]
  until: ${body.output.done == true}
  max_iters: 3
output: { from: body }
""")
    r = _compile(tmp_path, "lp")
    assert r.returncode == 0, r.stderr
    js = _all_js(tmp_path, "lp")
    assert "__iter = 0" in js
    assert "const __max = 3" in js
    assert "__top_iter" not in js  # the __top origin must NOT leak into var names


def _node_check(defs_root: Path, name: str):
    import shutil
    if shutil.which("node") is None:
        return
    for seg in sorted(_compiled(defs_root, name).glob("seg-*.js")):
        chk = subprocess.run(["node", "--check", str(seg)], capture_output=True, text=True)
        assert chk.returncode == 0, f"{seg.name} failed node --check: {chk.stderr}"


# ---------------------------------------------------------------- Task 2.2


def test_parent_and_child_loops_emit_two_regions(tmp_path):
    # A parent with its OWN loop that inlines a (loop-bearing) child compiles to TWO
    # do/while segments — the __top region (parent) + the spliced child region.
    _write(tmp_path / "ckid" / "WORKFLOW.yaml", """
version: 1
name: ckid
inputs: { thing: { required: true } }
nodes:
  - id: clp
    agent: scratch
    prompt: "child loop ${workflow.inputs.thing}"
loop:
  body: [clp]
  until: ${clp.output.done == true}
  max_iters: 2
output: { from: clp }
""")
    _write(tmp_path / "phost" / "WORKFLOW.yaml", """
version: 1
name: phost
imports: [ckid]
inputs: { req: { required: true } }
nodes:
  - id: a
    agent: scratch
    prompt: "parent loop ${workflow.inputs.req}"
  - id: nest
    workflow: ckid
    depends_on: [a]
    inputs:
      thing: ${a.output}
loop:
  body: [a]
  until: ${a.output.done == true}
  max_iters: 4
output: { from: nest }
""")
    r = _compile(tmp_path, "phost")
    assert r.returncode == 0, r.stderr
    m = _manifest(tmp_path, "phost")
    loop_steps = [s for s in m["steps"] if s.get("is_loop")]
    assert len(loop_steps) == 2, [s.get("nodes") for s in m["steps"]]
    # The parent region body is [a]; the child region body is [nest__clp].
    bodies = sorted(tuple(s["nodes"]) for s in loop_steps)
    assert bodies == [("a",), ("nest__clp",)]
    blob = _all_js(tmp_path, "phost")
    assert blob.count("do {") == 2  # two do/while scaffolds
    assert "const __max = 4" in blob  # parent region
    assert "const __max = 2" in blob  # child region
    _node_check(tmp_path, "phost")


def test_clean_two_body_child_region_isolates(tmp_path):
    # A spliced child loop with a 2-node body isolates cleanly into its own segment
    # via the region-boundary flush (the fail-loud isolation die at the end of the
    # partition is a defensive backstop BEHIND the per-region contiguity check — a
    # genuine non-isolation is caught there first with a clearer message).
    _write(tmp_path / "mkid" / "WORKFLOW.yaml", """
version: 1
name: mkid
inputs: { thing: { required: true } }
nodes:
  - id: lb
    agent: scratch
    prompt: "loop ${workflow.inputs.thing}"
  - id: post
    agent: scratch
    prompt: "post ${lb.output}"
loop:
  body: [lb, post]
  until: ${post.output.done == true}
  max_iters: 2
output: { from: post }
""")
    # mkid's loop body [lb, post] is fine standalone; force a merge by making the
    # parent place a background node INTO the region's topo window via a shared dep —
    # but simplest: a parent that inlines mkid AND has its own loop over the SAME
    # spliced nodes is illegal. Instead, assert the happy path isolates (the guard is
    # exercised structurally by the region-boundary flush); a true non-isolation is
    # only reachable via an interleaving the contiguity check already blocks, so this
    # test pins that a clean two-body child region DOES isolate.
    _write(tmp_path / "mhost" / "WORKFLOW.yaml", """
version: 1
name: mhost
imports: [mkid]
inputs: { req: { required: true } }
nodes:
  - id: seed
    agent: scratch
    prompt: "seed ${workflow.inputs.req}"
  - id: nest
    workflow: mkid
    depends_on: [seed]
    inputs:
      thing: ${seed.output}
output: { from: nest }
""")
    r = _compile(tmp_path, "mhost")
    assert r.returncode == 0, r.stderr
    m = _manifest(tmp_path, "mhost")
    loop_steps = [s for s in m["steps"] if s.get("is_loop")]
    assert len(loop_steps) == 1
    assert set(loop_steps[0]["nodes"]) == {"nest__lb", "nest__post"}
    _node_check(tmp_path, "mhost")


# ---------------------------------------------------------------- Task 2.3


def test_two_regions_two_exhaust_gates(tmp_path):
    # Both the parent loop and the inlined child loop declare on_exhaust: escalate —
    # each gets its OWN synthetic gate (loop_exhaust + loop_exhaust_nest) and its own
    # pseudo-result key (loop + loop_nest). Carry vars stay segment-local.
    _write(tmp_path / "ek" / "WORKFLOW.yaml", """
version: 1
name: ek
inputs:
  thing: { required: true }
  notes: { required: false }
nodes:
  - id: cl
    agent: scratch
    prompt: "child ${workflow.inputs.thing} carry=${loop.carry.findings}"
loop:
  body: [cl]
  until: ${cl.output.done == true}
  max_iters: 2
  carry:
    findings: ${cl.output.findings}
  on_exhaust:
    action: escalate
    on_headless: fail
    notes_input: notes
output: { from: cl }
""")
    _write(tmp_path / "eh" / "WORKFLOW.yaml", """
version: 1
name: eh
imports: [ek]
inputs:
  req: { required: true }
  notes: { required: false }
nodes:
  - id: a
    agent: scratch
    prompt: "parent ${workflow.inputs.req} carry=${loop.carry.acc}"
  - id: nest
    workflow: ek
    depends_on: [a]
    inputs:
      thing: ${a.output}
      notes: ${workflow.inputs.notes}
loop:
  body: [a]
  until: ${a.output.done == true}
  max_iters: 3
  carry:
    acc: ${a.output.acc}
  on_exhaust:
    action: escalate
    on_headless: fail
    notes_input: notes
output: { from: nest }
""")
    r = _compile(tmp_path, "eh")
    assert r.returncode == 0, r.stderr
    m = _manifest(tmp_path, "eh")
    gate_ids = [s.get("gate", {}).get("id") for s in m["steps"]
                if s.get("checkpoint_type") == "gate"]
    assert "loop_exhaust" in gate_ids          # parent __top region gate
    assert "loop_exhaust_nest" in gate_ids     # child region gate
    # Two loop segments, each with its own pseudo-result in produces.
    loop_steps = [s for s in m["steps"] if s.get("is_loop")]
    assert len(loop_steps) == 2
    produces = {p for s in loop_steps for p in s["produces"]}
    assert "loop" in produces and "loop_nest" in produces
    blob = _all_js(tmp_path, "eh")
    assert "carry_acc" in blob          # parent carry (segment-local)
    assert "carry_findings" in blob     # child carry (segment-local — own seg file)
    # The child region's notes_input (a CHILD input 'notes') remaps to the PARENT
    # input the host binding forwards (${workflow.inputs.notes} -> 'notes').
    child_step = next(s for s in loop_steps if set(s["nodes"]) == {"nest__cl"})
    assert child_step["on_exhaust"].get("notes_input") == "notes"
    _node_check(tmp_path, "eh")


def test_loop_output_consumer_rekeyed(tmp_path):
    # A child node OUTSIDE its loop body that consumes ${loop.output...} (the loop
    # pseudo-result) must re-key to the namespaced producing key (loop_nest) when
    # inlined — else it reads the parent's `loop` (cross-wire) or a missing key.
    _write(tmp_path / "ck2" / "WORKFLOW.yaml", """
version: 1
name: ck2
inputs:
  thing: { required: true }
  notes: { required: false }
nodes:
  - id: cl
    agent: scratch
    prompt: "loop ${workflow.inputs.thing}"
  - id: after
    agent: scratch
    depends_on: [cl]
    prompt: "converged=${loop.output.converged} rounds=${loop.output.rounds}"
loop:
  body: [cl]
  until: ${cl.output.done == true}
  max_iters: 2
  on_exhaust:
    action: escalate
    on_headless: fail
    notes_input: notes
output: { from: after }
""")
    _write(tmp_path / "ph2" / "WORKFLOW.yaml", """
version: 1
name: ph2
imports: [ck2]
inputs:
  req: { required: true }
  notes: { required: false }
nodes:
  - id: nest
    workflow: ck2
    inputs:
      thing: ${workflow.inputs.req}
      notes: ${workflow.inputs.notes}
output: { from: nest }
""")
    r = _compile(tmp_path, "ph2")
    assert r.returncode == 0, r.stderr
    m = _manifest(tmp_path, "ph2")
    after_seg = next(s for s in m["steps"]
                     if s.get("kind") == "segment" and "nest__after" in s["nodes"])
    # The consumer needs the NAMESPACED pseudo-result, not the bare `loop`.
    assert "loop_nest" in after_seg["needs"]["nodes"]
    assert "loop" not in after_seg["needs"]["nodes"]
    blob = _all_js(tmp_path, "ph2")
    assert "loop_nest" in blob
    _node_check(tmp_path, "ph2")
