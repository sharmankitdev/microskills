"""Tests for catalog/scripts/plan-signature-diff (the tiered-revise guard).

Strip-cosmetic-then-deep-compare: a cosmetic edit (description / input DEFAULT value /
input reorder / plan_yaml reflow) is `changed: false`; ANY other edit is `changed: true`
BY CONSTRUCTION. Includes explicit regression cases for the load-bearing changes an
earlier enumerate-the-signature approach let slip through as false negatives:
loop.body membership, an agent-node prompt ref rewire, a node `delegation` flip, and a
gate `default` change — all of which strip-cosmetic catches because it strips nothing
but descriptions and input defaults.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "plan-signature-diff"

# ---------------------------------------------------------------------------
# Fixtures (built as dicts, dumped to YAML — so the workflow plan_yaml block
# scalar is produced by yaml.safe_dump, never hand-indented).
# ---------------------------------------------------------------------------
MS_BASE = {
    "name": "requirements-document-from-notes",
    "description": "Original component description.",
    "inputs": [
        {"name": "notes_path", "required": True, "type": "string",
         "default": None, "description": "The raw notes."},
        {"name": "output_path", "required": False, "type": "string",
         "default": "./requirements.md", "description": "Where to write."},
    ],
    "steps": ["Read the notes.", "Write the document."],
    "output_schema": {"type": "object", "required": ["document_path"],
                      "properties": {"document_path": {"type": "string"}}},
}

WF_INNER = {
    "name": "my-workflow",
    "inputs": {"req_path": {"required": True, "type": "string",
                            "materialize": "file", "default": None,
                            "description": "the requirement"}},
    "nodes": [
        {"id": "plan", "use": "task-plan",
         "inputs": {"requirement_path": "${workflow.inputs.req_path}"},
         "description": "plan it"},
        {"id": "act", "agent": "general-purpose", "prompt": "do ${plan.output.x}",
         "depends_on": ["plan"]},
    ],
    "gates": [{"id": "approve", "after": "act", "type": "human_approval",
               "default": "approve", "options": ["approve", "abandon"]}],
    "loop": {"body": ["plan", "act"], "while": "${act.output.done}",
             "max_iters": 3, "carry": {"findings": "${act.output.findings}"}},
    "output": {"from": "act"},
}

WF_OUTER = {
    "name": "my-workflow",
    "scope_advisory": None,
    "missing_microskills": [{"name": "foo", "requirement": "do foo"}],
    "plan_yaml": yaml.safe_dump(WF_INNER, sort_keys=False),
}


def _write(tmp_path, name, obj_or_text):
    f = tmp_path / name
    if isinstance(obj_or_text, str):
        f.write_text(obj_or_text)
    else:
        f.write_text(yaml.safe_dump(obj_or_text, sort_keys=False))
    return f


def _run(before, after, domain):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--before", str(before),
         "--after", str(after), "--domain", domain],
        capture_output=True, text=True)


def _diff(tmp_path, domain, before_obj, after_obj):
    b = _write(tmp_path, "before.yaml", before_obj)
    a = _write(tmp_path, "after.yaml", after_obj)
    r = _run(b, a, domain)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _wf_with_inner(inner):
    o = copy.deepcopy(WF_OUTER)
    o["plan_yaml"] = yaml.safe_dump(inner, sort_keys=False)
    return o


# ---------------------------------------------------------------------------
# microskill — cosmetic edits => unchanged
# ---------------------------------------------------------------------------
def test_ms_default_value_change_is_cosmetic(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"][1]["default"] = "./architecture/requirements.md"
    out = _diff(tmp_path, "microskill", MS_BASE, after)
    assert out["changed"] is False and out["changed_fields"] == []


def test_ms_description_change_is_cosmetic(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["description"] = "Totally reworded."
    after["inputs"][0]["description"] = "different prose"
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is False


def test_ms_input_reorder_is_cosmetic(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"] = list(reversed(after["inputs"]))
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is False


# ---------------------------------------------------------------------------
# microskill — contract edits => changed
# ---------------------------------------------------------------------------
def test_ms_new_input_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"].append({"name": "format", "required": False, "type": "string"})
    out = _diff(tmp_path, "microskill", MS_BASE, after)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_ms_input_remove_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    del after["inputs"][1]
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is True


def test_ms_input_rename_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"][0]["name"] = "source_path"
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is True


def test_ms_required_flip_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"][1]["required"] = True
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is True


def test_ms_type_change_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["inputs"][1]["type"] = "integer"
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is True


def test_ms_output_schema_change_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["output_schema"]["required"] = ["document_path", "report_path"]
    out = _diff(tmp_path, "microskill", MS_BASE, after)
    assert out["changed"] is True and "output_schema" in out["changed_fields"]


def test_ms_name_change_is_load_bearing(tmp_path):
    after = copy.deepcopy(MS_BASE)
    after["name"] = "requirements-doc-builder"
    out = _diff(tmp_path, "microskill", MS_BASE, after)
    assert out["changed"] is True and "name" in out["changed_fields"]


def test_ms_step_change_is_load_bearing(tmp_path):
    # Steps define behavior; a step edit conservatively routes to the full re-plan.
    after = copy.deepcopy(MS_BASE)
    after["steps"][1] = "Validate, then write the document."
    assert _diff(tmp_path, "microskill", MS_BASE, after)["changed"] is True


# ---------------------------------------------------------------------------
# workflow — cosmetic edits => unchanged (incl. plan_yaml block reflow)
# ---------------------------------------------------------------------------
def test_wf_inner_input_default_is_cosmetic(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["inputs"]["req_path"]["default"] = "/some/path"
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is False


def test_wf_description_change_is_cosmetic(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["nodes"][0]["description"] = "reworded node prose"
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is False


def test_wf_plan_yaml_reflow_is_cosmetic(tmp_path):
    # Same inner structure, re-serialized with a different key order / formatting.
    after = copy.deepcopy(WF_OUTER)
    after["plan_yaml"] = yaml.safe_dump(WF_INNER, sort_keys=True)
    assert _diff(tmp_path, "workflow", WF_OUTER, after)["changed"] is False


# ---------------------------------------------------------------------------
# workflow — load-bearing edits => changed (the 4 majors the old approach missed)
# ---------------------------------------------------------------------------
def test_wf_loop_body_membership_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["loop"]["body"] = ["plan"]            # dropped 'act' from the body
    out = _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))
    assert out["changed"] is True and "plan_yaml.loop" in out["changed_fields"]


def test_wf_agent_prompt_ref_rewire_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["nodes"][1]["prompt"] = "do ${plan.output.y}"   # rewired ref
    out = _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))
    assert out["changed"] is True and "plan_yaml.nodes" in out["changed_fields"]


def test_wf_node_delegation_flip_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["nodes"][1]["delegation"] = "orchestrator"
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is True


def test_wf_gate_default_change_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["gates"][0]["default"] = "abandon"   # behavior-bearing; NOT an input default
    out = _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))
    assert out["changed"] is True and "plan_yaml.gates" in out["changed_fields"]


def test_wf_referenced_component_add_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["nodes"].append({"id": "extra", "use": "some-new-microskill", "depends_on": ["act"]})
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is True


def test_wf_node_id_change_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["nodes"][1]["id"] = "execute"
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is True


def test_wf_carry_expr_rewire_is_load_bearing(tmp_path):
    inner = copy.deepcopy(WF_INNER)
    inner["loop"]["carry"]["findings"] = "${act.output.other}"
    assert _diff(tmp_path, "workflow", WF_OUTER, _wf_with_inner(inner))["changed"] is True


def test_wf_outer_missing_microskills_is_load_bearing(tmp_path):
    after = copy.deepcopy(WF_OUTER)
    after["missing_microskills"] = [{"name": "bar", "requirement": "do bar"}]
    out = _diff(tmp_path, "workflow", WF_OUTER, after)
    assert out["changed"] is True and "missing_microskills" in out["changed_fields"]


def test_wf_outer_scope_advisory_is_load_bearing(tmp_path):
    after = copy.deepcopy(WF_OUTER)
    after["scope_advisory"] = "too big — split it"
    out = _diff(tmp_path, "workflow", WF_OUTER, after)
    assert out["changed"] is True and "scope_advisory" in out["changed_fields"]


# ---------------------------------------------------------------------------
# error contract => exit 2 + {error} on stderr
# ---------------------------------------------------------------------------
def test_malformed_yaml_errors(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", "name: x\n  bad: : :\n")
    r = _run(before, after, "microskill")
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


def test_malformed_plan_yaml_block_errors(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_OUTER)
    bad = copy.deepcopy(WF_OUTER)
    bad["plan_yaml"] = "nodes: : :\n"
    after = _write(tmp_path, "after.yaml", bad)
    r = _run(before, after, "workflow")
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


def test_non_mapping_plan_errors(tmp_path):
    before = _write(tmp_path, "before.yaml", "- a\n- b\n")
    after = _write(tmp_path, "after.yaml", MS_BASE)
    r = _run(before, after, "microskill")
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)
