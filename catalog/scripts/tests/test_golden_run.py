"""
Tests for the golden-run harness: run-segment-host (the mocked Node engine
that executes compiled segments) and test-workflow (the Python driver that
compiles a def into a throwaway world, walks the manifest per the dispatcher
contract, and diffs results + the mock call journal against goldens).

Hermetic tests build throwaway segments / worlds under tmp_path and pass all
roots as flags. The two SCENARIO tests intentionally point at the real
catalog/ (review-changes lite, microskill-create autonomous) — their worlds
are still copied into tmp_path, so nothing in the repo is written. Tests that
execute JS require node and skip cleanly when it is absent; the committed
GOLDEN files themselves are asserted on without node.

DOCUMENTED LIMIT (mirrors the scripts' own headers): the harness verifies
compiled artifacts + dispatcher kernels against the DOCUMENTED host contract
(agent/parallel/log/phase globals, args as a JSON string, sequential
deterministic parallel under the mock) — never against the live native
engine.

Run: python3 -m pytest catalog/scripts/tests/test_golden_run.py -v
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOST = REPO / "catalog" / "scripts" / "run-segment-host"
DRIVER = REPO / "catalog" / "scripts" / "test-workflow"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CATALOG_DEFS = REPO / "catalog" / "workflow-defs"
CATALOG_SKILLS = REPO / "catalog" / "microskills"
TEMPLATES = REPO / "templates"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


# ================================================================ host

MINI_META = """\
export const meta = {
  name: "mini__seg1",
  description: "Compiled segment 1 of mini",
  phases: [{ title: "a" }],
}

"""

MINI_BODY = """\
let _args = args
if (typeof _args === 'string') {
  try { _args = JSON.parse(_args) } catch (e) {
    throw new Error('workflow args is not valid JSON (truncated payload?): ' + e.message)
  }
}
const __missing = ["wf_x"].filter((k) => !_args || typeof _args !== 'object' || !(k in _args))
if (__missing.length) throw new Error('workflow args is missing required key(s): ' + __missing.join(', '))
phase("a")
const n_a = await agent("do a with " + _args.wf_x, { label: "ms:alpha", phase: "a" })
const pair = await parallel([() => agent("call b1", { label: "ms:beta" }), () => agent("call b2", { label: "ms:beta" }), null])
log(`mini done ${pair.length}`)
return { "a": n_a, "b1": pair[0], "b2": pair[1], "skipped": pair[2] }
"""

ALPHA = [{"result": {"echoed": "A"}}]


def run_host(tmp, fixtures, body=MINI_BODY, meta=MINI_META, args=None):
    script = tmp / "seg-1.js"
    script.write_text(meta + body)
    fx = tmp / "fixtures.json"
    fx.write_text(json.dumps(fixtures))
    journal = tmp / "calls.jsonl"
    cmd = ["node", str(HOST), "--script", str(script), "--fixtures", str(fx),
           "--journal", str(journal)]
    if args is not None:
        af = tmp / "args.json"
        af.write_text(args)
        cmd += ["--args-file", str(af)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    lines = [json.loads(ln) for ln in journal.read_text().splitlines()] \
        if journal.exists() else []
    return proc.returncode, data, lines


@needs_node
def test_host_ordered_queue_per_label_and_journal(tmp_path):
    # Two same-label calls consume the label's queue IN CALL ORDER; the
    # journal records phase/agent/log events chronologically with the baked
    # executor identity fields.
    fixtures = {"ms:alpha": ALPHA,
                "ms:beta": [{"result": {"n": 1}}, {"result": {"n": 2}}]}
    rc, data, lines = run_host(tmp_path, fixtures, args=json.dumps({"wf_x": "X"}))
    assert rc == 0, data
    assert data["ok"] is True
    assert data["result"] == {"a": {"echoed": "A"}, "b1": {"n": 1},
                              "b2": {"n": 2}, "skipped": None}
    events = [(ln["event"], ln.get("label") or ln.get("name") or ln.get("message"))
              for ln in lines]
    assert events == [("phase", "a"),
                      ("agent", "ms:alpha"),
                      ("agent", "ms:beta"),
                      ("agent", "ms:beta"),
                      ("log", "mini done 3")]
    # the agent line carries the call's prompt + executor identity fields
    assert lines[1]["prompt"] == "do a with X"
    assert lines[1]["agentType"] is None and lines[1]["model"] is None


@needs_node
def test_host_prompt_substring_matchers_disambiguate(tmp_path):
    # Queue entries listed in REVERSE call order, each pinned by a prompt
    # substring matcher — selection follows the matcher, not the position.
    fixtures = {"ms:alpha": ALPHA,
                "ms:beta": [{"match": "b2", "result": {"n": "for-b2"}},
                            {"match": "b1", "result": {"n": "for-b1"}}]}
    rc, data, _ = run_host(tmp_path, fixtures, args=json.dumps({"wf_x": "X"}))
    assert rc == 0
    assert data["result"]["b1"] == {"n": "for-b1"}
    assert data["result"]["b2"] == {"n": "for-b2"}


@needs_node
def test_host_exhausted_queue_fails_loud(tmp_path):
    fixtures = {"ms:alpha": ALPHA, "ms:beta": [{"result": {"n": 1}}]}
    rc, data, lines = run_host(tmp_path, fixtures, args=json.dumps({"wf_x": "X"}))
    assert rc == 1
    assert data["ok"] is False
    assert 'ms:beta' in data["error"] and "no usable entry" in data["error"]
    # the failing call is the journal's last line (journaled before selection)
    assert lines[-1]["event"] == "agent" and lines[-1]["label"] == "ms:beta"


@needs_node
def test_host_unknown_label_fails_loud(tmp_path):
    rc, data, _ = run_host(tmp_path, {"ms:alpha": ALPHA},
                           args=json.dumps({"wf_x": "X"}))
    assert rc == 1
    assert 'no queue for label "ms:beta"' in data["error"]


@needs_node
def test_host_error_fixture_rejects(tmp_path):
    fixtures = {"ms:alpha": [{"error": "boom from the mock"}]}
    rc, data, _ = run_host(tmp_path, fixtures, args=json.dumps({"wf_x": "X"}))
    assert rc == 1
    assert "boom from the mock" in data["error"]


@needs_node
def test_host_args_rides_as_string_and_guard_throws(tmp_path):
    # args is handed to the body as the raw JSON STRING (the documented
    # native-engine shape) — the compiled fail-loud guard parses and checks it.
    fixtures = {"ms:alpha": ALPHA, "ms:beta": [{"result": 1}, {"result": 2}]}
    rc, data, _ = run_host(tmp_path, fixtures, args="{}")
    assert rc == 1
    assert "missing required key" in data["error"] and "wf_x" in data["error"]
    rc, data, _ = run_host(tmp_path, fixtures, args="{ truncated")
    assert rc == 1
    assert "not valid JSON" in data["error"]


@needs_node
def test_host_meta_prefix_required(tmp_path):
    rc, data, _ = run_host(tmp_path, {"ms:alpha": ALPHA}, meta="",
                           args=json.dumps({"wf_x": "X"}))
    assert rc == 2
    assert "export const meta" in data["error"]


@needs_node
def test_host_flat_label_fixture_rejected(tmp_path):
    # Flat label keys are exactly the shape the queue design forbids
    # (ms:<name> collides across sibling nodes / for_each items).
    rc, data, _ = run_host(tmp_path, {"ms:alpha": {"result": {"echoed": "A"}}},
                           args=json.dumps({"wf_x": "X"}))
    assert rc == 2
    assert "ordered queue" in data["error"]


# ================================================================ driver

ECHO_MS_MD = """\
---
name: echo-ms
description: minimal echo microskill for golden-harness tests
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

T_FLOW = """\
version: 1
name: t-flow
inputs:
  topic:
    type: string
    required: true
nodes:
  - id: gen
    use: echo-ms
    inputs:
      topic: ${workflow.inputs.topic}
  - id: fin
    delegation: orchestrator
    depends_on: [gen]
    prompt: Finalize ${gen.output.echoed}
gates:
  - id: ok
    after: gen
    type: human_approval
    prompt: ok?
    options: [approve, abandon]
output:
  from: fin
"""


def make_driver_world(tmp_path, fixtures=None):
    """Throwaway registry (defs + microskills) and scenario dir under tmp_path."""
    src = tmp_path / "src"
    d = src / "workflow-defs" / "t-flow"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(T_FLOW)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    ms = src / "microskills" / "echo-ms"
    (ms / "profiles").mkdir(parents=True)
    (ms / "MICROSKILL.md").write_text(ECHO_MS_MD)
    (ms / "profiles" / "base.yaml").write_text(ECHO_MS_BASE)

    scen_dir = tmp_path / "scenario"
    scen_dir.mkdir()
    (scen_dir / "fixtures.json").write_text(json.dumps(
        fixtures or {"ms:echo-ms": [{"result": {"echoed": "topic!"}}]}))
    (scen_dir / "scenario.json").write_text(json.dumps({
        "workflow": "t-flow",
        "inputs": {"topic": "topic"},
        "fixtures": "fixtures.json",
        "gates": {"ok": "approve"},
        "checkpoints": {"fin": {"done": True}},
        "goldens": {"results": "golden-results.json",
                    "journal": "golden-journal.jsonl"},
    }))
    return src, scen_dir


def run_driver(scen_dir, workdir, src, *extra):
    proc = subprocess.run(
        [sys.executable, str(DRIVER),
         "--scenario", str(scen_dir / "scenario.json"),
         "--workdir", str(workdir),
         "--defs-source", str(src / "workflow-defs"),
         "--skills-source", str(src / "microskills"),
         "--templates-root", str(TEMPLATES), *extra],
        capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


@needs_node
def test_driver_update_then_match_then_mismatch(tmp_path):
    src, scen_dir = make_driver_world(tmp_path)

    # 1. generate goldens
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w1", src,
                                    "--update-goldens")
    assert rc == 0, out + err
    assert data["updated"] is True and data["steps_run"] == 3
    results = json.loads((scen_dir / "golden-results.json").read_text())
    assert results["results"]["gen"] == {"echoed": "topic!"}
    assert results["results"]["ok"] == {"choice": "approve"}
    assert results["output"] == {"done": True}
    journal = [json.loads(ln) for ln in
               (scen_dir / "golden-journal.jsonl").read_text().splitlines()]
    # checkpoint prompt was resolved by run-step eval (the compiler's own JS)
    fin = [ln for ln in journal if ln.get("event") == "checkpoint"][0]
    assert fin["prompt"] == "Finalize topic!"
    # the segment's agent call rode the mocked engine with the ms: label
    agents = [ln for ln in journal if ln.get("event") == "agent"]
    assert [a["label"] for a in agents] == ["ms:echo-ms"]

    # 2. a fresh run in a NEW workdir matches the goldens (path normalization)
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w2", src)
    assert rc == 0, out + err
    assert data["ok"] is True and data["mismatches"] == []

    # 3. a changed fixture (behavior change) is a loud golden mismatch
    (scen_dir / "fixtures.json").write_text(json.dumps(
        {"ms:echo-ms": [{"result": {"echoed": "DRIFTED"}}]}))
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w3", src)
    assert rc == 1
    assert data["ok"] is False
    assert {m["golden"] for m in data["mismatches"]} == {"results", "journal"}
    assert "DRIFTED" in data["mismatches"][0]["diff"]


@needs_node
def test_driver_gate_abandon_stops_cleanly(tmp_path):
    src, scen_dir = make_driver_world(tmp_path)
    scen = json.loads((scen_dir / "scenario.json").read_text())
    scen["gates"]["ok"] = "abandon"
    (scen_dir / "scenario.json").write_text(json.dumps(scen))
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w", src,
                                    "--update-goldens")
    assert rc == 0, out + err
    assert data["stopped_at"] == {"step": 1, "reason": "gate ok: abandon"}
    assert data["steps_run"] == 2  # fin never ran
    results = json.loads((scen_dir / "golden-results.json").read_text())
    assert "fin" not in results["results"]
    assert results["results"]["ok"] == {"choice": "abandon"}


@needs_node
def test_driver_missing_required_input_fails_loud(tmp_path):
    src, scen_dir = make_driver_world(tmp_path)
    scen = json.loads((scen_dir / "scenario.json").read_text())
    del scen["inputs"]["topic"]
    (scen_dir / "scenario.json").write_text(json.dumps(scen))
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w", src)
    assert rc == 1
    assert "required input 'topic'" in data["error"]


@needs_node
def test_driver_missing_checkpoint_result_fails_loud(tmp_path):
    src, scen_dir = make_driver_world(tmp_path)
    scen = json.loads((scen_dir / "scenario.json").read_text())
    del scen["checkpoints"]["fin"]
    (scen_dir / "scenario.json").write_text(json.dumps(scen))
    rc, data, out, err = run_driver(scen_dir, tmp_path / "w", src)
    assert rc == 1
    assert "'fin'" in data["error"]


# ================================================================ scenarios
# The two shipped golden scenarios: the real catalog defs compiled into a
# throwaway world and executed end-to-end under the mocked engine, diffed
# against the COMMITTED goldens. A mismatch means the compiled behavior (or a
# dispatcher kernel) changed — review the diff, then regenerate via
#   catalog/scripts/test-workflow --scenario <scenario.json> --update-goldens \
#     --defs-source catalog/workflow-defs --skills-source catalog/microskills
# and commit the reviewed goldens.

def run_scenario(name, workdir):
    scen = GOLDEN_DIR / name / "scenario.json"
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--scenario", str(scen),
         "--workdir", str(workdir),
         "--defs-source", str(CATALOG_DEFS),
         "--skills-source", str(CATALOG_SKILLS),
         "--templates-root", str(TEMPLATES)],
        capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


@needs_node
def test_scenario_review_changes_lite(tmp_path):
    rc, data, out, err = run_scenario("review-changes-lite", tmp_path)
    assert rc == 0, (json.dumps(data, indent=2) if data else out + err)
    assert data["ok"] is True and data["profile"] == "lite"
    assert data["steps_run"] == 1  # lite is one all-background segment


@needs_node
def test_scenario_review_changes_comprehensive(tmp_path):
    rc, data, out, err = run_scenario("review-changes-comprehensive", tmp_path)
    assert rc == 0, (json.dumps(data, indent=2) if data else out + err)
    assert data["ok"] is True and data["profile"] == "comprehensive"
    assert data["steps_run"] == 1  # comprehensive is one all-background segment


@needs_node
def test_scenario_microskill_create_autonomous(tmp_path):
    rc, data, out, err = run_scenario("microskill-create-autonomous", tmp_path)
    assert rc == 0, (json.dumps(data, indent=2) if data else out + err)
    assert data["ok"] is True and data["profile"] == "autonomous"
    assert data["steps_run"] == 5
    assert data["stopped_at"] is None


# --- committed-golden content pins (no node needed: pure file asserts on the
# load-bearing behaviors the goldens exist to verify) ---

def golden_journal(name):
    path = GOLDEN_DIR / name / "golden-journal.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()]


def golden_results(name):
    return json.loads((GOLDEN_DIR / name / "golden-results.json").read_text())


def test_golden_review_changes_verify_order_and_batching():
    # The verify fan-out (for_each over collect's findings, max_parallel 4 →
    # parallelChunked batches of 4) must call once per finding IN COLLECTION
    # ORDER — even though the committed fixture queue lists the verify entries
    # in REVERSE order pinned by prompt-substring matchers — and the stored
    # fan-out array must preserve that order across the 4+1 batch split.
    journal = golden_journal("review-changes-lite")
    verify_calls = [ln for ln in journal
                    if ln.get("event") == "agent" and ln["label"] == "ms:verify-finding"]
    ids = ["COR-1", "COR-2", "COR-3", "COR-4", "COR-5"]
    assert len(verify_calls) == 5
    for call, fid in zip(verify_calls, ids):
        assert f'\\"id\\":\\"{fid}\\"' in json.dumps(call["prompt"]) or \
               f'"id":"{fid}"' in call["prompt"]
    results = golden_results("review-changes-lite")
    assert [v["finding_id"] for v in results["results"]["verify"]] == ids
    # distinct per-finding fixtures: dedup-degenerate goldens are impossible
    verdicts = [v["verdict"] for v in results["results"]["verify"]]
    assert len(set(verdicts)) > 1


def test_golden_comprehensive_multi_dimension_label_collision():
    # THE ordered-queue label-collision case that motivated the fixture design:
    # the six expanded review_* siblings all call agent() under ONE label
    # (ms:review-dimension). The committed fixture queue lists its entries in
    # REVERSE expansion order, each pinned by a prompt-substring matcher on the
    # node's frozen-resolution path — selection must follow the matcher, so
    # every sibling consumes ITS dimension's fixture (a flat label-keyed
    # fixture, or a positional-only queue, would cross-wire them).
    journal = golden_journal("review-changes-comprehensive")
    rd_calls = [ln for ln in journal
                if ln.get("event") == "agent" and ln["label"] == "ms:review-dimension"]
    dims = ["correctness", "security", "performance", "style",
            "documentation", "test_coverage"]
    assert len(rd_calls) == 6
    for call, dim in zip(rd_calls, dims):
        assert f"resolved/review_{dim}.json" in call["prompt"]
    # per-item expand variation rode in: test-coverage threads threshold 80
    # (the workflow input default) into its own call only
    assert "- threshold: 80" in rd_calls[-1]["prompt"]
    assert all("- threshold:" not in c["prompt"] for c in rd_calls[:-1])
    results = golden_results("review-changes-comprehensive")
    # DISTINCT per-dimension results — six different finding sets landed on
    # six different nodes, and the fan-in kept all of them: collect-findings
    # dedup (file+line+title) cannot degenerate this golden
    first_ids = {n: results["results"][n]["findings"][0]["id"]
                 for n in (f"review_{d}" for d in dims)}
    assert first_ids == {
        "review_correctness": "COR-1", "review_security": "SEC-1",
        "review_performance": "PERF-1", "review_style": "STY-1",
        "review_documentation": "DOC-1", "review_test_coverage": "TC-1"}
    assert results["results"]["collect"]["count"] == 7
    collected_dims = {f["dimension"] for f in results["results"]["collect"]["findings"]}
    assert len(collected_dims) == 6
    # the verify fan-out (max_parallel 4 -> 4+3 batches) preserved collection
    # order across the batch split, exactly like the lite golden
    assert [v["finding_id"] for v in results["results"]["verify"]] == \
        ["COR-1", "COR-2", "SEC-1", "PERF-1", "STY-1", "DOC-1", "TC-1"]
    verdicts = [v["verdict"] for v in results["results"]["verify"]]
    assert len(set(verdicts)) > 1


def test_golden_microskill_create_loop_carry_threading():
    # The implement/evaluate loop ran 2 rounds; round 2's implement prompt
    # embeds round 1's evaluate verdict via loop.carry.last_findings — the
    # carry-threading assertion that previously existed nowhere.
    journal = golden_journal("microskill-create-autonomous")
    impl = [ln for ln in journal
            if ln.get("event") == "agent" and ln["label"] == "ms:task-implement"]
    assert len(impl) == 2
    assert "last_findings: null" in impl[0]["prompt"]
    assert "must stay linear" in impl[1]["prompt"]  # round-1 issue carried
    # the loop's compiled log line states the round count
    logs = [ln["message"] for ln in journal if ln.get("event") == "log"]
    assert any("ran 2 iteration(s)" in m for m in logs)


def test_golden_microskill_create_auto_gate_and_guarded_skip():
    # gate_mode: auto took the author-declared default VERBATIM; the advisory
    # branch (when scope_advisory != null) skipped to a stored null; finalize's
    # resolved prompt carries the substituted plan_path from the shared snippet.
    results = golden_results("microskill-create-autonomous")
    assert results["gate_mode"] == "auto"
    assert results["results"]["approve_plan"] == {"choice": "approve"}
    assert results["results"]["advise"] is None
    journal = golden_journal("microskill-create-autonomous")
    gate = [ln for ln in journal if ln.get("event") == "gate"][0]
    assert gate == {"event": "gate", "gate": "approve_plan",
                    "choice": "approve", "mode": "auto"}
    fin = [ln for ln in journal if ln.get("event") == "checkpoint"
           and ln["node"] == "finalize"][0]
    assert fin["skipped"] is False
    assert ".claude/.workflow-staging/current/plan.yaml" in fin["prompt"]
