"""
Tests for .claude/scripts/validate-microskill.

Invokes the validator as a subprocess and asserts on stdout JSON +
exit code. Covers structural checks on MICROSKILL.md and schema +
semantic checks on profile YAML files.

Run:  python3 -m pytest .claude/scripts/tests/ -v
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "validate-microskill"


def run(*args):
    cmd = [sys.executable, str(SCRIPT), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    return proc.returncode, data, proc.stdout, proc.stderr


MINIMAL_BODY = """\
---
name: {name}
description: fixture skill for testing the validator
---

# Fixture

## Purpose

Test fixture.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| dummy | no | string | placeholder | — |

## Steps

1. **First** — do thing one.
2. **Second** — do thing two.
3. **Third** — do thing three.

## Output

Nothing.

## Failure modes

- **Anything** — stop.
"""

GATE_BODY = MINIMAL_BODY.replace(
    "## Output", "<!-- gate-id: approve -->\n\n## Output")


def write_skill(tmp_path, name, body=None):
    p = tmp_path / "MICROSKILL.md"
    p.write_text(body if body is not None else MINIMAL_BODY.format(name=name))
    return p


def write_cfg(tmp_path, filename, body):
    p = tmp_path / filename
    p.write_text(body)
    return p


def issues_by_loc(data, location):
    return [i for i in data["issues"] if i["location"] == location]


def test_output_schema_accepted(tmp_path):
    skill = write_skill(tmp_path, "os")
    cfg = write_cfg(
        tmp_path, "base.yaml",
        "version: 1\noutput_schema:\n  type: object\n  properties:\n    x: { type: string }\n")
    rc, data, out, err = run(str(skill), str(cfg))
    assert data["pass"] is True, data


def test_empty_output_schema_blocks(tmp_path):
    skill = write_skill(tmp_path, "os")
    cfg = write_cfg(tmp_path, "base.yaml", "version: 1\noutput_schema: {}\n")
    rc, data, out, err = run(str(skill), str(cfg))
    assert rc == 1 and data["pass"] is False


def test_malformed_output_schema_blocks(tmp_path):
    skill = write_skill(tmp_path, "os-bad")
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\noutput_schema:\n  type: not-a-real-type\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(i["severity"] == "block" and "not a valid JSON Schema" in i["message"]
               for i in data["issues"]), data


def test_output_schema_non_object_warns(tmp_path):
    skill = write_skill(tmp_path, "os-arr")
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\noutput_schema:\n  type: array\n  items: { type: string }\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert any(i["severity"] == "warn" and "not 'object'" in i["message"]
               for i in data["issues"]), data


def test_output_schema_required_not_in_properties_warns(tmp_path):
    skill = write_skill(tmp_path, "os-req")
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\noutput_schema:\n  type: object\n  properties:\n    a: { type: string }\n  required: [b]\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert any(i["severity"] == "warn" and "not in properties" in i["message"]
               for i in data["issues"]), data


REQUIRED_BODY = """\
---
name: needs-input
description: fixture with a required input for drift checks
---

# Fixture

## Purpose

Test fixture.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| src | yes | string | required input | — |

## Steps

1. **First** — use src.

## Output

Nothing.

## Failure modes

- **Anything** — stop.
"""


def _blocks(data):
    return [i for i in data["issues"] if i["severity"] == "block"]


def test_required_table_without_base_required_blocks(tmp_path):
    skill = write_skill(tmp_path, "needs-input", REQUIRED_BODY)
    cfg = write_cfg(tmp_path, "base.yaml", "version: 1\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any("marked Required=yes" in i["message"] and i["severity"] == "block"
               for i in data["issues"])


def test_required_table_with_base_required_passes(tmp_path):
    skill = write_skill(tmp_path, "needs-input", REQUIRED_BODY)
    cfg = write_cfg(tmp_path, "base.yaml", "version: 1\ninputs:\n  src:\n    required: true\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 0, err
    assert _blocks(data) == []


def test_required_table_with_inject_from_exempt(tmp_path):
    skill = write_skill(tmp_path, "needs-input", REQUIRED_BODY)
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\ninputs:\n  src:\n    inject_from:\n      env: SRC\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 0, err
    assert _blocks(data) == []


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_minimal_valid_skill_passes(tmp_path):
    skill = write_skill(tmp_path, "minimal-ok")
    code, data, _, err = run(str(skill))
    assert code == 0, err
    assert data["pass"] is True
    assert [i for i in data["issues"] if i["severity"] == "block"] == []


def test_setup_section_rejected(tmp_path):
    body = MINIMAL_BODY.format(name="has-setup") + "\n## Setup\n\nSomething.\n"
    skill = write_skill(tmp_path, "has-setup", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any("## Setup section found" in i["message"] for i in data["issues"])


def test_branching_language_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="branchy").replace(
        "1. **First** — do thing one.",
        "1. **First** — if the file exists then read it.",
    )
    skill = write_skill(tmp_path, "branchy", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    branch_blocks = [
        i for i in data["issues"]
        if i["severity"] == "block" and "branching language" in i["message"]
    ]
    assert branch_blocks, data


def test_naked_if_in_step_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="naked-if").replace(
        "1. **First** — do thing one.",
        "1. **First** — stop if file missing.",
    )
    skill = write_skill(tmp_path, "naked-if", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any("branching language" in i["message"] for i in data["issues"])


def test_for_each_in_step_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="loopy").replace(
        "1. **First** — do thing one.",
        "1. **First** — for each commit, process it.",
    )
    skill = write_skill(tmp_path, "loopy", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any("branching language" in i["message"] for i in data["issues"])


def test_section_heading_casing_diagnostic(tmp_path):
    body = MINIMAL_BODY.format(name="bad-case").replace(
        "## Failure modes", "## Failure Modes"
    )
    skill = write_skill(tmp_path, "bad-case", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    diag = [
        i for i in data["issues"]
        if "Failure modes" in i["message"] and "casing" in i["message"]
    ]
    assert diag, data


def test_inputs_table_default_cell_not_dash_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="bad-default").replace(
        "| dummy | no | string | placeholder | — |",
        "| dummy | no | string | placeholder | ./hardcoded.md |",
    )
    skill = write_skill(tmp_path, "bad-default", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    bad = [
        i for i in data["issues"]
        if i["severity"] == "block"
        and "Default column" in i["message"]
        and "./hardcoded.md" in i["message"]
    ]
    assert bad, data


def test_inputs_table_pipe_in_description_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="pipe-desc").replace(
        "| dummy | no | string | placeholder | — |",
        "| dummy | no | string | accepts a | b format | — |",
    )
    skill = write_skill(tmp_path, "pipe-desc", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    pipe = [
        i for i in data["issues"]
        if i["severity"] == "block" and "more than 5 columns" in i["message"]
    ]
    assert pipe, data


def test_required_cell_non_vocabulary_blocks(tmp_path):
    body = MINIMAL_BODY.format(name="bad-required").replace(
        "| dummy | no | string | placeholder | — |",
        "| dummy | maybe | string | placeholder | — |",
    )
    skill = write_skill(tmp_path, "bad-required", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any(i["severity"] == "block" and "Required column" in i["message"]
               and "yes` or `no" in i["message"] for i in data["issues"]), data


def test_duplicate_input_names_block(tmp_path):
    body = MINIMAL_BODY.format(name="dup-input").replace(
        "| dummy | no | string | placeholder | — |",
        "| dummy | no | string | placeholder | — |\n| dummy | no | string | again | — |",
    )
    skill = write_skill(tmp_path, "dup-input", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any(i["severity"] == "block" and "duplicate input name" in i["message"]
               for i in data["issues"]), data


def test_word_count_cap_blocks(tmp_path):
    long_desc = " ".join(["word"] * 110)
    body = MINIMAL_BODY.format(name="too-many-words").replace(
        "description: fixture skill for testing the validator",
        f"description: {long_desc}",
    )
    skill = write_skill(tmp_path, "too-many-words", body)
    code, data, _, err = run(str(skill))
    assert code == 1, err
    assert any("hard cap is 100" in i["message"] for i in data["issues"])


# ---------------------------------------------------------------------------
# Config + semantic checks
# ---------------------------------------------------------------------------


def test_base_yaml_valid_passes(tmp_path):
    skill = write_skill(tmp_path, "cfg-ok")
    cfg = write_cfg(tmp_path, "base.yaml", "version: 1\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 0, err
    assert data["pass"] is True


def test_profile_block_in_overlay_blocks(tmp_path):
    skill = write_skill(tmp_path, "overlay-profile")
    cfg = write_cfg(
        tmp_path,
        "strict.yaml",
        textwrap.dedent("""\
            version: 1
            profile:
              default: nested
            """),
    )
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    block_msgs = [
        i["message"] for i in data["issues"]
        if i["severity"] == "block"
    ]
    assert any(
        "profile" in m and "only allowed in base.yaml" in m
        for m in block_msgs
    ), data


def test_inputs_default_in_overlay_blocks(tmp_path):
    skill = write_skill(tmp_path, "overlay-default")
    cfg = write_cfg(
        tmp_path,
        "strict.yaml",
        textwrap.dedent("""\
            version: 1
            inputs:
              dummy:
                default: ./not-allowed.md
            """),
    )
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(
        "inputs.<name>.default is base-only" in i["message"]
        for i in data["issues"]
    ), data


def test_unknown_input_in_config_warns(tmp_path):
    skill = write_skill(tmp_path, "unknown-input")
    cfg = write_cfg(
        tmp_path,
        "base.yaml",
        textwrap.dedent("""\
            version: 1
            inputs:
              nonexistent:
                required: true
            """),
    )
    code, data, _, err = run(str(skill), str(cfg))
    warnings = [
        i for i in data["issues"]
        if i["severity"] == "warn" and "nonexistent" in i["message"]
    ]
    assert warnings, data


def test_unknown_step_in_config_warns(tmp_path):
    skill = write_skill(tmp_path, "unknown-step")
    cfg = write_cfg(
        tmp_path,
        "base.yaml",
        textwrap.dedent("""\
            version: 1
            steps:
              "9":
                optional: true
            """),
    )
    code, data, _, err = run(str(skill), str(cfg))
    warnings = [
        i for i in data["issues"]
        if i["severity"] == "warn" and "step 9" in i["message"]
    ]
    assert warnings, data


def test_gates_add_id_collision_blocks(tmp_path):
    skill = write_skill(tmp_path, "gate-collide", GATE_BODY.format(name="gate-collide"))
    cfg = write_cfg(tmp_path, "base.yaml",
                    'version: 1\ngates:\n  add:\n    - id: approve\n      after: "1"\n      type: human_approval\n      prompt: Approve?\n')
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(i["severity"] == "block" and "collides with a gate already declared" in i["message"]
               for i in data["issues"]), data


def test_gates_add_duplicate_id_blocks(tmp_path):
    skill = write_skill(tmp_path, "gate-dup", MINIMAL_BODY.format(name="gate-dup"))
    cfg = write_cfg(tmp_path, "base.yaml",
                    'version: 1\ngates:\n  add:\n    - id: g1\n      after: "1"\n      type: verification\n    - id: g1\n      after: "2"\n      type: verification\n')
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(i["severity"] == "block" and "duplicate id" in i["message"]
               for i in data["issues"]), data


def test_gates_add_new_id_no_block(tmp_path):
    skill = write_skill(tmp_path, "gate-new", MINIMAL_BODY.format(name="gate-new"))
    cfg = write_cfg(tmp_path, "base.yaml",
                    'version: 1\ngates:\n  add:\n    - id: fresh\n      after: "1"\n      type: verification\n')
    code, data, _, err = run(str(skill), str(cfg))
    assert not any("collides with a gate" in i["message"] or "duplicate id" in i["message"]
                   for i in data["issues"]), data


# ---------------------------------------------------------------------------
# steps restructure target-existence checks
# ---------------------------------------------------------------------------


def test_steps_remove_unknown_warns(tmp_path):
    skill = write_skill(tmp_path, "sr", MINIMAL_BODY.format(name="sr"))
    cfg = write_cfg(tmp_path, "base.yaml", "version: 1\nsteps:\n  remove: [9]\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert any(i["severity"] == "warn" and "steps.remove" in i["message"] and "does not exist" in i["message"]
               for i in data["issues"]), data


def test_steps_patch_unknown_warns(tmp_path):
    skill = write_skill(tmp_path, "sp", MINIMAL_BODY.format(name="sp"))
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\nsteps:\n  patch:\n    \"9\":\n      text: replaced\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert any(i["severity"] == "warn" and "steps.patch" in i["message"] and "does not exist" in i["message"]
               for i in data["issues"]), data


def test_steps_add_after_unknown_warns(tmp_path):
    skill = write_skill(tmp_path, "sa", MINIMAL_BODY.format(name="sa"))
    cfg = write_cfg(tmp_path, "base.yaml",
                    "version: 1\nsteps:\n  add:\n    - after: 9\n      text: new step\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert any(i["severity"] == "warn" and "steps.add" in i["message"] and "does not exist" in i["message"]
               for i in data["issues"]), data


def test_profile_step_add_branching_blocks(tmp_path):
    skill = write_skill(tmp_path, "psb", MINIMAL_BODY.format(name="psb"))
    cfg = write_cfg(tmp_path, "strict.yaml",
                    "version: 1\nsteps:\n  add:\n    - after: 1\n      text: if the file exists then read it\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(i["severity"] == "block" and "branching language" in i["message"] and ".text" in i["message"]
               for i in data["issues"]), data


def test_profile_step_patch_branching_blocks(tmp_path):
    skill = write_skill(tmp_path, "psp", MINIMAL_BODY.format(name="psp"))
    cfg = write_cfg(tmp_path, "strict.yaml",
                    "version: 1\nsteps:\n  patch:\n    \"1\":\n      text: for each commit, process it\n")
    code, data, _, err = run(str(skill), str(cfg))
    assert code == 1, err
    assert any(i["severity"] == "block" and "branching language" in i["message"]
               for i in data["issues"]), data
