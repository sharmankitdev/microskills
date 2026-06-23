"""
Tests for the create-domain review CONTENT (§8 step 4): the 14 microskill + 7
workflow `review-dimension` rubric overlays, and the 2 `verify-finding`
adversarial-verify profiles (microskill-draft / workflow-draft). (The 2
`collect-findings` fan-in registration profiles it formerly also covered were
deleted — verify reads the review panel directly via the panel-aggregate ref.)

These profiles re-aim the existing review-dimension / verify-finding generics at a
microskill draft bundle and a workflow draft bundle instead of a code diff. They
are pure additive YAML overlays — no body, schema, or script change.

The unit tests:
  * resolve each review-dimension / verify-finding profile through the same
    resolve-microskill subprocess the dispatcher uses, asserting a clean resolve
    and FULL {{dimension}}/{{artifact_kind}} substitution (no leftover tokens);
  * assert the three-way naming invariant on the raw profile bytes — the profile
    FILENAME == vars.dimension == the single context.snippets entry named
    <dimension>-rubric (which is also the generated expand fan-out node suffix with
    '-' -> '_', so the panel-aggregate ref ${review[].findings} resolves);
  * assert NO forbidden inputs / runtime / output_schema key in any overlay (they
    all inherit from base; redeclaring output_schema would silently drop the
    {dimension, findings} contract the collect/verify/synthesize chain joins on);
  * assert the load-bearing artifact_kind strings are exact AND consistent across
    the review-dimension panel and its paired verify-finding profile.

The integration tests build a hermetic fan-out -> for_each verify WORKFLOW.yaml in
tmp_path (the same wiring §8 step 7 splices: verify fans out over the review panel
via ${review[].findings}, no collect node) and assert it validates + compiles
against the REAL catalog microskills — proving every named profile resolves and the
panel is joinable end to end.

Run: python3 -m pytest catalog/scripts/tests/test_create_domain_review_profiles.py -v
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
MS_ROOT = REPO / "catalog" / "microskills"
RD = MS_ROOT / "review-dimension"
VF = MS_ROOT / "verify-finding"
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
VALIDATE_WF = REPO / "catalog" / "scripts" / "validate-workflow"
COMPILE_WF = REPO / "catalog" / "scripts" / "compile-workflow"
# Pin the canonical committed templates/ (config-schema.json + workflow-schema.json)
# so these hermetic tests validate against the source of truth, never a stale,
# possibly-divergent generated .claude/ mirror. Mirrors the sibling test files.
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

# --- The locked dimension-name inventory (plan §"Dimension name inventory") ---
# Each name is, four ways at once: the review-dimension profile FILENAME, the
# vars.dimension value, the context.snippets[0].name prefix, and (with '-'->'_')
# the collect-findings input key + the generated expand fan-out node suffix.
MS_DIMS = [
    "ms-atomicity-single-purpose",
    "ms-single-linear-path-semantic",
    "ms-step-atomicity-linearity",
    "ms-inputs-contract-adequacy",
    "ms-output-contract-appropriate",
    "ms-failure-modes-coverage",
    "ms-output-section-vs-schema-fields",
    "ms-step-input-reference-undeclared",
    "ms-declared-input-unused",
    "ms-purpose-contract-fidelity",
    "ms-description-trigger-quality",
    "shape-not-faithfulness-fabrication-passes",
    "additionalprops-and-empty-content-permissive",
    "unanalyzable-fork-guard-no-signal",
]
WF_DIMS = [
    "wf-dag-decomposition-correctness",
    "wf-guard-logic-intent",
    "wf-prompt-task-fidelity",
    "wf-gate-prompt-options-quality",
    "wf-profile-overlay-coherence",
    "wf-spill-materialize-judgment",
    "wf-output-schema-downstream-fit",
]

# The load-bearing artifact_kind strings (plan inventory). Every ms review-dimension
# profile AND the verify-finding microskill-draft profile must carry the FIRST; every
# wf review-dimension profile AND verify-finding workflow-draft the SECOND. A drift here
# silently desynchronizes the review panel's framing from the adversarial-verify seat's.
MS_ARTIFACT_KIND = "microskill draft bundle (MICROSKILL.md + profiles/base.yaml, concatenated)"
WF_ARTIFACT_KIND = "workflow draft bundle (WORKFLOW.yaml + profiles/*.yaml, concatenated)"

FORBIDDEN_KEYS = {"inputs", "runtime", "output_schema"}


def _us(names):
    """Dimension names with '-' -> '_' — the inputs_each / collect-key form."""
    return {n.replace("-", "_") for n in names}


def _resolve(skill, profile):
    """Resolve <skill> under <profile> via the resolve-microskill subprocess (the
    exact path the dispatcher uses), pinning --skill-root at the real catalog so the
    base.yaml + the overlay both merge. Returns (returncode, parsed-json, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(RESOLVE), skill, "--profile", profile,
         "--skill-root", str(MS_ROOT)],
        capture_output=True, text=True, env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def _raw(path):
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Sanity: the inventory lists are exactly the locked sizes (catches an
# accidental list edit that would silently shrink the panel coverage).
# ---------------------------------------------------------------------------


def test_inventory_sizes():
    assert len(MS_DIMS) == 14 and len(set(MS_DIMS)) == 14
    assert len(WF_DIMS) == 7 and len(set(WF_DIMS)) == 7


# ---------------------------------------------------------------------------
# Task 1 / 2 unit tests: review-dimension rubric overlays resolve + substitute,
# and the naming invariant + no-forbidden-keys hold on the raw bytes.
# ---------------------------------------------------------------------------


def _assert_dim_resolves(name, artifact_kind):
    rc, data, err = _resolve("review-dimension", name)
    assert rc == 0, f"{name}: {err}"
    body = data["rendered_skill_body"]
    ctx = data.get("context_block", "") or ""
    # No leftover template tokens anywhere a var is substituted.
    assert "{{dimension}}" not in body and "{{artifact_kind}}" not in body, name
    assert "{{dimension}}" not in ctx and "{{artifact_kind}}" not in ctx, name
    assert data["unresolved_vars"] == [], (name, data["unresolved_vars"])
    # The dimension name + the (substituted) artifact_kind value reach the body.
    assert name in body, name
    assert artifact_kind in body, name


def _assert_dim_naming_and_keys(root, name, artifact_kind):
    doc = _raw(root / "profiles" / f"{name}.yaml")
    assert doc["vars"]["dimension"] == name, name
    assert doc["vars"]["artifact_kind"] == artifact_kind, name
    snips = doc["context"]["snippets"]
    assert len(snips) == 1, name
    assert snips[0]["name"] == f"{name}-rubric", name
    assert not (set(doc.keys()) & FORBIDDEN_KEYS), (name, doc.keys())


def test_ms_profiles_resolve_and_substitute():
    for name in MS_DIMS:
        _assert_dim_resolves(name, MS_ARTIFACT_KIND)


def test_ms_profiles_no_forbidden_keys_and_naming():
    for name in MS_DIMS:
        _assert_dim_naming_and_keys(RD, name, MS_ARTIFACT_KIND)


def test_wf_profiles_resolve_and_substitute():
    for name in WF_DIMS:
        _assert_dim_resolves(name, WF_ARTIFACT_KIND)


def test_wf_profiles_no_forbidden_keys_and_naming():
    for name in WF_DIMS:
        _assert_dim_naming_and_keys(RD, name, WF_ARTIFACT_KIND)


# ---------------------------------------------------------------------------
# Task 3 unit tests: verify-finding create profiles (artifact_kind swap only).
# (The collect-findings registration profiles were deleted — verify reads the
# review panel directly via the panel-aggregate ref ${review[].findings}.)
# ---------------------------------------------------------------------------


def _assert_verify_profile(profile, artifact_kind):
    doc = _raw(VF / "profiles" / f"{profile}.yaml")
    # Var-only overlay: it may only carry version + vars (inputs / runtime /
    # output_schema all inherit from base).
    assert set(doc.keys()) <= {"version", "vars"}, (profile, doc.keys())
    assert set(doc["vars"].keys()) == {"artifact_kind"}, (profile, doc["vars"])
    assert doc["vars"]["artifact_kind"] == artifact_kind, profile
    rc, data, err = _resolve("verify-finding", profile)
    assert rc == 0, f"{profile}: {err}"
    body = data["rendered_skill_body"]
    assert "{{artifact_kind}}" not in body, profile
    assert artifact_kind in body, profile
    assert data["unresolved_vars"] == [], (profile, data["unresolved_vars"])


def test_verify_microskill_draft_profile():
    _assert_verify_profile("microskill-draft", MS_ARTIFACT_KIND)


def test_verify_workflow_draft_profile():
    _assert_verify_profile("workflow-draft", WF_ARTIFACT_KIND)


# ---------------------------------------------------------------------------
# Task 4 integration: a hermetic fan-out -> verify panel validates + compiles
# against the REAL catalog microskills. This is the three-way naming invariant's
# live guard — the per-item customize.profile "{{each.item}}" forces every
# review-dimension profile named in expand.over to resolve, and the panel-aggregate
# ref ${review[].findings} fans verify out over the generated siblings directly
# (no collect node), so the verify create profiles must exist and resolve.
# ---------------------------------------------------------------------------


def _panel_yaml(dims, verify_profile, artifact_label):
    """A 3-node panel: a stub agent producer emitting a bundle_path; a
    review-dimension expand TEMPLATE fanned over `dims` with a per-item
    customize.profile; a verify-finding for_each over the review panel via the
    panel-aggregate ref ${review[].findings} (no collect fan-in node). Built as
    explicit lines (no f-string brace escaping of the YAML inline maps / ${refs} /
    {{each.item}} token)."""
    lines = [
        "version: 1",
        "name: panel-flow",
        "description: hermetic create-domain review panel (fan-out -> verify over panel)",
        "nodes:",
        "  - id: producer",
        "    agent: stub-producer",
        f"    prompt: emit the {artifact_label} as a concatenated bundle and return its path",
        "    output_schema:",
        "      type: object",
        "      required: [bundle_path]",
        "      properties:",
        "        bundle_path: { type: string }",
        "  - id: review",
        "    use: review-dimension",
        '    customize: { profile: "{{each.item}}" }',
        "    expand:",
        "      over:",
    ]
    for d in dims:
        lines.append(f'        - {{ item: {d}, name: "{d}" }}')
    lines += [
        "    inputs:",
        "      artifact_path: ${producer.output.bundle_path}",
        "  - id: verify",
        "    use: verify-finding",
        f"    customize: {{ profile: {verify_profile} }}",
        "    for_each: ${review[].findings}",
        "    as: finding",
        "    max_parallel: 4",
        "    inputs:",
        "      finding: ${finding}",
        "      artifact_path: ${producer.output.bundle_path}",
    ]
    return "\n".join(lines) + "\n"


def _build_world(tmp_path, panel_yaml):
    """A hermetic compile world: <tmp>/workflow-defs/panel-flow holds the panel;
    <tmp>/microskills is a symlink to the REAL catalog so the use: targets resolve
    via compile's skill-root == <defs-root>.parent/microskills idiom."""
    defs_root = tmp_path / "workflow-defs"
    (tmp_path / "microskills").symlink_to(MS_ROOT)
    d = defs_root / "panel-flow"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(panel_yaml)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    return defs_root, d


def _validate_wf(wf_path):
    # --defs-root catalog/microskills makes validate's skill-root collapse back to
    # catalog/microskills (skill_root == defs_root.parent/microskills), so every
    # use: target resolves against the real registry.
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_WF), str(wf_path),
         "--defs-root", str(MS_ROOT)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def _compile_wf(defs_root, name):
    proc = subprocess.run(
        [sys.executable, str(COMPILE_WF), name, "--defs-root", str(defs_root)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def _assert_panel_wires(tmp_path, dims, verify_profile, label):
    panel = _panel_yaml(dims, verify_profile, label)
    defs_root, d = _build_world(tmp_path, panel)
    wf_path = d / "WORKFLOW.yaml"

    rc, data, err = _validate_wf(wf_path)
    blocks = [i for i in (data["issues"] if data else []) if i["severity"] == "block"]
    assert rc == 0 and data and data["pass"] is True, (blocks, err)

    rc, _, out, err = _compile_wf(defs_root, "panel-flow")
    assert rc == 0, out + err


def test_ms_panel_wires_endtoend(tmp_path):
    _assert_panel_wires(tmp_path, MS_DIMS, "microskill-draft", "microskill draft")


def test_wf_panel_wires_endtoend(tmp_path):
    _assert_panel_wires(tmp_path, WF_DIMS, "workflow-draft", "workflow draft")
