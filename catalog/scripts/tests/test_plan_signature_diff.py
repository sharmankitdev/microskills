"""
Tests for plan-signature-diff: the deterministic, agent-independent safety guard
for the tiered-revise flow. It extracts a LOAD-BEARING signature from a plan YAML
and reports whether it changed between a before-snapshot and the edited file.

Cosmetic edits (changed default value, reworded description) must read as
unchanged; contract changes (new/renamed input, flipped required, changed type,
changed output_schema, changed name) and workflow-domain dependency changes
(referenced-component add or dispatch-kind swap, node-id rename, profile/gate/
provision-set change) must read as changed. Malformed YAML must surface a clear
{error} on stderr with exit 2.

The microskill plan.yaml is a FLAT document (top-level name / inputs / steps /
output_schema — per microskill-create/references/planner.md). The workflow
plan.yaml is an OUTER doc (name / missing_microskills / scope_advisory) with the
WORKFLOW design nested in a `plan_yaml: |` BLOCK SCALAR (per
workflow-create/references/planner.md) — the WF fixture mirrors that wrapped shape
so the parse path is genuinely exercised.

Run: python3 -m pytest catalog/scripts/tests/test_plan_signature_diff.py -v
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "plan-signature-diff"

MS_BASE = textwrap.dedent("""\
    name: requirements-document-from-notes
    description: Original description.
    inputs:
      - name: notes_path
        required: true
        type: string
        default: null
      - name: output_path
        required: false
        type: string
        default: "./requirements.md"
    output_schema:
      type: object
      required: [document_path]
      properties:
        document_path: { type: string }
    steps:
      - Read the notes.
      - Write the document.
""")

# The REAL workflow plan shape: an OUTER doc (name / missing_microskills /
# scope_advisory) with the WORKFLOW design nested in a `plan_yaml: |` block
# scalar. name + missing_microskills come from the OUTER doc; the design fields
# (nodes / inputs / gates / output / _new_profiles) live in the INNER document.
WF_BASE = textwrap.dedent("""\
    name: release-notes-pipeline
    missing_microskills:
      - name: extract-pr-links
        requirement: Extract PR links from the changelog.
    scope_advisory: null
    plan_yaml: |
      version: 1
      name: release-notes-pipeline
      description: Original workflow description.
      inputs:
        changelog_path:
          type: string
          required: true
        output_dir:
          type: string
          required: false
          default: ./out
      nodes:
        - id: collect
          use: extract-pr-links
          inputs:
            changelog: ${workflow.inputs.changelog_path}
        - id: write
          agent: prose-writer
          prompt: Write the notes.
      output:
        from: write
""")


def _run(before: Path, after: Path, domain="microskill"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--before", str(before),
         "--after", str(after), "--domain", domain],
        capture_output=True, text=True)


def _write(tmp_path, name, text):
    f = tmp_path / name
    f.write_text(text)
    return f


# --- microskill domain ------------------------------------------------------

def test_cosmetic_default_change_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace('"./requirements.md"', '"./architecture/requirements.md"'))
    r = _run(before, after)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["changed"] is False
    assert out["changed_fields"] == []


def test_description_change_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("Original description.", "Totally reworded description."))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is False
    assert out["changed_fields"] == []


def test_step_wording_change_is_unchanged(tmp_path):
    # Pure step WORDING (count unchanged) is non-load-bearing — step_count is the
    # only step fingerprint, so a reword stays unchanged.
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("Write the document.", "Write the requirements document to disk."))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is False
    assert out["changed_fields"] == []


def test_step_add_is_load_bearing(tmp_path):
    # Adding a step changes step_count even though wording/reorder do not.
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("  - Write the document.",
                                   "  - Write the document.\n  - Validate the output."))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True and "step_count" in out["changed_fields"]


def test_new_input_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", MS_BASE.replace(
        "  - name: output_path",
        "  - name: format\n    required: false\n    type: string\n    default: md\n  - name: output_path"))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True
    assert "inputs" in out["changed_fields"]


def test_required_flip_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", MS_BASE.replace(
        "  - name: output_path\n    required: false",
        "  - name: output_path\n    required: true"))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_type_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", MS_BASE.replace(
        "  - name: output_path\n    required: false\n    type: string",
        "  - name: output_path\n    required: false\n    type: object"))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_output_schema_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("[document_path]", "[document_path, report_path]"))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True and "output_schema" in out["changed_fields"]


def test_name_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("requirements-document-from-notes", "requirements-doc-builder"))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is True and "name" in out["changed_fields"]


def test_identical_microskill_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", MS_BASE)
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is False and out["changed_fields"] == []


def test_malformed_yaml_errors(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", "name: x\n  bad: : :\n")
    r = _run(before, after)
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


def test_non_mapping_plan_errors(tmp_path):
    # A plan YAML that parses to a list (not a mapping) — the documented
    # non-mapping error branch.
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml", "- a\n- b\n")
    r = _run(before, after)
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


def test_missing_file_errors(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = tmp_path / "does-not-exist.yaml"
    r = _run(before, after)
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


# --- workflow domain --------------------------------------------------------

def test_wf_cosmetic_description_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml",
                   WF_BASE.replace("Original workflow description.", "Reworded workflow description."))
    r = _run(before, after, domain="workflow")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["changed"] is False
    assert out["changed_fields"] == []


def test_wf_default_value_change_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace("default: ./out", "default: ./dist"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is False
    assert out["changed_fields"] == []


def test_wf_referenced_component_add_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: write\n      agent: prose-writer\n      prompt: Write the notes.",
        "    - id: enrich\n      use: summarize-diff\n      inputs:\n        x: 1\n"
        "    - id: write\n      agent: prose-writer\n      prompt: Write the notes."))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "referenced_components" in out["changed_fields"]
    assert "node_ids" in out["changed_fields"]


def test_wf_referenced_component_remove_is_load_bearing(tmp_path):
    # Removing the collect/extract-pr-links node drops a referenced component (and
    # a node id) — both must register (mirror of the add case).
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: collect\n      use: extract-pr-links\n      inputs:\n"
        "        changelog: ${workflow.inputs.changelog_path}\n", ""))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "referenced_components" in out["changed_fields"]
    assert "node_ids" in out["changed_fields"]


def test_wf_use_to_agent_same_target_is_load_bearing(tmp_path):
    # A dispatch-KIND swap on the SAME target name must register, since refs are
    # keyed by (kind, name).
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml",
                   WF_BASE.replace("use: extract-pr-links", "agent: extract-pr-links"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "referenced_components" in out["changed_fields"]
    # node ids are unchanged by a same-name kind swap.
    assert "node_ids" not in out["changed_fields"]


def test_wf_workflow_reference_is_tracked(tmp_path):
    # Swap an agent: target for a workflow: target on the same node — exercises
    # the workflow: branch of referenced_components.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml",
                   WF_BASE.replace("agent: prose-writer", "workflow: plan-rvs"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "referenced_components" in out["changed_fields"]


def test_wf_node_id_rename_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace("- id: write", "- id: render"))
    after_text = after.read_text().replace("from: write", "from: render")
    after.write_text(after_text)
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "node_ids" in out["changed_fields"]
    assert "output_from" in out["changed_fields"]


def test_wf_input_required_flip_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    output_dir:\n      type: string\n      required: false",
        "    output_dir:\n      type: string\n      required: true"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_wf_input_type_change_is_load_bearing(tmp_path):
    # Workflow input type IS load-bearing (mirrors the ms side) — a string -> object
    # flip on a declared workflow input must surface as a contract change.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    output_dir:\n      type: string",
        "    output_dir:\n      type: object"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_wf_input_materialize_add_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    changelog_path:\n      type: string\n      required: true",
        "    changelog_path:\n      type: string\n      required: true\n      materialize: file"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_wf_input_materialize_remove_is_load_bearing(tmp_path):
    with_mat = WF_BASE.replace(
        "    changelog_path:\n      type: string\n      required: true",
        "    changelog_path:\n      type: string\n      required: true\n      materialize: file")
    before = _write(tmp_path, "before.yaml", with_mat)
    after = _write(tmp_path, "after.yaml", WF_BASE)
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_wf_name_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    # Replace ONLY the outer name (count=1) to prove name is read from the OUTER doc.
    after = _write(tmp_path, "after.yaml",
                   WF_BASE.replace("name: release-notes-pipeline", "name: release-notes-builder", 1))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "name" in out["changed_fields"]


def test_wf_missing_microskill_requirement_change_is_load_bearing(tmp_path):
    # A changed requirement line on a missing_microskills entry (OUTER doc) is what
    # provision consumes — it must surface.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "Extract PR links from the changelog.",
        "Extract PR links and commit hashes from the changelog."))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "missing_microskills" in out["changed_fields"]


def test_wf_customize_profile_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: collect\n      use: extract-pr-links",
        "    - id: collect\n      use: extract-pr-links\n      customize:\n        profile: lite"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "node_profiles" in out["changed_fields"]


def test_wf_gate_add_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "  output:\n    from: write",
        "  gates:\n    - id: approve\n      after: write\n      type: human_approval\n"
        "  output:\n    from: write"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "gates" in out["changed_fields"]


def test_wf_node_output_schema_change_is_load_bearing(tmp_path):
    # A per-node output_schema (the node's narrowed output contract — what
    # downstream refs read) must register, not just output.from.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: write\n      agent: prose-writer\n      prompt: Write the notes.",
        "    - id: write\n      agent: prose-writer\n      prompt: Write the notes.\n"
        "      output_schema:\n        type: object\n        properties:\n"
        "          notes: { type: string }"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "node_output_schemas" in out["changed_fields"]


def test_wf_scope_advisory_change_is_load_bearing(tmp_path):
    # The OUTER-doc scope_advisory is a value a downstream node branches on — a
    # file-edited advisory must register.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "scope_advisory: null",
        "scope_advisory:\n  kind: split\n  reason: too big\n  recommendation: split it"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "scope_advisory" in out["changed_fields"]


def test_wf_inner_design_name_change_is_load_bearing(tmp_path):
    # The INNER plan_yaml name is the value deployed as the WORKFLOW.yaml name —
    # an inner-only edit (outer name untouched) must register via design_name.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    # The inner name lives in the `plan_yaml` block, indented two spaces; the outer
    # name has no leading space. Replacing the INDENTED form touches only the inner
    # design name, leaving the outer name untouched.
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "  name: release-notes-pipeline", "  name: release-notes-builder"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "design_name" in out["changed_fields"]
    # The OUTER name is unchanged by the indented-inner-only edit.
    assert "name" not in out["changed_fields"]


def test_wf_linear_to_loop_is_load_bearing(tmp_path):
    # Wrapping nodes in a loop block (linear -> loop) must register via `loop`.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "  output:\n    from: write",
        "  loop:\n    while: ${write.output.again}\n    max_iters: 3\n"
        "    body: [write]\n    carry:\n      acc: ${write.output.acc}\n"
        "  output:\n    from: write"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "loop" in out["changed_fields"]


def test_wf_node_when_guard_add_is_load_bearing(tmp_path):
    # Adding a when guard to a node must register via node_control.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: write\n      agent: prose-writer",
        "    - id: write\n      when: ${collect.output.ok}\n      agent: prose-writer"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "node_control" in out["changed_fields"]


def test_wf_depends_on_add_is_load_bearing(tmp_path):
    # A pure-ordering depends_on edge (no ref) must register via node_control.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "    - id: write\n      agent: prose-writer",
        "    - id: write\n      depends_on: [collect]\n      agent: prose-writer"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "node_control" in out["changed_fields"]


def test_wf_edge_rewire_is_load_bearing(tmp_path):
    # Rewiring a node's ${...} input ref to a different upstream id must register
    # via edges (the data-flow edge set), even with node ids unchanged.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "changelog: ${workflow.inputs.changelog_path}",
        "changelog: ${write.output.changelog}"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "edges" in out["changed_fields"]


def test_wf_new_profiles_add_is_load_bearing(tmp_path):
    # A _new_profiles entry in the inner plan_yaml drives what gets minted — an
    # add must register via new_profiles.
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "  output:\n    from: write",
        "  _new_profiles:\n    - {microskill: extract-pr-links, profile: lite}\n"
        "  output:\n    from: write"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "new_profiles" in out["changed_fields"]


def test_wf_new_profiles_same_both_sides_is_unchanged(tmp_path):
    # The SAME _new_profiles set on both sides is non-load-bearing (pins the
    # unchanged case for the new_profiles field).
    with_np = WF_BASE.replace(
        "  output:\n    from: write",
        "  _new_profiles:\n    - {microskill: extract-pr-links, profile: lite}\n"
        "  output:\n    from: write")
    before = _write(tmp_path, "before.yaml", with_np)
    # Reword the description only — new_profiles identical on both sides.
    after = _write(tmp_path, "after.yaml",
                   with_np.replace("Original workflow description.", "Reworded."))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is False
    assert "new_profiles" not in out["changed_fields"]


def test_wf_identical_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE)
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is False and out["changed_fields"] == []


def test_wf_scalar_input_spec_errors(tmp_path):
    # A non-dict (scalar) input spec is a malformed workflow plan — it must surface
    # as a clean {error} + exit 2, never a raw traceback.
    bad = textwrap.dedent("""\
        name: x
        missing_microskills: []
        plan_yaml: |
          version: 1
          name: x
          inputs:
            changelog_path: somescalar
          nodes:
            - id: a
              use: foo
          output:
            from: a
    """)
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", bad)
    r = _run(before, after, domain="workflow")
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)


def test_wf_malformed_inner_plan_yaml_errors(tmp_path):
    # The plan_yaml block scalar itself is malformed YAML.
    bad = "name: x\nmissing_microskills: []\nplan_yaml: |\n  name: x\n    bad: : :\n"
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", bad)
    r = _run(before, after, domain="workflow")
    assert r.returncode == 2
    assert "error" in json.loads(r.stderr)
