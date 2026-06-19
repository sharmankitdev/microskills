"""
Tests for the cross-artifact review CONTENT (§8 step 5 / spec §11 D2): the 3
review-dimension cross-artifact rubric overlays (duplicate-capability,
naming-collision, reverse-consumer), the verify-finding cross-artifact profile,
and the collect-findings cross-create fan-in profile. These re-aim the generics at
a component-draft-vs-catalog comparison grounded by catalog-index.json. Pure
additive YAML overlays — no body / schema / script change.

Run: python3 -m pytest catalog/scripts/tests/test_cross_artifact_review_profiles.py -v
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
CF = MS_ROOT / "collect-findings"
VF = MS_ROOT / "verify-finding"
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
VALIDATE_WF = REPO / "catalog" / "scripts" / "validate-workflow"
COMPILE_WF = REPO / "catalog" / "scripts" / "compile-workflow"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}

CROSS_DIMS = ["duplicate-capability", "naming-collision", "reverse-consumer"]
CROSS_REVIEW_ARTIFACT_KIND = "component draft bundle under catalog review (a microskill or workflow draft)"
CROSS_VERIFY_ARTIFACT_KIND = "component draft bundle followed by the catalog index (catalog-index.json), concatenated"
FORBIDDEN_KEYS = {"inputs", "runtime", "output_schema"}


def _us(names):
    return {n.replace("-", "_") for n in names}


def _raw(path):
    return yaml.safe_load(path.read_text())


def _resolve(skill, profile):
    proc = subprocess.run(
        [sys.executable, str(RESOLVE), skill, "--profile", profile, "--skill-root", str(MS_ROOT)],
        capture_output=True, text=True, env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, data, proc.stderr


def test_inventory_size():
    assert len(CROSS_DIMS) == 3 and len(set(CROSS_DIMS)) == 3


def test_cross_dims_resolve_and_substitute():
    for name in CROSS_DIMS:
        rc, data, err = _resolve("review-dimension", name)
        assert rc == 0, f"{name}: {err}"
        body, ctx = data["rendered_skill_body"], (data.get("context_block", "") or "")
        assert "{{dimension}}" not in body and "{{artifact_kind}}" not in body, name
        assert "{{dimension}}" not in ctx and "{{artifact_kind}}" not in ctx, name
        assert data["unresolved_vars"] == [], (name, data["unresolved_vars"])
        assert name in body and CROSS_REVIEW_ARTIFACT_KIND in body, name
        # every cross-artifact rubric must name the index grounding slot
        assert "context_path" in ctx, name


def test_cross_dims_naming_invariant_and_no_forbidden_keys():
    for name in CROSS_DIMS:
        doc = _raw(RD / "profiles" / f"{name}.yaml")
        assert doc["vars"]["dimension"] == name, name
        assert doc["vars"]["artifact_kind"] == CROSS_REVIEW_ARTIFACT_KIND, name
        snips = doc["context"]["snippets"]
        assert len(snips) == 1 and snips[0]["name"] == f"{name}-rubric", name
        assert not (set(doc.keys()) & FORBIDDEN_KEYS), (name, doc.keys())


def test_collect_cross_create_keys():
    doc = _raw(CF / "profiles" / "cross-create.yaml")
    assert set(doc["inputs"]) == _us(CROSS_DIMS)
    assert "output_schema" not in doc and "runtime" not in doc
    rc, _, err = _resolve("collect-findings", "cross-create")
    assert rc == 0, err


def test_verify_cross_artifact_profile():
    doc = _raw(VF / "profiles" / "cross-artifact.yaml")
    assert set(doc.keys()) <= {"version", "vars"}, doc.keys()
    assert set(doc["vars"].keys()) == {"artifact_kind"}
    assert doc["vars"]["artifact_kind"] == CROSS_VERIFY_ARTIFACT_KIND
    rc, data, err = _resolve("verify-finding", "cross-artifact")
    assert rc == 0, err
    assert "{{artifact_kind}}" not in data["rendered_skill_body"]
    assert CROSS_VERIFY_ARTIFACT_KIND in data["rendered_skill_body"]
    assert data["unresolved_vars"] == []


# --- Integration: a dedicated cross-artifact panel validates + compiles against
#     the REAL catalog (the four-way naming invariant's live guard). ---

def _panel_yaml(dims, collect_profile, verify_profile):
    lines = [
        "version: 1",
        "name: cross-panel",
        "description: hermetic cross-artifact review panel",
        "nodes:",
        "  - id: producer",
        "    agent: stub-producer",
        "    prompt: emit the draft bundle and the catalog index and return their paths",
        "    output_schema:",
        "      type: object",
        "      required: [bundle_path, index_path]",
        "      properties:",
        "        bundle_path: { type: string }",
        "        index_path: { type: string }",
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
        "      context_path: ${producer.output.index_path}",
        "  - id: collect",
        "    use: collect-findings",
        f"    customize: {{ profile: {collect_profile} }}",
        "    inputs_each: review",
        "  - id: verify",
        "    use: verify-finding",
        f"    customize: {{ profile: {verify_profile} }}",
        "    for_each: ${collect.output.findings}",
        "    as: finding",
        "    max_parallel: 4",
        "    inputs:",
        "      finding: ${finding}",
        "      artifact_path: ${producer.output.bundle_path}",
    ]
    return "\n".join(lines) + "\n"


def _build_world(tmp_path, panel_yaml):
    defs_root = tmp_path / "workflow-defs"
    (tmp_path / "microskills").symlink_to(MS_ROOT)
    d = defs_root / "cross-panel"
    (d / "profiles").mkdir(parents=True)
    (d / "WORKFLOW.yaml").write_text(panel_yaml)
    (d / "profiles" / "base.yaml").write_text("version: 1\n")
    return defs_root, d


def test_cross_panel_wires_endtoend(tmp_path):
    panel = _panel_yaml(CROSS_DIMS, "cross-create", "cross-artifact")
    defs_root, d = _build_world(tmp_path, panel)
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_WF), str(d / "WORKFLOW.yaml"), "--defs-root", str(MS_ROOT)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    data = json.loads(proc.stdout) if proc.stdout.strip() else None
    blocks = [i for i in (data["issues"] if data else []) if i["severity"] == "block"]
    assert proc.returncode == 0 and data and data["pass"] is True, (blocks, proc.stderr)
    proc = subprocess.run(
        [sys.executable, str(COMPILE_WF), "cross-panel", "--defs-root", str(defs_root)],
        capture_output=True, text=True, cwd=str(REPO), env=_ENV)
    assert proc.returncode == 0, proc.stdout + proc.stderr
