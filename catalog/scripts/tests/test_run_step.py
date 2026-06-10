"""
Tests for run-step — the dispatcher's deterministic step kernel
(`args`: segment args from needs; `eval`: checkpoint when/for_each/${...}
expressions executed as the compiler's own translated JS under node).

Hermetic: every test builds a throwaway manifest + run-state (+ defs-root for
nested-child checks) under tmp_path and passes all paths as flags — nothing
touches the real repo. Tests that EXECUTE expressions require node and skip
cleanly when it is absent; the fail-loud missing-node path is tested without
node via a scrubbed PATH.

Run: python3 -m pytest catalog/scripts/tests/test_run_step.py -v
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "run-step"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

# The compiler module itself — the parity tests translate expressions with the
# REAL translate_ref and execute the compiled-segment forms directly.
_loader = importlib.machinery.SourceFileLoader(
    "compile_workflow_for_parity", str(REPO / "catalog" / "scripts" / "compile-workflow"))
_spec = importlib.util.spec_from_loader("compile_workflow_for_parity", _loader)
cw = importlib.util.module_from_spec(_spec)
_loader.exec_module(cw)


def run(*args, env=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(REPO), env=env)
    data = None
    if proc.stdout.strip().startswith("{"):
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
    return proc.returncode, data, proc.stdout, proc.stderr


def world(tmp, steps, inputs=None, results=None, state_hash="sha256:h1",
          required_inputs=None, input_defaults=None, step_index=0):
    """Throwaway manifest + run-state pair; returns their paths."""
    manifest = {
        "name": "wf-x",
        "manifest_hash": "sha256:h1",
        "steps": steps,
        "required_inputs": required_inputs or [],
        "input_defaults": input_defaults or {},
    }
    man = tmp / "manifest.json"
    man.write_text(json.dumps(manifest))
    state = {"manifest_hash": state_hash, "step_index": step_index,
             "inputs": inputs or {}, "results": results or {}}
    st = tmp / "run-state.json"
    st.write_text(json.dumps(state))
    return man, st


def seg_step(needs, script=".compiled/seg-1.js"):
    return {"kind": "segment", "index": 1, "script": script,
            "nodes": [], "is_loop": False, "needs": needs, "produces": []}


# ================================================================== args

def test_args_builds_canonical_sorted_args_with_defaults(tmp_path):
    man, st = world(
        tmp_path,
        [seg_step({"wf_inputs": ["diff_path", "mode"], "nodes": ["plan"],
                   "carry": ["notes"]})],
        inputs={"diff_path": "/d.cat"},
        results={"plan": {"plan_path": "/p.yaml"}},
        input_defaults={"mode": "lite"})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["errors"] == []
    assert data["kind"] == "segment"
    assert data["script"] == ".compiled/seg-1.js"
    assert data["args"] == {
        "wf_diff_path": "/d.cat",
        "wf_mode": "lite",            # manifest default applied
        "plan": {"plan_path": "/p.yaml"},
        "carry_notes": None,          # loop carry seed is always null
    }
    # canonical: args keys ride sorted in the emitted JSON
    assert list(data["args"]) == sorted(data["args"])
    assert data["args_bytes"] == len(json.dumps(
        data["args"], sort_keys=True, separators=(",", ":")).encode())


def test_args_ungathered_optional_is_explicit_null(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": ["context"],
                                         "nodes": [], "carry": []})])
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 0
    # presence, not truthiness: the key MUST exist with null, never be omitted
    assert "wf_context" in data["args"] and data["args"]["wf_context"] is None


def test_args_missing_required_input_fails_loud(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": ["diff_path"],
                                         "nodes": [], "carry": []})],
                    required_inputs=["diff_path"])
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("required workflow input 'diff_path'" in e for e in data["errors"])
    assert "args" not in data  # never hand onward a partial args object


def test_args_missing_node_result_fails_loud(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": [], "nodes": ["plan"],
                                         "carry": []})])
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("no recorded result for node 'plan'" in e for e in data["errors"])


def test_args_stored_null_from_guarded_skip_is_legal(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": [], "nodes": ["advise"],
                                         "carry": []})],
                    results={"advise": None})
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 0
    assert "advise" in data["args"] and data["args"]["advise"] is None


def test_args_oversized_fails_loud_and_never_spills(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": [], "nodes": ["big"],
                                         "carry": []})],
                    results={"big": {"blob": "x" * 4096}})
    before = sorted(p.name for p in tmp_path.iterdir())
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0",
                       "--budget", "256")
    assert rc == 1
    msg = " ".join(data["errors"])
    assert "NO AUTO-SPILL" in msg and "budget 256" in msg and "big=" in msg
    assert "args" not in data  # the oversized payload is not handed onward
    # fail loud ONLY: no spill file appeared anywhere in the world
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_args_rejects_checkpoint_step(tmp_path):
    man, st = world(tmp_path, [{"kind": "checkpoint",
                                "checkpoint_type": "gate", "gate": {"id": "g"}}])
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 2
    assert "segment steps only" in data["error"]


def test_args_manifest_hash_mismatch_is_environment_error(tmp_path):
    man, st = world(tmp_path, [seg_step({"wf_inputs": [], "nodes": [],
                                         "carry": []})],
                    state_hash="sha256:OTHER")
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 2
    assert "manifest_hash" in data["error"]


def test_args_pre_shape_run_state_without_inputs_fails_loud(tmp_path):
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"manifest_hash": "sha256:h1", "steps": [
        seg_step({"wf_inputs": [], "nodes": [], "carry": []})]}))
    st = tmp_path / "run-state.json"
    st.write_text(json.dumps({"manifest_hash": "sha256:h1", "step_index": 0,
                              "results": {}}))  # legacy shape: no inputs
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 2
    assert "inputs" in data["error"] and "{manifest_hash, step_index, inputs, results}" in data["error"]


# ================================================================== eval

def orch_step(prompt, when=None, for_each=None, as_var=None, node="advise"):
    step = {"kind": "checkpoint", "checkpoint_type": "orchestrator_node",
            "node": node, "prompt": prompt, "depends_on": []}
    if when:
        step["when"] = when
    if for_each:
        step["for_each"] = for_each
    if as_var:
        step["as"] = as_var
    return step


def nested_step(workflow, inputs, when=None, for_each=None, as_var=None,
                profile=None, node="provision"):
    step = {"kind": "checkpoint", "checkpoint_type": "nested_workflow",
            "node": node, "workflow": workflow, "inputs": inputs,
            "depends_on": []}
    if when:
        step["when"] = when
    if for_each:
        step["for_each"] = for_each
    if as_var:
        step["as"] = as_var
    if profile:
        step["profile"] = profile
    return step


def child_def(defs_root, name, inputs_yaml):
    d = defs_root / name
    d.mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(
        f"name: {name}\ndescription: child\ninputs:\n{inputs_yaml}\nnodes: []\n")
    return d


@needs_node
def test_eval_resolves_prompt_refs_against_inputs_and_results(tmp_path):
    man, st = world(
        tmp_path,
        [orch_step("Finalize ${plan.output.name} into ${workflow.inputs.out_dir}.")],
        inputs={"out_dir": "/staging"},
        results={"plan": {"name": "review-changes"}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["errors"] == []
    assert data["skipped"] is False
    assert data["when"] is None and data["for_each"] is None
    assert data["prompt"] == "Finalize review-changes into /staging."


@needs_node
def test_eval_object_ref_interpolates_as_json_like_segment_ref(tmp_path):
    # __ref(non-string) -> JSON.stringify, verbatim segment-helper semantics
    man, st = world(tmp_path,
                    [orch_step("Advisory: ${plan.output.scope_advisory}")],
                    results={"plan": {"scope_advisory": {"kind": "split"}}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 0
    assert data["prompt"] == 'Advisory: {"kind":"split"}'


@needs_node
def test_eval_false_when_skips_without_evaluating_fan_out(tmp_path):
    # for_each points at a NON-array — but the false guard short-circuits it,
    # exactly like the compiled ternary (a skipped node never evaluates its
    # collection), so this must NOT error.
    man, st = world(
        tmp_path,
        [orch_step("per-item ${m}", when="${plan.output.scope_advisory != null}",
                   for_each="${plan.output.scope_advisory}", as_var="m")],
        results={"plan": {"scope_advisory": None}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["skipped"] is True
    assert data["when"] == {"expr": "${plan.output.scope_advisory != null}",
                            "value": False}
    assert "prompt" not in data and "iterations" not in data
    assert data["for_each"] == {"expr": "${plan.output.scope_advisory}", "as": "m"}


@needs_node
def test_eval_orchestrator_for_each_resolves_prompt_per_item(tmp_path):
    man, st = world(
        tmp_path,
        [orch_step("Create ${spec.name}: ${spec.requirement}",
                   for_each="${plan.output.missing}", as_var="spec")],
        results={"plan": {"missing": [
            {"name": "a", "requirement": "ra"},
            {"name": "b", "requirement": "rb"}]}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["for_each"]["as"] == "spec"
    assert data["for_each"]["items"] == [
        {"name": "a", "requirement": "ra"}, {"name": "b", "requirement": "rb"}]
    assert [it["prompt"] for it in data["iterations"]] == [
        "Create a: ra", "Create b: rb"]


def test_eval_node_named_item_without_for_each_missing_result_fails_loud(tmp_path):
    # REGRESSION: the presence guard excludes the step's `as` var from needed
    # nodes ONLY when for_each is present (a per-item binding exists only in a
    # fan-out). Without for_each, a node literally named 'item' (the `as`
    # default) is an ordinary node ref — an absent recorded result must FAIL
    # LOUD, never render `undefined` with exit 0.
    man, st = world(tmp_path, [orch_step("Use ${item.output.value}")],
                    results={"plan": {"name": "x"}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 1, out + err
    assert any("no recorded result for node 'item'" in e for e in data["errors"])


@needs_node
def test_eval_node_named_item_without_for_each_resolves_recorded_result(tmp_path):
    # The positive twin: with a recorded result, a node named 'item' resolves
    # like any other node ref when no for_each is in play.
    man, st = world(tmp_path, [orch_step("Use ${item.output.value}")],
                    results={"item": {"value": "V"}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["prompt"] == "Use V"


@needs_node
def test_eval_for_each_as_var_still_excluded_from_presence_guard(tmp_path):
    # With for_each present, the `as` binding (default 'item') is NOT a node
    # ref — no recorded result is required for it.
    man, st = world(
        tmp_path,
        [orch_step("Per ${item.name}", for_each="${plan.output.missing}")],
        results={"plan": {"missing": [{"name": "a"}]}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert [it["prompt"] for it in data["iterations"]] == ["Per a"]


@needs_node
def test_eval_for_each_empty_collection_yields_empty_iterations(tmp_path):
    man, st = world(tmp_path,
                    [orch_step("x ${m}", for_each="${plan.output.missing}",
                               as_var="m")],
                    results={"plan": {"missing": []}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 0
    assert data["skipped"] is False
    assert data["for_each"]["items"] == [] and data["iterations"] == []


@needs_node
def test_eval_for_each_non_array_fails_loud(tmp_path):
    man, st = world(tmp_path,
                    [orch_step("x ${m}", for_each="${plan.output.missing}",
                               as_var="m")],
                    results={"plan": {"missing": "not-a-list"}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("not an array" in e for e in data["errors"])


@needs_node
def test_eval_nested_resolves_child_inputs_and_cross_checks_required(tmp_path):
    defs_root = tmp_path / "defs"
    child_def(defs_root, "ms-create",
              "  requirement_path: { required: true }\n"
              "  name: { required: true }\n"
              "  harness_root: { default: harness }")
    man, st = world(
        tmp_path,
        [nested_step("ms-create",
                     {"requirement_path": "${spec.requirement}",
                      "name": "${spec.name}",
                      "harness_root": "${workflow.inputs.harness_root}"},
                     when="${plan.output.advisory == null}",
                     for_each="${plan.output.missing}", as_var="spec")],
        inputs={"harness_root": "harness"},
        results={"plan": {"advisory": None,
                          "missing": [{"name": "a", "requirement": "ra"},
                                      {"name": "b", "requirement": "rb"}]}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0",
                             "--defs-root", str(defs_root))
    assert rc == 0, out + err
    assert data["skipped"] is False
    assert data["child_required_inputs"] == ["name", "requirement_path"]
    assert [it["child_inputs"] for it in data["iterations"]] == [
        {"requirement_path": "ra", "name": "a", "harness_root": "harness"},
        {"requirement_path": "rb", "name": "b", "harness_root": "harness"}]


@needs_node
def test_eval_nested_full_ref_keeps_type_embedded_ref_interpolates(tmp_path):
    defs_root = tmp_path / "defs"
    child_def(defs_root, "child-wf", "  count: { required: true }")
    man, st = world(
        tmp_path,
        [nested_step("child-wf",
                     {"count": "${plan.output.n}",
                      "label": "run ${plan.output.n} of ${workflow.inputs.total}"})],
        inputs={"total": 5},
        results={"plan": {"n": 3}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0",
                       "--defs-root", str(defs_root))
    assert rc == 0
    # full ${ref} keeps the JSON type; embedded refs string-interpolate
    assert data["child_inputs"] == {"count": 3, "label": "run 3 of 5"}


@needs_node
def test_eval_nested_uncovered_required_input_fails_pre_run(tmp_path):
    defs_root = tmp_path / "defs"
    child_def(defs_root, "child-wf",
              "  plan_path: { required: true }\n"
              "  harness_yaml: { required: true }")
    man, st = world(
        tmp_path,
        [nested_step("child-wf", {"plan_path": "${plan.output.plan_path}"})],
        results={"plan": {"plan_path": "/p.yaml"}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0",
                       "--defs-root", str(defs_root))
    assert rc == 1
    assert any("required input 'harness_yaml'" in e for e in data["errors"])
    # the resolved map still rides in the output for diagnosis
    assert data["child_inputs"] == {"plan_path": "/p.yaml"}


@needs_node
def test_eval_nested_null_for_required_input_fails(tmp_path):
    # a guarded-skip null upstream covers the key but not the requirement
    defs_root = tmp_path / "defs"
    child_def(defs_root, "child-wf", "  plan_path: { required: true }")
    man, st = world(
        tmp_path,
        [nested_step("child-wf", {"plan_path": "${plan.output.plan_path}"})],
        results={"plan": {"plan_path": None}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0",
                       "--defs-root", str(defs_root))
    assert rc == 1
    assert any("required input 'plan_path'" in e for e in data["errors"])


@needs_node
def test_eval_nested_profile_overlay_changes_required_set(tmp_path):
    defs_root = tmp_path / "defs"
    d = child_def(defs_root, "child-wf",
                  "  plan_path: { required: true }\n"
                  "  notes: { description: optional in base }")
    (d / "profiles").mkdir()
    (d / "profiles" / "strict.yaml").write_text(
        "inputs:\n  notes: { required: true }\n")
    man, st = world(
        tmp_path,
        [nested_step("child-wf", {"plan_path": "${plan.output.plan_path}"},
                     profile="strict")],
        results={"plan": {"plan_path": "/p.yaml"}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0",
                       "--defs-root", str(defs_root))
    assert rc == 1
    assert data["child_required_inputs"] == ["notes", "plan_path"]
    assert any("required input 'notes'" in e for e in data["errors"])


@needs_node
def test_eval_unrecorded_upstream_node_fails_before_node_runs(tmp_path):
    man, st = world(tmp_path,
                    [orch_step("x", when="${plan.output.pass == true}")],
                    results={})  # plan never stored
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("no recorded result for node 'plan'" in e for e in data["errors"])


def test_eval_missing_node_binary_fails_loud(tmp_path):
    # PATH scrubbed to an empty dir: shutil.which('node') finds nothing. The
    # script itself is launched by absolute interpreter path, so this runs
    # fine on machines with or without node.
    empty = tmp_path / "empty-path"
    empty.mkdir()
    man, st = world(tmp_path, [orch_step("x", when="${plan.output.ok}")],
                    results={"plan": {"ok": True}})
    env = {k: v for k, v in os.environ.items() if k != "PATH"}
    env["PATH"] = str(empty)
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0", env=env)
    assert rc == 2, out + err
    assert "node not found" in data["error"]
    assert "no Python re-implementation" in data["error"]


def test_eval_rejects_gate_and_segment_steps(tmp_path):
    man, st = world(tmp_path, [
        {"kind": "checkpoint", "checkpoint_type": "gate", "gate": {"id": "g"}},
        seg_step({"wf_inputs": [], "nodes": [], "carry": []})])
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 2 and "gates carry no expressions" in data["error"]
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "1")
    assert rc == 2 and "run-step args" in data["error"]


# ============================== expression parity with compiled segments

def segment_form_when(expr, ctx):
    """Execute the COMPILED-SEGMENT form of a `when` guard — the exact
    `(cond) ? 'ran' : 'skipped'` ternary node_call_js emits, with the cond
    translated by the real translate_ref — directly under node. The oracle
    the kernel must agree with."""
    cond = cw.translate_ref(cw.strip_ref_wrapper(expr), set())
    program = (
        "const __chunks = []\n"
        "process.stdin.on('data', (c) => __chunks.push(c))\n"
        "process.stdin.on('end', () => {\n"
        "  const _args = JSON.parse(Buffer.concat(__chunks).toString('utf8'))\n"
        f"  const __v = ({cond}) ? 'ran' : 'skipped'\n"
        "  process.stdout.write(JSON.stringify(__v))\n"
        "})\n")
    proc = subprocess.run(["node", "-e", program], input=json.dumps(ctx),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) == "ran"


# (expression, results, expected) — JS semantics are the contract: string
# equality, negation, numeric compare incl. string coercion, and the two
# missing-field behaviors (falsy compare / == null is TRUE for undefined).
PARITY_CASES = [
    # string ops
    ("${verdict.output.status == 'PASS'}", {"verdict": {"status": "PASS"}}, True),
    ("${verdict.output.status == 'PASS'}", {"verdict": {"status": "FAIL"}}, False),
    ("${verdict.output.status != 'PASS'}", {"verdict": {"status": "FAIL"}}, True),
    ("${verdict.output.msg.includes('blocker')}",
     {"verdict": {"msg": "1 blocker found"}}, True),
    # negation
    ("${!(evaluate.output.pass)}", {"evaluate": {"pass": True}}, False),
    ("${!(evaluate.output.pass)}", {"evaluate": {"pass": False}}, True),
    ("${plan.output.advisory != null}", {"plan": {"advisory": None}}, False),
    # numeric compare (incl. JS string coercion — load-bearing parity)
    ("${gaps.output.gaps_count > 0}", {"gaps": {"gaps_count": 2}}, True),
    ("${gaps.output.gaps_count > 0}", {"gaps": {"gaps_count": 0}}, False),
    ("${gaps.output.gaps_count > 0}", {"gaps": {"gaps_count": "2"}}, True),
    # missing field: undefined compares falsy, but == null is TRUE
    ("${plan.output.nope > 2}", {"plan": {}}, False),
    ("${plan.output.nope == null}", {"plan": {}}, True),
]


@needs_node
@pytest.mark.parametrize("expr,results,expected", PARITY_CASES)
def test_eval_when_parity_with_compiled_segment_semantics(
        tmp_path, expr, results, expected):
    # oracle: the segment ternary form, executed under node
    ctx = dict(results)
    assert segment_form_when(expr, ctx) is expected
    # kernel: run-step eval on the same expression + context
    man, st = world(tmp_path, [orch_step("body", when=expr)], results=results)
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["when"]["value"] is expected
    assert data["skipped"] is (not expected)


@needs_node
def test_eval_missing_object_deref_throws_like_a_segment(tmp_path):
    # plan.output.deep.field where deep is undefined: a compiled segment
    # throws TypeError mid-run; the kernel surfaces the same throw as a
    # structured error instead of hand-evaluating around it.
    man, st = world(tmp_path,
                    [orch_step("x", when="${plan.output.deep.field == 'y'}")],
                    results={"plan": {}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("expression evaluation failed" in e for e in data["errors"])


@needs_node
def test_eval_template_missing_field_renders_undefined_like_segment(tmp_path):
    # `${__ref(undefined)}` inside a template literal renders "undefined" in a
    # compiled segment; the kernel must reproduce, not "fix", that.
    man, st = world(tmp_path, [orch_step("got ${plan.output.nope}")],
                    results={"plan": {}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "0")
    assert rc == 0
    assert data["prompt"] == "got undefined"


# ================================================== spill (output-by-reference)
# compile-workflow records a node's declared spill_outputs as a `spill` map on
# the PRODUCING manifest step; run-step writes the listed fields to the per-run
# ledger (<run_dir>/handoff/<node>.<field>) and substitutes their ABSOLUTE
# paths in the THREADED VIEW only — the committed run-state keeps the values.


def spill_producer_step(spill, nodes=None):
    nodes = nodes if nodes is not None else list(spill)
    return {"kind": "segment", "index": 1, "script": ".compiled/seg-1.js",
            "nodes": nodes, "is_loop": False,
            "needs": {"wf_inputs": [], "nodes": [], "carry": []},
            "produces": nodes, "spill": spill}


def test_args_spills_string_field_verbatim_and_substitutes_path(tmp_path):
    blob = "x" * 4096
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report"]}),
         seg_step({"wf_inputs": [], "nodes": ["review"], "carry": []},
                  script=".compiled/seg-2.js")],
        results={"review": {"report": blob, "count": 3}})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "1",
                             "--budget", "1024")
    assert rc == 0, out + err
    hpath = str(tmp_path / "handoff" / "review.report")
    assert data["args"]["review"]["report"] == hpath   # path, not the value
    assert data["args"]["review"]["count"] == 3        # unspilled field: value
    assert data["spilled"] == {"review": {"report": hpath}}
    # the string field rides VERBATIM (the *_path convention — Read the text)
    assert Path(hpath).read_text() == blob
    # under budget: the big field now rides as a short path
    assert data["args_bytes"] < 1024
    # the committed run-state is NEVER mutated — stored results keep the value
    assert json.loads(st.read_text())["results"]["review"]["report"] == blob


def test_args_spills_non_string_field_as_canonical_json(tmp_path):
    value = {"b": [2, 1], "a": {"k": True}}
    man, st = world(
        tmp_path,
        [spill_producer_step({"collect": ["findings"]}),
         seg_step({"wf_inputs": [], "nodes": ["collect"], "carry": []},
                  script=".compiled/seg-2.js")],
        results={"collect": {"findings": value}})
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "1")
    assert rc == 0
    hpath = data["args"]["collect"]["findings"]
    assert Path(hpath).read_text() == json.dumps(value, indent=2,
                                                 sort_keys=True) + "\n"


def test_args_spilled_guarded_skip_null_still_passes_presence(tmp_path):
    # The S1 args guard asserts key PRESENCE (null is legal). A spilled key
    # normally arrives as a path STRING — which satisfies presence — but a
    # guarded-skip producer stored null: the null must ride through untouched
    # (key present, nothing spilled, no handoff side effects), exactly the
    # presence-not-truthiness rule the compiled guard enforces.
    man, st = world(
        tmp_path,
        [spill_producer_step({"advise": ["report"]}),
         seg_step({"wf_inputs": [], "nodes": ["advise"], "carry": []},
                  script=".compiled/seg-2.js")],
        results={"advise": None})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "1")
    assert rc == 0, out + err
    assert "advise" in data["args"] and data["args"]["advise"] is None
    assert "spilled" not in data
    assert not (tmp_path / "handoff").exists()


def test_args_spill_absent_or_null_field_left_alone(tmp_path):
    # An absent or null FIELD spills nothing (presence rules unchanged;
    # schema conformance is check-step-io's job, never the kernel's).
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report", "extra"]}),
         seg_step({"wf_inputs": [], "nodes": ["review"], "carry": []},
                  script=".compiled/seg-2.js")],
        results={"review": {"count": 1, "extra": None}})
    rc, data, *_ = run("args", "--manifest", str(man),
                       "--run-state", str(st), "--step", "1")
    assert rc == 0
    assert data["args"]["review"] == {"count": 1, "extra": None}
    assert "spilled" not in data
    assert not (tmp_path / "handoff").exists()


def test_args_spill_is_deterministic_and_idempotent(tmp_path):
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report"]}),
         seg_step({"wf_inputs": [], "nodes": ["review"], "carry": []},
                  script=".compiled/seg-2.js")],
        results={"review": {"report": "stable"}})
    rc1, d1, *_ = run("args", "--manifest", str(man),
                      "--run-state", str(st), "--step", "1")
    first = Path(d1["args"]["review"]["report"]).read_bytes()
    rc2, d2, *_ = run("args", "--manifest", str(man),
                      "--run-state", str(st), "--step", "1")
    assert rc1 == rc2 == 0
    assert d1["args"] == d2["args"]
    assert Path(d2["args"]["review"]["report"]).read_bytes() == first


@needs_node
def test_eval_substitutes_spilled_field_as_path_in_prompt(tmp_path):
    # A checkpoint consumer sees the SAME threaded view a segment would: the
    # spilled field resolves to the handoff path, an unspilled sibling to its
    # value (one meaning everywhere downstream of the producer).
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report"]}),
         orch_step("post ${review.output.report} (${review.output.count})")],
        results={"review": {"report": "y" * 2048, "count": 2}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "1")
    assert rc == 0, out + err
    hpath = str(tmp_path / "handoff" / "review.report")
    assert data["prompt"] == f"post {hpath} (2)"
    assert data["spilled"] == {"review": {"report": hpath}}
    assert Path(hpath).read_text() == "y" * 2048


@needs_node
def test_eval_nested_child_input_receives_spilled_path(tmp_path):
    defs_root = tmp_path / "defs"
    child_def(defs_root, "child-wf", "  report_path: { required: true }")
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report"]}),
         nested_step("child-wf", {"report_path": "${review.output.report}"})],
        results={"review": {"report": "z" * 2048}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "1",
                             "--defs-root", str(defs_root))
    assert rc == 0, out + err
    hpath = str(tmp_path / "handoff" / "review.report")
    assert data["child_inputs"] == {"report_path": hpath}


@needs_node
def test_eval_spilled_node_named_item_without_for_each_substitutes_path(tmp_path):
    # REGRESSION twin of the presence-guard fix: the spilled-view substitution
    # loop must also treat a node named 'item' as an ordinary node ref when no
    # for_each is present — its spilled field resolves to the handoff path.
    man, st = world(
        tmp_path,
        [spill_producer_step({"item": ["report"]}),
         orch_step("post ${item.output.report}")],
        results={"item": {"report": "w" * 2048}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "1")
    assert rc == 0, out + err
    hpath = str(tmp_path / "handoff" / "item.report")
    assert data["prompt"] == f"post {hpath}"
    assert data["spilled"] == {"item": {"report": hpath}}


@needs_node
def test_eval_unneeded_spilled_node_is_not_written(tmp_path):
    # Only nodes the step's expressions actually reference are spilled — an
    # unrelated producer with a spill declaration leaves no handoff file.
    man, st = world(
        tmp_path,
        [spill_producer_step({"review": ["report"]}),
         orch_step("finalize ${plan.output.name}")],
        results={"review": {"report": "big"}, "plan": {"name": "x"}})
    rc, data, *_ = run("eval", "--manifest", str(man),
                       "--run-state", str(st), "--step", "1")
    assert rc == 0
    assert data["prompt"] == "finalize x"
    assert "spilled" not in data
    assert not (tmp_path / "handoff").exists()


def test_args_spill_integration_with_compiled_manifest(tmp_path):
    # Shape integration (the test_check_step_io precedent): the spill map
    # run-step consumes is the one compile-workflow ACTUALLY records on the
    # producing step — compile a hermetic spill def, then build the consuming
    # segment's args against the EMITTED manifest. Hand-built fixtures above
    # cannot drift from this shape unnoticed.
    defs_root = tmp_path / "defs"
    d = defs_root / "sp-int"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text("""\
version: 1
name: sp-int
nodes:
  - id: review
    agent: ag
    prompt: review it
    output_schema:
      type: object
      required: [report, count]
      properties:
        report: { type: string }
        count: { type: integer }
    spill_outputs: [report]
  - id: post
    agent: ag
    prompt: post ${review.output.report} (${review.output.count})
gates:
  - id: g1
    after: review
    type: human_approval
    prompt: ok?
""")
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    env = {**os.environ,
           "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}
    proc = subprocess.run(
        [sys.executable, str(REPO / "catalog" / "scripts" / "compile-workflow"),
         "sp-int", "--defs-root", str(defs_root)],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    man = d / ".compiled" / "manifest.json"
    manifest = json.loads(man.read_text())
    # 0 = segment[review] (the producer, carrying spill), 1 = gate, 2 = segment[post]
    assert manifest["steps"][0]["spill"] == {"review": ["report"]}
    assert "spill" not in manifest["steps"][2]

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    st = run_dir / "run-state.json"
    st.write_text(json.dumps({
        "manifest_hash": manifest["manifest_hash"], "step_index": 2,
        "inputs": {}, "results": {"review": {"report": "R" * 4096, "count": 1}}}))
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "2",
                             "--budget", "1024")
    assert rc == 0, out + err
    hpath = str(run_dir / "handoff" / "review.report")
    assert data["args"]["review"]["report"] == hpath
    assert data["args"]["review"]["count"] == 1
    assert Path(hpath).read_text() == "R" * 4096
    # the emitted consuming-segment guard still asserts only PRESENCE of the
    # needed key — a path string satisfies it (S1 parity by construction)
    seg2 = (d / manifest["steps"][2]["script"]).read_text()
    assert '["review"]' in seg2 and "__missing" in seg2


# ================================================================== loop.on_exhaust

def _loop_step(carry_vars=("last",), needs_carry=("last",)):
    return {"kind": "segment", "index": 1, "script": ".compiled/seg-1.js",
            "nodes": ["impl", "ev"], "is_loop": True,
            "needs": {"wf_inputs": [], "nodes": [], "carry": sorted(needs_carry)},
            "produces": ["impl", "ev", "loop"],
            "on_exhaust": {"action": "escalate",
                           "carry_vars": sorted(carry_vars)}}


def test_args_extend_flag_seeds_carry_from_committed_loop_result(tmp_path):
    man, st = world(
        tmp_path, [_loop_step(carry_vars=("last", "extra"))],
        results={"impl": {"art": "a"}, "ev": {"pass": False},
                 "loop": {"converged": False, "rounds": 3,
                          "carry": {"last": {"pass": False}, "extra": 0}}})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0", "--extend")
    assert rc == 0, out + err
    # every DECLARED carry var is seeded (carry_vars ∪ needs.carry), and a
    # falsy committed value (0) survives — presence semantics, not truthiness
    assert data["args"] == {"carry_extra": 0, "carry_last": {"pass": False}}


def test_args_fresh_loop_entry_still_seeds_null(tmp_path):
    man, st = world(tmp_path, [_loop_step(carry_vars=("last", "extra"))],
                    results={})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["args"] == {"carry_extra": None, "carry_last": None}


def test_args_escalate_step_without_extend_flag_keeps_null_seeds(tmp_path):
    # the EXPLICIT-flag contract: a committed 'loop' result alone never
    # triggers seeding — a revise-style re-run of the same segment must get
    # today's null seeds, not a silent continuation from the committed carry
    man, st = world(
        tmp_path, [_loop_step(carry_vars=("last", "extra"))],
        results={"loop": {"converged": False, "rounds": 3,
                          "carry": {"last": {"pass": False}, "extra": 0}}})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["args"] == {"carry_extra": None, "carry_last": None}


def test_args_plain_loop_step_keeps_null_seed(tmp_path):
    # no on_exhaust record → today's always-null contract, even if a stray
    # 'loop' result exists in run-state
    step = _loop_step()
    step.pop("on_exhaust")
    man, st = world(tmp_path, [step],
                    results={"loop": {"converged": False, "rounds": 1,
                                      "carry": {"last": {"pass": False}}}})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["args"] == {"carry_last": None}


def test_args_extend_on_non_escalate_step_dies(tmp_path):
    step = _loop_step()
    step.pop("on_exhaust")
    man, st = world(tmp_path, [step], results={})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0", "--extend")
    assert rc == 2
    assert "only valid on a loop segment" in data["error"]


def test_args_extend_without_committed_loop_result_dies(tmp_path):
    man, st = world(tmp_path, [_loop_step()], results={})
    rc, data, out, err = run("args", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0", "--extend")
    assert rc == 2
    assert "committed 'loop' pseudo-result" in data["error"]


def _exhaust_gate_step(when="${!(loop.output.converged)}"):
    return {"kind": "checkpoint", "checkpoint_type": "gate",
            "gate": {"id": "loop_exhaust", "type": "human_approval",
                     "severity": "hard", "prompt": "cap hit",
                     "options": ["extend", "accept", "abandon"],
                     "after": "ev"},
            "when": when}


def test_eval_conditional_gate_fires_when_unconverged(tmp_path):
    man, st = world(tmp_path, [_exhaust_gate_step()],
                    results={"loop": {"converged": False, "rounds": 3,
                                      "carry": {}}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["gate"] == "loop_exhaust"
    assert data["when"] == {"expr": "${!(loop.output.converged)}", "value": True}
    assert data["skipped"] is False
    # a gate carries no prompt/child_inputs payload
    assert "prompt" not in data and "child_inputs" not in data


def test_eval_conditional_gate_skipped_when_converged(tmp_path):
    man, st = world(tmp_path, [_exhaust_gate_step()],
                    results={"loop": {"converged": True, "rounds": 2,
                                      "carry": {}}})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 0, out + err
    assert data["skipped"] is True


def test_eval_conditional_gate_missing_loop_result_fails_loud(tmp_path):
    man, st = world(tmp_path, [_exhaust_gate_step()], results={})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 1
    assert any("no recorded result for node 'loop'" in e
               for e in data["errors"])


def test_eval_plain_gate_step_still_rejected(tmp_path):
    step = _exhaust_gate_step()
    step.pop("when")
    man, st = world(tmp_path, [step], results={})
    rc, data, out, err = run("eval", "--manifest", str(man),
                             "--run-state", str(st), "--step", "0")
    assert rc == 2
    assert "gates carry no expressions" in data["error"]
