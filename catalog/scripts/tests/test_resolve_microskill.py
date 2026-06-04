"""
Tests for .claude/scripts/resolve-microskill.

Invokes the resolver as a subprocess (same path the LLM uses via Bash) and
asserts on stdout JSON + exit code. Tests build hermetic fixture skills under
tmp_path (vars/profiles/allowed_tools, skip steps, mandated tools, inject_from,
gates append, null deletion, output_schema).

Run:  python3 -m pytest .claude/scripts/tests/ -v
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "resolve-microskill"
REAL_MICROSKILLS_ROOT = REPO / "catalog" / "microskills"
# Pin the resolver to the canonical catalog templates/ (config-schema.json) so
# these hermetic tests validate against the source of truth, never a stale
# generated .claude/ mirror. _templates_root honors this env var with highest
# precedence; unset, the resolver's runtime precedence is unchanged.
CATALOG_TEMPLATES_ROOT = REPO / "templates"


def load_resolver():
    """Load the extensionless resolve-microskill script as a module so its pure
    helpers (deep_merge, apply_list_verbs) can be unit-tested directly."""
    loader = importlib.machinery.SourceFileLoader("resolve_microskill", str(SCRIPT))
    spec = importlib.util.spec_from_loader("resolve_microskill", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run(skill, *args, skill_root=None, env=None):
    cmd = [sys.executable, str(SCRIPT), skill, *args]
    if skill_root is not None:
        cmd.extend(["--skill-root", str(skill_root)])
    run_env = dict(env if env is not None else os.environ.copy())
    run_env.setdefault("MICROSKILLS_TEMPLATES_ROOT", str(CATALOG_TEMPLATES_ROOT))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=run_env,
    )
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    return proc.returncode, data, proc.stdout, proc.stderr


def make_skill(root, name, skill_body, default_cfg=None, profiles=None, extras=None):
    """Build a fixture microskill directory under `root` with the new profiles/ layout."""
    sdir = root / name
    sdir.mkdir(parents=True)
    (sdir / "MICROSKILL.md").write_text(skill_body)
    profiles_dir = sdir / "profiles"
    profiles_dir.mkdir()
    base_body = default_cfg if default_cfg is not None else "version: 1\n"
    (profiles_dir / "base.yaml").write_text(base_body)
    for prof_name, body in (profiles or {}).items():
        (profiles_dir / f"{prof_name}.yaml").write_text(body)
    for rel, content in (extras or {}).items():
        path = sdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return sdir


MINIMAL_SKILL = """\
---
name: {name}
description: fixture skill for testing the resolver
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


def test_output_schema_surfaced_and_injected(tmp_path):
    cfg = (
        "version: 1\n"
        "output_schema:\n"
        "  type: object\n"
        "  required: [result]\n"
        "  properties:\n"
        "    result: { type: string }\n"
    )
    make_skill(tmp_path, "os-skill", MINIMAL_SKILL.format(name="os-skill"), default_cfg=cfg)
    rc, data, out, err = run("os-skill", skill_root=tmp_path)
    assert rc == 0, err
    assert data["output_schema"]["required"] == ["result"]
    body = data["rendered_skill_body"]
    assert "## Output (required structured result)" in body
    assert '"result"' in body


def test_no_output_schema_leaves_body_unchanged(tmp_path):
    make_skill(tmp_path, "plain-skill", MINIMAL_SKILL.format(name="plain-skill"))
    rc, data, out, err = run("plain-skill", skill_root=tmp_path)
    assert rc == 0, err
    assert data["output_schema"] is None
    assert "## Output (required structured result)" not in data["rendered_skill_body"]


# ---------------------------------------------------------------------------
# Hermetic fixture mirroring a vars + profiles + allowed_tools + input-default
# skill (these tests formerly ran against the informal-to-ears demo microskill,
# which has been removed; the fixture reproduces the same resolver behaviors).
# ---------------------------------------------------------------------------

VARSKILL_BODY = """\
---
name: var-skill
description: fixture skill with a template_path var, an output_path input, and allowed_tools
---

# Var Skill

## Purpose

Populate the template at {{template_path}} from the supplied text.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| src | yes | string | the supplied text | — |
| output_path | no | string | where to write | — |

## Steps

1. **Ingest** — read the supplied text.
2. **Populate** — fill {{template_path}} and write the document.

## Output

A file at the chosen destination.

## Failure modes

- **Missing input** — stop.
"""

VARSKILL_BASE = (
    "version: 1\n"
    "vars:\n"
    "  template_path: templates/req.template.md\n"
    "inputs:\n"
    "  output_path:\n"
    "    default: ./out.md\n"
    "runtime:\n"
    "  allowed_tools: [Read, Write]\n"
)

VARSKILL_USER_STORY = (
    "version: 1\n"
    "vars:\n"
    "  template_path: templates/story.template.md\n"
    "inputs:\n"
    "  output_path:\n"
    "    required: true\n"
)


def make_var_skill(root):
    return make_skill(root, "var-skill", VARSKILL_BODY,
                      default_cfg=VARSKILL_BASE,
                      profiles={"user-story": VARSKILL_USER_STORY})


def test_no_profile_substitutes_default_vars(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", skill_root=tmp_path)
    assert code == 0, err
    assert data["profile_used"] == "base"
    assert data["profile_requested"] is None
    assert "templates/req.template.md" in data["rendered_skill_body"]
    assert "{{template_path}}" not in data["rendered_skill_body"]
    assert data["unresolved_vars"] == []
    assert data["warnings"] == []


def test_user_story_profile_overrides_template_path(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", "--profile", "user-story", skill_root=tmp_path)
    assert code == 0, err
    assert data["profile_used"] == "user-story"
    assert "templates/story.template.md" in data["rendered_skill_body"]
    assert "templates/req.template.md" not in data["rendered_skill_body"]


def test_bogus_profile_falls_back_with_warning(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", "--profile", "nope", skill_root=tmp_path)
    assert code == 0, err
    assert data["profile_used"] == "base"
    assert data["profile_requested"] == "nope"
    assert any("nope" in w and "using base" in w for w in data["warnings"])


def test_override_replaces_var_value(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run(
        "var-skill", "--override", "vars.template_path=custom/path.md", skill_root=tmp_path)
    assert code == 0, err
    assert "custom/path.md" in data["rendered_skill_body"]
    assert "templates/req.template.md" not in data["rendered_skill_body"]


# ---------------------------------------------------------------------------
# Hermetic fixture tests
# ---------------------------------------------------------------------------


def test_unknown_var_left_literal_and_reported(tmp_path):
    body = MINIMAL_SKILL.format(name="ghost-fixture").replace(
        "Test fixture.",
        "Test fixture. Path: {{ghost}}",
    )
    make_skill(
        tmp_path,
        "ghost-fixture",
        body,
        default_cfg="version: 1\nvars:\n  other: value\n",
    )
    code, data, _, err = run("ghost-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "{{ghost}}" in data["rendered_skill_body"]
    assert data["unresolved_vars"] == ["ghost"]


def test_missing_required_inject_from_env_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="injectenv-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          owner:
            inject_from:
              env: __RESOLVE_TEST_NEVER_SET_XYZ__
        """)
    make_skill(tmp_path, "injectenv-fixture", body, default_cfg=cfg)
    env = {k: v for k, v in os.environ.items() if k != "__RESOLVE_TEST_NEVER_SET_XYZ__"}
    code, data, _, err = run("injectenv-fixture", skill_root=tmp_path, env=env)
    assert code == 1, f"expected block, got {code}: {err}"
    assert any("__RESOLVE_TEST_NEVER_SET_XYZ__" in w for w in data["warnings"])


def test_inject_from_env_resolves(tmp_path):
    body = MINIMAL_SKILL.format(name="injectenv-ok")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          owner:
            inject_from:
              env: __RESOLVE_TEST_PRESENT__
        """)
    make_skill(tmp_path, "injectenv-ok", body, default_cfg=cfg)
    env = {**os.environ, "__RESOLVE_TEST_PRESENT__": "Jane Smith"}
    code, data, _, err = run("injectenv-ok", skill_root=tmp_path, env=env)
    assert code == 0, err
    assert data["injected_inputs"]["owner"] == "Jane Smith"


def test_list_replace_and_gates_append(tmp_path):
    body = MINIMAL_SKILL.format(name="merge-fixture")
    default_cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_tools: [Read, Write]
        gates:
          add:
            - id: gate_one
              after: step_1
              type: verification
        """)
    profile_cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_tools: [Glob]
        gates:
          add:
            - id: gate_two
              after: step_2
              type: verification
        """)
    make_skill(
        tmp_path,
        "merge-fixture",
        body,
        default_cfg=default_cfg,
        profiles={"alt": profile_cfg},
    )
    code, data, _, err = run("merge-fixture", "--profile", "alt", skill_root=tmp_path)
    assert code == 0, err
    assert data["config"]["runtime"]["allowed_tools"] == ["Glob"]
    gate_ids = [g["id"] for g in data["config"]["gates"]["add"]]
    assert gate_ids == ["gate_one", "gate_two"]


def test_skip_optional_step_removes_and_renumbers(tmp_path):
    body = MINIMAL_SKILL.format(name="skip-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          "2":
            optional: true
        """)
    make_skill(tmp_path, "skip-fixture", body, default_cfg=cfg)
    code, data, _, err = run(
        "skip-fixture", "--skip-step", "2", skill_root=tmp_path
    )
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "**Second**" not in rendered
    assert "1. **First**" in rendered
    assert "2. **Third**" in rendered
    assert "3. " not in rendered.split("## Output")[0] or "3. **Third**" not in rendered


def test_skip_non_optional_step_warns_and_does_not_skip(tmp_path):
    body = MINIMAL_SKILL.format(name="skipnon-fixture")
    make_skill(
        tmp_path, "skipnon-fixture", body, default_cfg="version: 1\n"
    )
    code, data, _, err = run(
        "skipnon-fixture", "--skip-step", "2", skill_root=tmp_path
    )
    assert code == 0, err
    assert "**Second**" in data["rendered_skill_body"]
    assert any("2" in w and "optional" in w for w in data["warnings"])


def test_mandated_tool_tags_step(tmp_path):
    body = MINIMAL_SKILL.format(name="mandate-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          "3":
            mandate_tool: Read
        """)
    make_skill(tmp_path, "mandate-fixture", body, default_cfg=cfg)
    code, data, _, err = run("mandate-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert data["directives"]["mandated_tools"] == {"3": "Read"}
    assert "[REQUIRED TOOL: Read]" in data["rendered_skill_body"]
    third_line = [
        ln for ln in data["rendered_skill_body"].splitlines() if ln.startswith("3.")
    ][0]
    assert "[REQUIRED TOOL: Read]" in third_line


def test_context_extend_inlines_artifact(tmp_path):
    body = MINIMAL_SKILL.format(name="extend-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        context:
          extend: prior.md
        """)
    make_skill(
        tmp_path,
        "extend-fixture",
        body,
        default_cfg=cfg,
        extras={"prior.md": "ARTIFACT-CONTENT-MARKER"},
    )
    code, data, _, err = run("extend-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "ARTIFACT-CONTENT-MARKER" in data["context_block"]


def test_context_refs_missing_path_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="refs-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        context:
          refs:
            - missing/ref.md
        """)
    make_skill(tmp_path, "refs-fixture", body, default_cfg=cfg)
    code, data, _, err = run("refs-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert any("missing/ref.md" in w for w in data["warnings"])


def test_null_override_deletes_field(tmp_path):
    body = MINIMAL_SKILL.format(name="null-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        context:
          extend: prior.md
        """)
    make_skill(
        tmp_path,
        "null-fixture",
        body,
        default_cfg=cfg,
        extras={"prior.md": "ORIGINAL"},
    )
    code, data, _, err = run(
        "null-fixture", "--override", "context.extend=null", skill_root=tmp_path
    )
    assert code == 0, err
    assert "extend" not in data["config"].get("context", {})
    assert "ORIGINAL" not in data["context_block"]


def test_snippets_substitute_vars_and_render(tmp_path):
    body = MINIMAL_SKILL.format(name="snip-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        vars:
          project: Atlas
        context:
          snippets:
            - name: rule
              text: "Project {{project}} requires review."
        """)
    make_skill(tmp_path, "snip-fixture", body, default_cfg=cfg)
    code, data, _, err = run("snip-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "Project Atlas requires review." in data["context_block"]
    assert "{{project}}" not in data["context_block"]


def test_missing_skill_dir_exits_2(tmp_path):
    code, data, _, err = run("does-not-exist", skill_root=tmp_path)
    assert code == 2, err


def test_runtime_directives_emit_advisory(tmp_path):
    body = MINIMAL_SKILL.format(name="adv-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_tools: [Read, Glob]
          allowed_mcps: [linear]
        """)
    make_skill(tmp_path, "adv-fixture", body, default_cfg=cfg)
    code, data, _, err = run("adv-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert data["directives"]["allowed_tools"] == ["Read", "Glob"]
    assert data["directives"]["allowed_mcps"] == ["linear"]


def test_gates_severity_override_in_directives(tmp_path):
    body = MINIMAL_SKILL.format(name="gates-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          phase4_approval:
            severity: warn
          add:
            - id: extra_check
              after: step_2
              type: verification
              severity: hard
        """)
    make_skill(tmp_path, "gates-fixture", body, default_cfg=cfg)
    code, data, _, err = run("gates-fixture", skill_root=tmp_path)
    assert code == 0, err
    gates = data["directives"]["gates"]
    by_id = {g["id"]: g for g in gates}
    assert by_id["phase4_approval"]["severity"] == "warn"
    assert by_id["extra_check"]["severity"] == "hard"


def test_malformed_override_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="bad-fixture")
    make_skill(tmp_path, "bad-fixture", body, default_cfg="version: 1\n")
    code, _, _, err = run(
        "bad-fixture", "--override", "no_equals_sign", skill_root=tmp_path
    )
    assert code == 1, err


def test_minimal_base_returns_skill_body_unchanged(tmp_path):
    body = MINIMAL_SKILL.format(name="bare-fixture")
    make_skill(tmp_path, "bare-fixture", body)
    code, data, _, err = run("bare-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "1. **First**" in data["rendered_skill_body"]
    assert data["config"] == {"version": 1}
    assert data["unresolved_vars"] == []


REQUIRED_INPUTS_SKILL = """\
---
name: {name}
description: fixture skill exercising required_inputs ledger
---

# Fixture

## Purpose

Test fixture.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| mypath | no | string | Output file path | ./default.md |
| owner | no | string | Repo owner | — |

## Steps

1. **First** — do thing one.
2. **Second** — do thing two.

## Output

Nothing.

## Failure modes

- **Anything** — stop.
"""


def test_profile_flip_required_adds_to_ledger_and_rewrites_table(tmp_path):
    body = REQUIRED_INPUTS_SKILL.format(name="ledger-flip")
    profile_cfg = textwrap.dedent("""\
        version: 1
        inputs:
          mypath:
            required: true
        """)
    make_skill(
        tmp_path,
        "ledger-flip",
        body,
        default_cfg="version: 1\n",
        profiles={"strict": profile_cfg},
    )
    code, data, _, err = run("ledger-flip", "--profile", "strict", skill_root=tmp_path)
    assert code == 0, err
    assert "mypath" in data["required_inputs"]
    assert "mypath" in data["profile_overrides_inputs"]
    assert data["profile_overrides_inputs"]["mypath"]["default_nullified"] is True
    mypath_rows = [
        ln for ln in data["rendered_skill_body"].splitlines()
        if ln.startswith("| mypath ")
    ]
    assert len(mypath_rows) == 1
    row = mypath_rows[0]
    parts = row.split("|")
    assert parts[2].strip() == "yes"
    assert parts[5].strip() == "—"
    assert "./default.md" not in row


def test_inject_from_with_required_satisfies_and_excludes_from_ledger(tmp_path):
    body = REQUIRED_INPUTS_SKILL.format(name="ledger-injected")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          owner:
            required: true
            inject_from:
              env: __RESOLVE_REQ_INJECT_TEST__
        """)
    make_skill(tmp_path, "ledger-injected", body, default_cfg=cfg)
    env = {**os.environ, "__RESOLVE_REQ_INJECT_TEST__": "alice"}
    code, data, _, err = run("ledger-injected", skill_root=tmp_path, env=env)
    assert code == 0, err
    assert "owner" not in data["required_inputs"]
    assert data["injected_inputs"]["owner"] == "alice"


def test_required_true_no_inject_present_in_ledger(tmp_path):
    body = REQUIRED_INPUTS_SKILL.format(name="ledger-required-no-inject")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          owner:
            required: true
        """)
    make_skill(tmp_path, "ledger-required-no-inject", body, default_cfg=cfg)
    code, data, _, err = run("ledger-required-no-inject", skill_root=tmp_path)
    assert code == 0, err
    assert "owner" in data["required_inputs"]
    assert "owner" in data["profile_overrides_inputs"]


def test_required_true_for_unknown_input_warns_not_in_ledger(tmp_path):
    body = MINIMAL_SKILL.format(name="ledger-ghost")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          ghost:
            required: true
        """)
    make_skill(tmp_path, "ledger-ghost", body, default_cfg=cfg)
    code, data, _, err = run("ledger-ghost", skill_root=tmp_path)
    assert code == 0, err
    assert "ghost" not in data["required_inputs"]
    assert "ghost" not in data["profile_overrides_inputs"]
    assert any("ghost" in w and "not found in MICROSKILL.md" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# Per-step inline reinforcement
# ---------------------------------------------------------------------------


def test_allowed_tools_inlined_on_every_retained_step(tmp_path):
    body = MINIMAL_SKILL.format(name="atools-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_tools: [Read, Write]
        """)
    make_skill(tmp_path, "atools-fixture", body, default_cfg=cfg)
    code, data, _, err = run("atools-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 3
    for ln in step_lines:
        assert "[ALLOWED TOOLS: Read, Write]" in ln, ln


def test_allowed_mcps_inlined_on_every_retained_step(tmp_path):
    body = MINIMAL_SKILL.format(name="amcps-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_mcps: [linear, github]
        """)
    make_skill(tmp_path, "amcps-fixture", body, default_cfg=cfg)
    code, data, _, err = run("amcps-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 3
    for ln in step_lines:
        assert "[ALLOWED MCPs: linear, github]" in ln, ln


def test_allowed_tools_absent_when_runtime_not_declared(tmp_path):
    body = MINIMAL_SKILL.format(name="natools-fixture")
    make_skill(tmp_path, "natools-fixture", body, default_cfg="version: 1\n")
    code, data, _, err = run("natools-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "[ALLOWED TOOLS:" not in data["rendered_skill_body"]
    assert "[ALLOWED MCPs:" not in data["rendered_skill_body"]


ALLOWED_VALUES_SKILL = """\
---
name: {name}
description: fixture skill exercising allowed_values inline echo
---

# Fixture

## Purpose

Test fixture.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| mode | no | string | mode flag | — |
| other | no | string | unrelated | — |

## Steps

1. **First** — set mode to active.
2. **Second** — do unrelated work.
3. **Third** — finalize.

## Output

Nothing.

## Failure modes

- **Anything** — stop.
"""


def test_allowed_values_echoed_on_matching_step_only(tmp_path):
    body = ALLOWED_VALUES_SKILL.format(name="av-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        inputs:
          mode:
            allowed_values: [active, paused, archived]
        """)
    make_skill(tmp_path, "av-fixture", body, default_cfg=cfg)
    code, data, _, err = run("av-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert any("[INPUT mode ∈ active|paused|archived]" in ln for ln in step_lines)
    matching = [ln for ln in step_lines if "[INPUT mode ∈" in ln]
    assert len(matching) == 1, matching
    assert "**First**" in matching[0]
    assert any("heuristic match" in w and "mode" in w and "step 1" in w for w in data["warnings"])


SNIPPET_SKILL = """\
---
name: {name}
description: fixture skill exercising snippet citation hint
---

# Fixture

## Purpose

Test fixture.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| dummy | no | string | placeholder | — |

## Steps

1. **First** — apply audit_rule before proceeding.
2. **Second** — do unrelated work.
3. **Third** — finalize.

## Output

Nothing.

## Failure modes

- **Anything** — stop.
"""


def test_snippet_citation_hint_on_matching_step(tmp_path):
    body = SNIPPET_SKILL.format(name="snip-cite-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        context:
          snippets:
            - name: audit_rule
              text: "Audit applies before any write."
        """)
    make_skill(tmp_path, "snip-cite-fixture", body, default_cfg=cfg)
    code, data, _, err = run("snip-cite-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    matching = [ln for ln in step_lines if "[CITE SNIPPET: audit_rule]" in ln]
    assert len(matching) == 1, matching
    assert "**First**" in matching[0]
    assert any("heuristic match" in w and "audit_rule" in w and "step 1" in w for w in data["warnings"])


def test_step_anchored_gate_inlined_at_step(tmp_path):
    body = MINIMAL_SKILL.format(name="inline-gate-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: post_step_2
              after: "2"
              type: verification
              severity: warn
        """)
    make_skill(tmp_path, "inline-gate-fixture", body, default_cfg=cfg)
    code, data, _, err = run("inline-gate-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "[GATE AFTER STEP 2: post_step_2 (severity: warn)]" in rendered
    assert "## Gates (resolved)" not in rendered
    by_id = {g["id"]: g for g in data["directives"]["gates"]}
    assert by_id["post_step_2"]["severity"] == "warn"


def test_step_anchored_gate_with_step_prefix_inlined(tmp_path):
    body = MINIMAL_SKILL.format(name="inline-gate-prefix-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: post_step_3
              after: step_3
              type: verification
        """)
    make_skill(tmp_path, "inline-gate-prefix-fixture", body, default_cfg=cfg)
    code, data, _, err = run("inline-gate-prefix-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "[GATE AFTER STEP 3: post_step_3 (severity: hard)]" in rendered
    assert "## Gates (resolved)" not in rendered


def test_phase_anchored_gate_stays_in_trailing_block(tmp_path):
    body = MINIMAL_SKILL.format(name="phase-gate-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: phase_check
              after: phase_2
              type: verification
        """)
    make_skill(tmp_path, "phase-gate-fixture", body, default_cfg=cfg)
    code, data, _, err = run("phase-gate-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "[GATE AFTER STEP" not in rendered
    assert "## Gates (resolved)" in rendered
    assert "phase_check" in rendered


def test_step_anchored_gate_falls_back_when_step_skipped(tmp_path):
    body = MINIMAL_SKILL.format(name="orphan-gate-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          "2":
            optional: true
        gates:
          add:
            - id: stranded
              after: "2"
              type: verification
              severity: hard
        """)
    make_skill(tmp_path, "orphan-gate-fixture", body, default_cfg=cfg)
    code, data, _, err = run(
        "orphan-gate-fixture", "--skip-step", "2", skill_root=tmp_path
    )
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "[GATE AFTER STEP" not in rendered
    assert "## Gates (resolved)" in rendered
    assert "stranded" in rendered
    assert any("stranded" in w and "skipped step 2" in w for w in data["warnings"])


def test_required_tool_and_allowed_tools_coexist_on_step(tmp_path):
    body = MINIMAL_SKILL.format(name="combo-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        runtime:
          allowed_tools: [Read, Write]
        steps:
          "2":
            mandate_tool: Read
        """)
    make_skill(tmp_path, "combo-fixture", body, default_cfg=cfg)
    code, data, _, err = run("combo-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    second = [ln for ln in rendered.splitlines() if ln.startswith("2.")][0]
    assert "[REQUIRED TOOL: Read]" in second
    assert "[ALLOWED TOOLS: Read, Write]" in second
    assert second.index("[REQUIRED TOOL:") < second.index("[ALLOWED TOOLS:")


def test_no_setup_section_in_rendered_body(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "## Setup" not in rendered
    assert "Run before any other step" not in rendered


def test_allowed_tools_present_in_each_step(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert step_lines, "expected numbered steps in rendered body"
    for ln in step_lines:
        assert "[ALLOWED TOOLS:" in ln, ln
        assert "Read" in ln and "Write" in ln


# ---------------------------------------------------------------------------
# profiles/ layout: base.yaml routing, defaults, semantic checks
# ---------------------------------------------------------------------------


def test_input_default_rendered_from_base_yaml(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", skill_root=tmp_path)
    assert code == 0, err
    output_path_rows = [
        ln for ln in data["rendered_skill_body"].splitlines()
        if ln.startswith("| output_path ")
    ]
    assert len(output_path_rows) == 1
    parts = output_path_rows[0].split("|")
    assert parts[5].strip() == "./out.md"


def test_input_default_nullified_by_overlay_required(tmp_path):
    make_var_skill(tmp_path)
    code, data, _, err = run("var-skill", "--profile", "user-story", skill_root=tmp_path)
    assert code == 0, err
    output_path_rows = [
        ln for ln in data["rendered_skill_body"].splitlines()
        if ln.startswith("| output_path ")
    ]
    assert len(output_path_rows) == 1
    parts = output_path_rows[0].split("|")
    assert parts[2].strip() == "yes"
    assert parts[5].strip() == "—"


def test_profile_default_routes_to_named_overlay(tmp_path):
    body = MINIMAL_SKILL.format(name="default-route-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        profile:
          default: strict
        vars:
          mode: lax
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        vars:
          mode: strict
        """)
    make_skill(
        tmp_path,
        "default-route-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"strict": overlay_cfg},
    )
    code, data, _, err = run("default-route-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert data["profile_used"] == "strict"
    assert data["profile_requested"] is None
    assert data["config"]["vars"]["mode"] == "strict"


def test_profile_block_rejected_in_overlay(tmp_path):
    body = MINIMAL_SKILL.format(name="overlay-profile-fixture")
    base_cfg = "version: 1\n"
    overlay_cfg = textwrap.dedent("""\
        version: 1
        profile:
          default: nested
        """)
    make_skill(
        tmp_path,
        "overlay-profile-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"other": overlay_cfg},
    )
    code, data, _, err = run(
        "overlay-profile-fixture", "--profile", "other", skill_root=tmp_path
    )
    assert code == 1
    assert "profile" in data["error"]
    assert "only allowed in base.yaml" in data["error"]


def test_explicit_profile_base_skips_overlay(tmp_path):
    body = MINIMAL_SKILL.format(name="explicit-base-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        profile:
          default: alt
        vars:
          who: base-value
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        vars:
          who: alt-value
        """)
    make_skill(
        tmp_path,
        "explicit-base-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"alt": overlay_cfg},
    )
    code, data, _, err = run(
        "explicit-base-fixture", "--profile", "base", skill_root=tmp_path
    )
    assert code == 0, err
    assert data["profile_used"] == "base"
    assert data["profile_requested"] == "base"
    assert data["config"]["vars"]["who"] == "base-value"


def test_missing_base_yaml_exits_2(tmp_path):
    sdir = tmp_path / "no-base-fixture"
    sdir.mkdir()
    (sdir / "MICROSKILL.md").write_text(MINIMAL_SKILL.format(name="no-base-fixture"))
    code, data, _, err = run("no-base-fixture", skill_root=tmp_path)
    assert code == 2, err
    assert "profiles/base.yaml not found" in data["error"]


# ---------------------------------------------------------------------------
# Compliance-bypass regression tests (P0/P1 remediations)
# ---------------------------------------------------------------------------


def test_missing_microskill_md_exits_2(tmp_path):
    sdir = tmp_path / "no-md-fixture"
    sdir.mkdir()
    (sdir / "profiles").mkdir()
    (sdir / "profiles" / "base.yaml").write_text("version: 1\n")
    code, data, _, err = run("no-md-fixture", skill_root=tmp_path)
    assert code == 2, err
    assert "MICROSKILL.md not found" in data["error"]


def test_inputs_default_in_overlay_blocks(tmp_path):
    body = REQUIRED_INPUTS_SKILL.format(name="overlay-default-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        inputs:
          mypath:
            default: ./base.md
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        inputs:
          mypath:
            default: ./overlay.md
        """)
    make_skill(
        tmp_path,
        "overlay-default-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"override": overlay_cfg},
    )
    code, data, _, err = run(
        "overlay-default-fixture", "--profile", "override", skill_root=tmp_path
    )
    assert code == 1, err
    assert "default" in data["error"] and "base-only" in data["error"]


def test_gates_add_id_collides_with_skill_declared_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="gate-collision-fixture").replace(
        "Test fixture.",
        "Test fixture.\n\n<!-- gate-id: existing -->",
    )
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: existing
              after: "1"
              type: verification
        """)
    make_skill(tmp_path, "gate-collision-fixture", body, default_cfg=cfg)
    code, data, _, err = run("gate-collision-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "existing" in data["error"]
    assert "collision" in data["error"]


def test_gates_add_id_duplicated_across_layers_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="gate-dup-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: only_one
              after: "1"
              type: verification
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: only_one
              after: "2"
              type: verification
        """)
    make_skill(
        tmp_path,
        "gate-dup-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"more": overlay_cfg},
    )
    code, data, _, err = run(
        "gate-dup-fixture", "--profile", "more", skill_root=tmp_path
    )
    assert code == 1, err
    assert "only_one" in data["error"]
    assert "duplicated" in data["error"]


def test_hyphenated_var_substitutes(tmp_path):
    body = MINIMAL_SKILL.format(name="hyphenated-var-fixture").replace(
        "Test fixture.",
        "Test fixture. Hello {{my-var}}.",
    )
    cfg = textwrap.dedent("""\
        version: 1
        vars:
          my-var: world
        """)
    make_skill(tmp_path, "hyphenated-var-fixture", body, default_cfg=cfg)
    code, data, _, err = run("hyphenated-var-fixture", skill_root=tmp_path)
    assert code == 0, err
    assert "Hello world." in data["rendered_skill_body"]
    assert "{{my-var}}" not in data["rendered_skill_body"]
    assert data["unresolved_vars"] == []


def test_gate_anchored_to_nonexistent_step_warns(tmp_path):
    body = MINIMAL_SKILL.format(name="gate-nostep-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: stray
              after: "9"
              type: verification
        """)
    make_skill(tmp_path, "gate-nostep-fixture", body, default_cfg=cfg)
    code, data, _, err = run("gate-nostep-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    assert "[GATE AFTER STEP" not in rendered
    assert "## Gates (resolved)" in rendered
    assert "stray" in rendered
    assert any("stray" in w and "step 9" in w and "does not exist" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# P1.0 keyed-list deep_merge verb engine (apply_list_verbs + deep_merge routing)
#
# Unit tests load the resolver as a module and drive the pure helpers directly,
# so verb mechanics are exercised on arbitrary lists without the closed config
# schema interfering. Integration tests at the bottom route verbs through the
# only schema-legal list-of-objects-with-id field (gates.add) via the subprocess.
# ---------------------------------------------------------------------------


def test_apply_list_verbs_patch_by_id_preserves_position():
    mod = load_resolver()
    base = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]
    out = mod.apply_list_verbs(base, {"patch": [{"id": "b", "v": 20, "extra": "x"}]}, path="lst")
    # position of b preserved, fields deep-merged in place
    assert [e["id"] for e in out] == ["a", "b", "c"]
    assert out[1] == {"id": "b", "v": 20, "extra": "x"}
    # original base untouched (new list returned)
    assert base[1] == {"id": "b", "v": 2}


def test_apply_list_verbs_remove_drops_and_preserves_order():
    mod = load_resolver()
    base = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    out = mod.apply_list_verbs(base, {"remove": ["b"]}, path="lst")
    assert [e["id"] for e in out] == ["a", "c"]
    assert base == [{"id": "a"}, {"id": "b"}, {"id": "c"}]


def test_apply_list_verbs_add_appends_at_end():
    mod = load_resolver()
    base = [{"id": "a"}, {"id": "b"}]
    out = mod.apply_list_verbs(base, {"add": [{"id": "z", "v": 9}]}, path="lst")
    assert [e["id"] for e in out] == ["a", "b", "z"]
    assert out[-1] == {"id": "z", "v": 9}


def test_apply_list_verbs_add_id_collision_raises():
    mod = load_resolver()
    base = [{"id": "a"}, {"id": "b"}]
    with pytest.raises(ValueError) as ei:
        mod.apply_list_verbs(base, {"add": [{"id": "a"}]}, path="lst")
    assert "a" in str(ei.value)
    assert "lst" in str(ei.value)


def test_apply_list_verbs_remove_patch_add_combined_in_order():
    mod = load_resolver()
    base = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]
    # remove runs first (drops c), then patch (b in place), then add (append d).
    # 'c' may be re-added by add because remove ran before the add collision check.
    verbs = {
        "remove": ["c"],
        "patch": [{"id": "b", "v": 22}],
        "add": [{"id": "d", "v": 4}, {"id": "c", "v": 30}],
    }
    out = mod.apply_list_verbs(base, verbs, path="lst")
    assert [e["id"] for e in out] == ["a", "b", "d", "c"]
    assert out[1] == {"id": "b", "v": 22}
    assert out[3] == {"id": "c", "v": 30}


def test_apply_list_verbs_missing_patch_id_raises():
    mod = load_resolver()
    base = [{"id": "a"}]
    with pytest.raises(ValueError) as ei:
        mod.apply_list_verbs(base, {"patch": [{"id": "ghost", "v": 1}]}, path="lst")
    assert "ghost" in str(ei.value)
    assert "lst" in str(ei.value)


def test_apply_list_verbs_missing_remove_id_raises():
    mod = load_resolver()
    base = [{"id": "a"}]
    with pytest.raises(ValueError) as ei:
        mod.apply_list_verbs(base, {"remove": ["ghost"]}, path="lst")
    assert "ghost" in str(ei.value)
    assert "lst" in str(ei.value)


def test_deep_merge_routes_verb_dict_over_list():
    mod = load_resolver()
    base = {"lst": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    overlay = {"lst": {"patch": [{"id": "a", "v": 11}], "add": [{"id": "c"}]}}
    out = mod.deep_merge(base, overlay)
    assert [e["id"] for e in out["lst"]] == ["a", "b", "c"]
    assert out["lst"][0] == {"id": "a", "v": 11}


def test_deep_merge_mixed_key_dict_over_list_raises():
    mod = load_resolver()
    base = {"lst": [{"id": "a"}]}
    # a dict with a non-verb key mixed in cannot merge onto a list
    overlay = {"lst": {"add": [{"id": "b"}], "bogus": 1}}
    with pytest.raises(ValueError) as ei:
        mod.deep_merge(base, overlay)
    assert "lst" in str(ei.value)


def test_deep_merge_empty_dict_over_list_keeps_existing_behavior():
    mod = load_resolver()
    base = {"lst": [{"id": "a"}]}
    # empty dict is NOT a verb dict (non-empty subset required) -> falls through to
    # the existing wholesale-replace branch (dict replaces the list)
    out = mod.deep_merge(base, {"lst": {}})
    assert out["lst"] == {}


def test_deep_merge_plain_list_over_list_still_replaces():
    mod = load_resolver()
    base = {"runtime": {"allowed_tools": ["Read", "Write"]}}
    overlay = {"runtime": {"allowed_tools": ["Glob"]}}
    out = mod.deep_merge(base, overlay)
    assert out["runtime"]["allowed_tools"] == ["Glob"]


def test_deep_merge_gates_add_literal_append_untouched():
    mod = load_resolver()
    base = {"gates": {"add": [{"id": "gate_one"}]}}
    overlay = {"gates": {"add": [{"id": "gate_two"}]}}
    out = mod.deep_merge(base, overlay)
    # list-onto-list at gates.add still appends (regression — NOT routed to verbs)
    assert [g["id"] for g in out["gates"]["add"]] == ["gate_one", "gate_two"]


def test_deep_merge_remove_verb_and_null_delete_coexist():
    mod = load_resolver()
    base = {"lst": [{"id": "a"}, {"id": "b"}], "doomed": "x", "keep": 1}
    # 'remove' verb drops the list entry 'a'; null over 'doomed' pops the key.
    # The two mechanisms must not collide.
    overlay = {"lst": {"remove": ["a"]}, "doomed": None}
    out = mod.deep_merge(base, overlay)
    assert [e["id"] for e in out["lst"]] == ["b"]
    assert "doomed" not in out
    assert out["keep"] == 1


# --- Integration: verbs through the real resolver via gates.add (schema-legal) ---


def test_resolver_verb_patch_through_gates_add(tmp_path):
    body = MINIMAL_SKILL.format(name="verb-patch-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: gate_alpha
              after: "1"
              type: verification
              severity: hard
            - id: gate_beta
              after: "2"
              type: verification
              severity: hard
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            patch:
              - id: gate_alpha
                severity: warn
        """)
    make_skill(
        tmp_path,
        "verb-patch-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"soften": overlay_cfg},
    )
    code, data, _, err = run("verb-patch-fixture", "--profile", "soften", skill_root=tmp_path)
    assert code == 0, err
    add = data["config"]["gates"]["add"]
    # order preserved, alpha patched in place, beta untouched
    assert [g["id"] for g in add] == ["gate_alpha", "gate_beta"]
    assert add[0]["severity"] == "warn"
    assert add[1]["severity"] == "hard"


def test_resolver_verb_remove_and_add_through_gates_add(tmp_path):
    body = MINIMAL_SKILL.format(name="verb-rm-add-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: gate_alpha
              after: "1"
              type: verification
            - id: gate_beta
              after: "2"
              type: verification
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            remove: [gate_alpha]
            add:
              - id: gate_gamma
                after: "2"
                type: verification
        """)
    make_skill(
        tmp_path,
        "verb-rm-add-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"reshape": overlay_cfg},
    )
    code, data, _, err = run("verb-rm-add-fixture", "--profile", "reshape", skill_root=tmp_path)
    assert code == 0, err
    add = data["config"]["gates"]["add"]
    assert [g["id"] for g in add] == ["gate_beta", "gate_gamma"]


def test_resolver_verb_missing_remove_id_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="verb-bad-rm-fixture")
    base_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            - id: gate_alpha
              after: "1"
              type: verification
        """)
    overlay_cfg = textwrap.dedent("""\
        version: 1
        gates:
          add:
            remove: [no_such_gate]
        """)
    make_skill(
        tmp_path,
        "verb-bad-rm-fixture",
        body,
        default_cfg=base_cfg,
        profiles={"reshape": overlay_cfg},
    )
    code, data, _, err = run("verb-bad-rm-fixture", "--profile", "reshape", skill_root=tmp_path)
    assert code == 1, err
    assert "no_such_gate" in data["error"]


# ---------------------------------------------------------------------------
# P1.1 microskill steps.add / steps.patch / steps.remove
#
# A microskill PROFILE may add / patch / remove markdown "## Steps" entries,
# keyed by each step's ORIGINAL number, after which the surviving + added steps
# are renumbered contiguously. Distinct from the deep_merge list-verb engine:
# steps are markdown numbered lines, not a YAML list.
#
# Shape (consistent with the keyed-by-number steps map):
#   steps:
#     remove: [<orig_n>, ...]            # elide steps, keyed by original number
#     patch:  { "<orig_n>": {text: ...}} # replace a step's text, position kept
#     add:    [{after: <orig_n|0>, text: ...}, ...]  # 0 prepends, N inserts after orig N
# Apply order: REMOVE -> PATCH -> ADD -> renumber.
# ---------------------------------------------------------------------------


def test_steps_patch_replaces_text_and_preserves_position(tmp_path):
    body = MINIMAL_SKILL.format(name="patch-step-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          patch:
            "2":
              text: "**Replaced** — the new second step."
        """)
    make_skill(tmp_path, "patch-step-fixture", body, default_cfg=cfg)
    code, data, _, err = run("patch-step-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert step_lines[0].startswith("1. **First**")
    assert step_lines[1] == "2. **Replaced** — the new second step."
    assert step_lines[2].startswith("3. **Third**")
    assert "**Second**" not in rendered
    assert len(step_lines) == 3


def test_steps_remove_elides_and_renumbers(tmp_path):
    body = MINIMAL_SKILL.format(name="remove-step-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          remove: [2]
        """)
    make_skill(tmp_path, "remove-step-fixture", body, default_cfg=cfg)
    code, data, _, err = run("remove-step-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 2
    assert step_lines[0].startswith("1. **First**")
    assert step_lines[1].startswith("2. **Third**")
    assert "**Second**" not in rendered


def test_steps_add_after_number_inserts_and_renumbers(tmp_path):
    body = MINIMAL_SKILL.format(name="add-step-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          add:
            - after: 1
              text: "**Inserted** — brand new step."
        """)
    make_skill(tmp_path, "add-step-fixture", body, default_cfg=cfg)
    code, data, _, err = run("add-step-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 4
    assert step_lines[0].startswith("1. **First**")
    assert step_lines[1] == "2. **Inserted** — brand new step."
    assert step_lines[2].startswith("3. **Second**")
    assert step_lines[3].startswith("4. **Third**")


def test_steps_add_after_zero_prepends(tmp_path):
    body = MINIMAL_SKILL.format(name="prepend-step-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          add:
            - after: 0
              text: "**Zeroth** — runs before everything."
        """)
    make_skill(tmp_path, "prepend-step-fixture", body, default_cfg=cfg)
    code, data, _, err = run("prepend-step-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 4
    assert step_lines[0] == "1. **Zeroth** — runs before everything."
    assert step_lines[1].startswith("2. **First**")


def test_steps_gate_anchored_after_removed_step_falls_back(tmp_path):
    # MUST-FIX #3: a gate anchored after a REMOVED step must hit the same
    # trailing 'Gates (resolved)' fallback as a skipped step — never silently drop.
    body = MINIMAL_SKILL.format(name="remove-gate-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          remove: [2]
        gates:
          add:
            - id: orphaned
              after: "2"
              type: verification
              severity: hard
        """)
    make_skill(tmp_path, "remove-gate-fixture", body, default_cfg=cfg)
    code, data, _, err = run("remove-gate-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    # the step is gone, the gate is NOT inlined, but it survives in the trailing block
    assert "**Second**" not in rendered
    assert "[GATE AFTER STEP" not in rendered
    assert "## Gates (resolved)" in rendered
    assert "orphaned" in rendered
    assert any(
        "orphaned" in w and "2" in w and "Gates (resolved)" in w
        for w in data["warnings"]
    )


def test_steps_add_branching_language_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="branch-add-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          add:
            - after: 1
              text: "**Branchy** — if the file exists then read it otherwise skip."
        """)
    make_skill(tmp_path, "branch-add-fixture", body, default_cfg=cfg)
    code, data, _, err = run("branch-add-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "error" in data
    assert "branch" in data["error"].lower()


def test_steps_patch_branching_language_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="branch-patch-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          patch:
            "2":
              text: "**Looped** — for each item in the list, process it."
        """)
    make_skill(tmp_path, "branch-patch-fixture", body, default_cfg=cfg)
    code, data, _, err = run("branch-patch-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "error" in data
    assert "branch" in data["error"].lower()


def test_steps_over_ten_merged_warns_not_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="overten-fixture")
    # start with 3 steps, add 9 more -> 12 merged steps
    adds = "\n".join(
        f"    - after: 3\n      text: \"**Extra{i}** — additional step number {i}.\""
        for i in range(9)
    )
    cfg = "version: 1\nsteps:\n  add:\n" + adds + "\n"
    make_skill(tmp_path, "overten-fixture", body, default_cfg=cfg)
    code, data, _, err = run("overten-fixture", skill_root=tmp_path)
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    assert len(step_lines) == 12
    assert any("12" in w and "atomic" in w for w in data["warnings"])


def test_steps_patch_missing_number_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="patch-ghost-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          patch:
            "9":
              text: "**Nope** — there is no step nine."
        """)
    make_skill(tmp_path, "patch-ghost-fixture", body, default_cfg=cfg)
    code, data, _, err = run("patch-ghost-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "error" in data
    assert "9" in data["error"]


def test_steps_remove_missing_number_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="remove-ghost-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          remove: [9]
        """)
    make_skill(tmp_path, "remove-ghost-fixture", body, default_cfg=cfg)
    code, data, _, err = run("remove-ghost-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "error" in data
    assert "9" in data["error"]


def test_steps_add_after_missing_anchor_blocks(tmp_path):
    body = MINIMAL_SKILL.format(name="add-ghost-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          add:
            - after: 9
              text: "**Nope** — anchored to a missing step."
        """)
    make_skill(tmp_path, "add-ghost-fixture", body, default_cfg=cfg)
    code, data, _, err = run("add-ghost-fixture", skill_root=tmp_path)
    assert code == 1, err
    assert "error" in data
    assert "9" in data["error"]


def test_steps_directives_preserve_optional_mandate_skip(tmp_path):
    # REGRESSION: the legacy optional / mandate_tool / --skip-step path is unchanged
    # even when keyed-by-number step config coexists with the new verb keys.
    body = MINIMAL_SKILL.format(name="legacy-coexist-fixture")
    cfg = textwrap.dedent("""\
        version: 1
        steps:
          "1":
            mandate_tool: Read
          "3":
            optional: true
          patch:
            "2":
              text: "**Patched Second** — replaced body."
        """)
    make_skill(tmp_path, "legacy-coexist-fixture", body, default_cfg=cfg)
    code, data, _, err = run(
        "legacy-coexist-fixture", "--skip-step", "3", skill_root=tmp_path
    )
    assert code == 0, err
    rendered = data["rendered_skill_body"]
    step_lines = [ln for ln in rendered.splitlines() if re.match(r"^\d+\.", ln)]
    # step 3 skipped, step 2 patched, step 1 mandate-tagged
    assert len(step_lines) == 2
    assert "[REQUIRED TOOL: Read]" in step_lines[0]
    assert step_lines[1].endswith("**Patched Second** — replaced body.")
    assert "**Third**" not in rendered
    assert data["directives"]["mandated_tools"] == {"1": "Read"}


def test_shared_atomicity_module_vocabulary_matches_validate(tmp_path):
    # The branching-language vocabulary must be the SAME object used by
    # validate-microskill — loaded from the shared module, not duplicated.
    import importlib.machinery as _m
    import importlib.util as _u

    shared_path = REPO / "catalog" / "scripts" / "microskill_steps.py"
    loader = _m.SourceFileLoader("microskill_steps", str(shared_path))
    spec = _u.spec_from_loader("microskill_steps", loader)
    mod = _u.module_from_spec(spec)
    loader.exec_module(mod)
    # branching language is detected; plain linear prose is not
    assert mod.BRANCH_RE.search("if the file exists then read it")
    assert not mod.BRANCH_RE.search("read the supplied text and write the document")
    # step-line counter matches a simple numbered block
    assert mod.STEP_RE.search("1. do thing")
