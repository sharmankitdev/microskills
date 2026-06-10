"""
Tests for check-step-io — the dispatcher's post-step IO validator.

Hermetic: every test builds a throwaway manifest.json + run-state.json under
tmp_path and passes both PATHS as flags (the checker's paths-only contract —
node output content never rides argv). One integration test compiles a real
hermetic flow with compile-workflow and runs the checker against the manifest
it emitted, so the two tools' io shapes can never drift.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "check-step-io"
COMPILE = REPO / "catalog" / "scripts" / "compile-workflow"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

SCHEMA = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}


def run_check(manifest_path, state_path, step, *extra):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest_path),
         "--run-state", str(state_path), "--step", str(step), *extra],
        capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def world(tmp_path, steps, results, manifest_hash="h1", state_hash=None):
    """Write a minimal manifest + run-state pair; return their paths."""
    manifest = {"name": "t-flow", "steps": steps, "manifest_hash": manifest_hash}
    state = {"manifest_hash": state_hash or manifest_hash, "step_index": 1,
             "results": results}
    mp = tmp_path / "manifest.json"
    sp = tmp_path / "run-state.json"
    mp.write_text(json.dumps(manifest, indent=2))
    sp.write_text(json.dumps(state, indent=2))
    return mp, sp


def seg_step(io, needs_nodes=()):
    produces = list(io)
    return {"kind": "segment", "index": 1, "script": ".compiled/seg-1.js",
            "nodes": produces, "is_loop": False,
            "needs": {"wf_inputs": [], "nodes": sorted(needs_nodes), "carry": []},
            "produces": produces, "io": io}


def by_node(data):
    return {c["node"]: c for c in data["checked"]}


# --- verdict shape 1: a valid value passes -------------------------------------

def test_valid_value_passes(tmp_path):
    steps = [seg_step({"a": {"schema": SCHEMA, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {"x": "hi"}})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 0, out
    assert data["verdict"] == "ok"
    assert by_node(data)["a"]["result"] == "ok"
    assert data["errors"] == [] and data["warnings"] == []


def test_no_schema_no_guard_any_object_passes(tmp_path):
    # io.schema null → nothing to validate; a non-null value is fine as-is.
    steps = [seg_step({"a": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {"anything": 1}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert by_node(data)["a"]["result"] == "ok"


# --- verdict shape 2: guarded null is a LEGAL skip ------------------------------

def test_guarded_null_is_legal(tmp_path):
    steps = [seg_step({"b": {"schema": SCHEMA, "guarded": True, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"b": None})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert data["verdict"] == "ok"
    assert by_node(data)["b"]["result"] == "guarded_null"


def test_guarded_fan_out_null_is_legal(tmp_path):
    # A when-guarded for_each node skips to null (the whole array), legally.
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": True, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": None})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert by_node(data)["scan"]["result"] == "guarded_null"


# --- verdict shape 3: unguarded null/{} + required props = probable truncation --

def test_unguarded_null_with_required_props_is_truncation_error(tmp_path):
    steps = [seg_step({"a": {"schema": SCHEMA, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": None})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 1, out
    assert data["verdict"] == "error"
    assert by_node(data)["a"]["result"] == "truncation_suspected"
    assert any("probable truncation/fabrication" in e for e in data["errors"])


def test_unguarded_empty_object_with_required_props_is_truncation_error(tmp_path):
    steps = [seg_step({"a": {"schema": SCHEMA, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["a"]["result"] == "truncation_suspected"
    assert any("probable truncation/fabrication" in e for e in data["errors"])


def test_unguarded_fan_out_null_with_required_props_is_truncation_error(tmp_path):
    # An unguarded for_each node always yields an ARRAY ([] when empty) — a null
    # with a required-props item schema is the truncation signature, not a skip.
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": False, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": None})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["scan"]["result"] == "truncation_suspected"


def test_unguarded_null_without_schema_passes(tmp_path):
    # No schema → no required props → null is not evidence of truncation.
    steps = [seg_step({"a": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": None})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert by_node(data)["a"]["result"] == "ok"


# --- schema violations (incl. fan_out array-of-schema) --------------------------

def test_schema_violation_fails(tmp_path):
    steps = [seg_step({"a": {"schema": SCHEMA, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {"x": 42}})  # x must be a string
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["a"]["result"] == "schema_violation"


def test_fan_out_validates_array_of_schema(tmp_path):
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": False, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": [{"x": "1"}, {"x": "2"}]})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert by_node(data)["scan"]["result"] == "ok"


def test_fan_out_empty_array_passes(tmp_path):
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": False, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": []})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0


def test_fan_out_bad_item_fails(tmp_path):
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": False, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": [{"x": "ok"}, {"x": 7}]})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["scan"]["result"] == "schema_violation"


def test_fan_out_non_array_fails(tmp_path):
    steps = [seg_step({"scan": {"schema": SCHEMA, "guarded": False, "fan_out": True}})]
    mp, sp = world(tmp_path, steps, {"scan": {"x": "not-an-array"}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["scan"]["result"] == "schema_violation"
    assert any("array" in e for e in data["errors"])


def test_missing_result_fails(tmp_path):
    # The dispatcher must store EVERY produced node (null for a skip) — an absent
    # key means the segment return was dropped or mis-stored.
    steps = [seg_step({"a": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["a"]["result"] == "missing"


# --- verdict shape 4: oversized threaded value WARNS (never an error) ------------

def test_oversized_threaded_value_warns_but_passes(tmp_path):
    steps = [
        seg_step({"big": {"schema": None, "guarded": False, "fan_out": False}}),
        seg_step({"down": {"schema": None, "guarded": False, "fan_out": False}},
                 needs_nodes=["big"]),
    ]
    mp, sp = world(tmp_path, steps, {"big": {"blob": "A" * 40000}})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 0, out                       # warning, NOT an error — the
    assert data["verdict"] == "ok"            # compiled args guard enforces
    assert any("big" in w for w in data["warnings"])
    assert data["errors"] == []


def test_oversized_unthreaded_value_does_not_warn(tmp_path):
    # No later segment needs it → it never rides args → no truncation risk.
    steps = [seg_step({"big": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"big": {"blob": "A" * 40000}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert data["warnings"] == []


def test_budget_flag_raises_threshold(tmp_path):
    steps = [
        seg_step({"big": {"schema": None, "guarded": False, "fan_out": False}}),
        seg_step({"down": {"schema": None, "guarded": False, "fan_out": False}},
                 needs_nodes=["big"]),
    ]
    mp, sp = world(tmp_path, steps, {"big": {"blob": "A" * 40000}})
    rc, data, *_ = run_check(mp, sp, 0, "--budget", "100000")
    assert rc == 0
    assert data["warnings"] == []


# --- checkpoint steps ------------------------------------------------------------

def test_orchestrator_checkpoint_io_validated(tmp_path):
    chk = {"kind": "checkpoint", "checkpoint_type": "orchestrator_node",
           "node": "fin", "prompt": "finalize", "depends_on": [],
           "io": {"fin": {"schema": {"type": "object", "required": ["choice"],
                                     "properties": {"choice": {"type": "string"}}},
                          "guarded": False, "fan_out": False}}}
    mp, sp = world(tmp_path, [chk], {"fin": {"choice": "approve"}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert by_node(data)["fin"]["result"] == "ok"
    # A prose-shaped return (missing the declared field) is a hard stop.
    bad = tmp_path / "bad"
    bad.mkdir()
    mp2, sp2 = world(bad, [chk], {"fin": {"summary": "done!"}})
    rc2, data2, *_ = run_check(mp2, sp2, 0)
    assert rc2 == 1
    assert by_node(data2)["fin"]["result"] == "schema_violation"


def test_gate_step_is_vacuous_pass(tmp_path):
    gate = {"kind": "checkpoint", "checkpoint_type": "gate",
            "gate": {"id": "g1", "after": "a", "type": "human_approval",
                     "prompt": "ok?", "options": ["approve", "abandon"]}}
    mp, sp = world(tmp_path, [gate], {"g1": {"choice": "approve"}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0
    assert data["checked"] == []


def test_step_without_io_block_passes_gracefully(tmp_path):
    # A manifest predating the io block (or a hand-built one): nothing to
    # validate per node beyond result presence.
    step = {"kind": "segment", "index": 1, "script": ".compiled/seg-1.js",
            "nodes": ["a"], "is_loop": False,
            "needs": {"wf_inputs": [], "nodes": [], "carry": []},
            "produces": ["a"]}
    mp, sp = world(tmp_path, [step], {"a": {"whatever": True}})
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 0


# --- environment / wiring errors (exit 2) ----------------------------------------

def test_run_state_from_different_manifest_is_env_error(tmp_path):
    steps = [seg_step({"a": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {}}, manifest_hash="h1", state_hash="OTHER")
    rc, data, *_ = run_check(mp, sp, 0)
    assert rc == 2
    assert "manifest_hash" in data["error"]


def test_step_out_of_range_is_env_error(tmp_path):
    steps = [seg_step({"a": {"schema": None, "guarded": False, "fan_out": False}})]
    mp, sp = world(tmp_path, steps, {"a": {}})
    rc, data, *_ = run_check(mp, sp, 5)
    assert rc == 2
    assert "out of range" in data["error"]


def test_unreadable_manifest_is_env_error(tmp_path):
    sp = tmp_path / "run-state.json"
    sp.write_text("{}")
    rc, data, *_ = run_check(tmp_path / "nope.json", sp, 0)
    assert rc == 2


# --- integration: checker consumes EXACTLY what compile-workflow emits -----------

IO_FLOW = """\
version: 1
name: io-flow
inputs:
  items:
    type: array
    required: true
nodes:
  - id: a
    agent: ag
    prompt: do a
    output_schema:
      type: object
      required: [x]
      properties:
        x: { type: string }
  - id: scan
    agent: ag
    depends_on: [a]
    for_each: ${workflow.inputs.items}
    as: item
    prompt: scan ${item}
    output_schema:
      type: object
      required: [hit]
      properties:
        hit: { type: boolean }
  - id: fin
    delegation: orchestrator
    when: ${a.output.x == 'go'}
    depends_on: [scan]
    prompt: finalize using ${a.output.x}
    output_schema:
      type: object
      required: [choice]
      properties:
        choice: { type: string }
"""


def _compile(defs_root, name):
    proc = subprocess.run(
        [sys.executable, str(COMPILE), name, "--defs-root", str(defs_root)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((defs_root / name / ".compiled" / "manifest.json").read_text())


def test_checker_against_compiled_manifest(tmp_path):
    d = tmp_path / "io-flow"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(IO_FLOW)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    manifest = _compile(tmp_path, "io-flow")

    # The compiled manifest carries the per-node io contracts.
    seg = manifest["steps"][0]
    assert seg["io"]["a"] == {"schema": {"type": "object", "required": ["x"],
                                         "properties": {"x": {"type": "string"}}},
                              "guarded": False, "fan_out": False}
    assert seg["io"]["scan"]["fan_out"] is True
    chk = manifest["steps"][1]
    assert chk["checkpoint_type"] == "orchestrator_node"
    assert chk["io"]["fin"]["guarded"] is True
    assert chk["io"]["fin"]["schema"]["required"] == ["choice"]

    state = tmp_path / "run-state.json"
    mp = d / ".compiled" / "manifest.json"

    # Healthy segment results pass.
    state.write_text(json.dumps({
        "manifest_hash": manifest["manifest_hash"], "step_index": 1,
        "results": {"a": {"x": "go"}, "scan": [{"hit": True}, {"hit": False}]}}))
    rc, data, out, err = run_check(mp, state, 0)
    assert rc == 0, out

    # The truncation signature on the same compiled manifest fails loud.
    state.write_text(json.dumps({
        "manifest_hash": manifest["manifest_hash"], "step_index": 1,
        "results": {"a": {}, "scan": None}}))
    rc, data, *_ = run_check(mp, state, 0)
    assert rc == 1
    assert by_node(data)["a"]["result"] == "truncation_suspected"
    assert by_node(data)["scan"]["result"] == "truncation_suspected"

    # The guarded orchestrator checkpoint: a legal skip and a contract return.
    state.write_text(json.dumps({
        "manifest_hash": manifest["manifest_hash"], "step_index": 2,
        "results": {"a": {"x": "stop"}, "scan": [], "fin": None}}))
    rc, data, *_ = run_check(mp, state, 1)
    assert rc == 0
    assert by_node(data)["fin"]["result"] == "guarded_null"


def test_oversized_spilled_field_does_not_warn(tmp_path):
    # The producing step's recorded `spill` map means run-step threads the
    # field as a short handoff path — the look-ahead sizes THAT view, so a
    # declared spill silences the warning it exists to fix. An unspilled
    # oversized sibling still warns.
    spilled_step = dict(seg_step(
        {"big": {"schema": None, "guarded": False, "fan_out": False}}))
    spilled_step["spill"] = {"big": ["blob"]}
    steps = [
        spilled_step,
        seg_step({"down": {"schema": None, "guarded": False, "fan_out": False}},
                 needs_nodes=["big"]),
    ]
    mp, sp = world(tmp_path, steps, {"big": {"blob": "A" * 40000, "note": "ok"}})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 0, out
    assert data["warnings"] == []
    assert by_node(data)["big"]["threaded_bytes"] < 1000
    # the spill view is sizing-only: an oversized UNSPILLED field still warns
    mp2, sp2 = world(tmp_path, steps, {"big": {"blob": "A" * 40000,
                                               "note": "B" * 40000}})
    rc2, data2, *_ = run_check(mp2, sp2, 0)
    assert rc2 == 0
    assert any("big" in w for w in data2["warnings"])


# ================================================================== loop.on_exhaust

LOOP_IO = {"schema": {"type": "object",
                      "required": ["converged", "rounds", "carry"],
                      "properties": {"converged": {"type": "boolean"},
                                     "rounds": {"type": "integer"},
                                     "carry": {"type": "object"}}},
           "guarded": False, "fan_out": False}


def test_loop_pseudo_result_validates_ok(tmp_path):
    steps = [seg_step({"ev": {"schema": None, "guarded": False, "fan_out": False},
                       "loop": LOOP_IO})]
    steps[0]["is_loop"] = True
    steps[0]["on_exhaust"] = {"action": "escalate", "carry_vars": ["last"]}
    mp, sp = world(tmp_path, steps,
                   {"ev": {"pass": False},
                    "loop": {"converged": False, "rounds": 3,
                             "carry": {"last": {"pass": False}}}})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 0, out + err
    assert by_node(data)["loop"]["result"] == "ok"


def test_null_loop_pseudo_result_trips_truncation(tmp_path):
    steps = [seg_step({"loop": LOOP_IO})]
    steps[0]["is_loop"] = True
    steps[0]["on_exhaust"] = {"action": "escalate", "carry_vars": []}
    mp, sp = world(tmp_path, steps, {"loop": None})
    rc, data, out, err = run_check(mp, sp, 0)
    assert rc == 1
    assert by_node(data)["loop"]["result"] == "truncation_suspected"


def test_escalate_loop_result_sized_as_self_threaded(tmp_path):
    steps = [seg_step({"loop": LOOP_IO})]
    steps[0]["is_loop"] = True
    steps[0]["on_exhaust"] = {"action": "escalate", "carry_vars": ["blob"]}
    big = {"converged": False, "rounds": 3, "carry": {"blob": "x" * 64}}
    mp, sp = world(tmp_path, steps, {"loop": big})
    rc, data, out, err = run_check(mp, sp, 0, "--budget", "32")
    assert rc == 0  # warning-only, like every look-ahead size check
    assert any("'loop'" in w and "threaded" in w for w in data["warnings"])
