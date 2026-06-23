"""
Tests for plan-signature-diff: the deterministic, agent-independent safety guard
for the tiered-revise flow. It extracts a LOAD-BEARING signature from a plan YAML
and reports whether it changed between a before-snapshot and the edited file.

Cosmetic edits (changed default value, reworded description) must read as
unchanged; contract changes (new/renamed input, flipped required, changed type,
changed output_schema, changed name) and workflow-domain dependency changes
(referenced-component add, node-id rename) must read as changed. Malformed YAML
must surface a clear {error} on stderr with exit 2.

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

WF_BASE = textwrap.dedent("""\
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


def test_step_wording_change_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", MS_BASE)
    after = _write(tmp_path, "after.yaml",
                   MS_BASE.replace("Write the document.", "Write the requirements document to disk."))
    out = json.loads(_run(before, after).stdout)
    assert out["changed"] is False


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


def test_wf_referenced_component_add_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE.replace(
        "  - id: write\n    agent: prose-writer\n    prompt: Write the notes.",
        "  - id: enrich\n    use: summarize-diff\n    inputs:\n      x: 1\n"
        "  - id: write\n    agent: prose-writer\n    prompt: Write the notes."))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True
    assert "referenced_components" in out["changed_fields"]
    assert "node_ids" in out["changed_fields"]


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
        "  output_dir:\n    type: string\n    required: false",
        "  output_dir:\n    type: string\n    required: true"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "inputs" in out["changed_fields"]


def test_wf_name_change_is_load_bearing(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml",
                   WF_BASE.replace("release-notes-pipeline", "release-notes-builder"))
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is True and "name" in out["changed_fields"]


def test_wf_identical_is_unchanged(tmp_path):
    before = _write(tmp_path, "before.yaml", WF_BASE)
    after = _write(tmp_path, "after.yaml", WF_BASE)
    out = json.loads(_run(before, after, domain="workflow").stdout)
    assert out["changed"] is False and out["changed_fields"] == []
