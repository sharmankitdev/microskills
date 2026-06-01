"""
Tests for validate-workflow. Run: python3 -m pytest .claude/scripts/tests/ -v

Covers schema + DAG checks via subprocess against tmp_path fixtures, plus an
end-to-end check against the real microskill-create definition.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "validate-workflow"
REAL_FLOW = REPO / "catalog" / "workflow-defs" / "microskill-create"


def run(*paths):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(p) for p in paths]],
        capture_output=True, text=True, cwd=str(REPO))
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


def test_undeclared_output_ref_blocks(tmp_path):
    body = VALID.replace("    depends_on: [a]\n", "")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 1 and data["pass"] is False
    assert any("not in depends_on" in i["message"] for i in data["issues"])


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


def test_when_ref_needs_depends_on(tmp_path):
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
    assert rc == 1
    assert any("not in depends_on" in i["message"] for i in data["issues"])


def test_real_flow_passes():
    rc, data, _ = run(REAL_FLOW / "WORKFLOW.yaml", REAL_FLOW / "profiles" / "base.yaml")
    assert rc == 0, data
    assert data["pass"] is True


def test_real_workflow_create_passes():
    wc = REPO / "catalog" / "workflow-defs" / "workflow-create"
    rc, data, _ = run(wc / "WORKFLOW.yaml", wc / "profiles" / "base.yaml")
    assert rc == 0, data
    assert data["pass"] is True
