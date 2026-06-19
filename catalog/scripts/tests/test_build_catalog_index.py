"""
Tests for build-catalog-index (§8 step 5): the deterministic catalog enumerator
that backs the cross-artifact review dimensions. Hermetic tmp_path worlds; assert
on the returned JSON AND the written catalog-index.json bytes.

Run: python3 -m pytest catalog/scripts/tests/test_build_catalog_index.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build-catalog-index"


def _md(p: Path, name: str, desc: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n", encoding="utf-8")


def _yaml(p: Path, body: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _world(tmp_path: Path) -> Path:
    """A minimal catalog: 1 microskill, 1 agent, 1 subgraph, and a workflow that
    `use:`s the microskill (so a consumer edge exists)."""
    cr = tmp_path / "catalog"
    _md(cr / "microskills" / "do-thing" / "MICROSKILL.md", "do-thing", "Do the thing and return a result.")
    _md(cr / "agents" / "thing-agent" / "AGENT.md", "thing-agent", "An agent that things.")
    _yaml(cr / "workflow-defs" / "_subgraphs" / "rv" / "SUBGRAPH.yaml",
          "name: rv\ndescription: review then verify\nnodes:\n  - id: a\n    agent: x\n    prompt: hi\n")
    _yaml(cr / "workflow-defs" / "thing-flow" / "WORKFLOW.yaml",
          "name: thing-flow\ndescription: runs do-thing\nnodes:\n"
          "  - id: t\n    use: do-thing\n    inputs:\n      x: 1\n")
    # a non-WORKFLOW dir (e.g. _snippets) must be skipped without crashing
    (cr / "workflow-defs" / "_snippets").mkdir(parents=True)
    (cr / "workflow-defs" / "_snippets" / "note.md").write_text("just a snippet\n", encoding="utf-8")
    return cr


def _run(cr, out, harness=None):
    cmd = [sys.executable, str(SCRIPT), "--catalog-root", str(cr), "--out", str(out)]
    if harness:
        cmd += ["--harness", str(harness)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stderr


def test_enumerates_kinds_names_purposes(tmp_path):
    cr = _world(tmp_path)
    out = tmp_path / "catalog-index.json"
    rc, summary, err = _run(cr, out)
    assert rc == 0, err
    idx = json.loads(out.read_text())
    by = {(e["kind"], e["name"]): e for e in idx}
    assert ("microskill", "do-thing") in by
    assert ("agent", "thing-agent") in by
    assert ("subgraph", "rv") in by
    assert ("workflow", "thing-flow") in by
    assert by[("microskill", "do-thing")]["purpose"] == "Do the thing and return a result."
    assert summary["component_count"] == 4


def test_consumer_cross_reference(tmp_path):
    cr = _world(tmp_path)
    out = tmp_path / "catalog-index.json"
    rc, summary, _ = _run(cr, out)
    idx = {e["name"]: e for e in json.loads(out.read_text())}
    assert idx["do-thing"]["consumers"] == ["thing-flow"]
    assert summary["consumer_map_count"] == 1


def test_byte_deterministic(tmp_path):
    cr = _world(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _run(cr, a)
    _run(cr, b)
    assert a.read_bytes() == b.read_bytes()
    # entries sorted by (kind, name); consumers sorted
    idx = json.loads(a.read_text())
    assert idx == sorted(idx, key=lambda e: (e["kind"], e["name"], e["line"]))


def test_purpose_collapses_multiline(tmp_path):
    cr = tmp_path / "catalog"
    p = cr / "microskills" / "wrappy" / "MICROSKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nname: wrappy\ndescription: >\n  line one\n  line two\n---\n", encoding="utf-8")
    out = tmp_path / "i.json"
    _run(cr, out)
    idx = {e["name"]: e for e in json.loads(out.read_text())}
    assert idx["wrappy"]["purpose"] == "line one line two"


def test_harness_custom_component_included(tmp_path):
    cr = _world(tmp_path)
    hroot = tmp_path / "harness"
    _md(hroot / "microskills" / "custom-skill" / "MICROSKILL.md", "custom-skill", "A vendored custom skill.")
    harness = hroot / "harness.yaml"
    harness.write_text(
        "version: 2\nmicroskills:\n  - name: custom-skill\n    source: custom\n", encoding="utf-8")
    out = tmp_path / "i.json"
    rc, summary, err = _run(cr, out, harness=harness)
    assert rc == 0, err
    names = {e["name"] for e in json.loads(out.read_text())}
    assert "custom-skill" in names
    assert summary["component_count"] == 5


def test_missing_catalog_root_exits_2(tmp_path):
    rc, _, err = _run(tmp_path / "nope", tmp_path / "i.json")
    assert rc == 2
    assert "not a directory" in err


def test_duplicate_name_collision_faithful(tmp_path):
    """The script's central guarantee: duplicate names are NEVER deduped (a flat
    list, not name-keyed) — the grounding for naming-collision/reverse-consumer.
    Two components of different kinds share one name, and a workflow uses that
    name: BOTH entries must survive and the consumer edge must fan to BOTH."""
    cr = tmp_path / "catalog"
    _md(cr / "microskills" / "shared" / "MICROSKILL.md", "shared", "a microskill named shared")
    _yaml(cr / "workflow-defs" / "_subgraphs" / "shared" / "SUBGRAPH.yaml",
          "name: shared\ndescription: a subgraph also named shared\n"
          "nodes:\n  - id: a\n    agent: x\n    prompt: hi\n")
    _yaml(cr / "workflow-defs" / "user-flow" / "WORKFLOW.yaml",
          "name: user-flow\ndescription: uses shared\nnodes:\n"
          "  - id: t\n    use: shared\n    inputs:\n      x: 1\n")
    out = tmp_path / "i.json"
    rc, summary, err = _run(cr, out)
    assert rc == 0, err
    idx = json.loads(out.read_text())
    shared = [e for e in idx if e["name"] == "shared"]
    assert {e["kind"] for e in shared} == {"microskill", "subgraph"}, "both same-name entries must survive"
    assert all(e["consumers"] == ["user-flow"] for e in shared), "consumer edge fans to BOTH"
    assert summary["consumer_map_count"] == 2


def test_workflow_and_subgraph_reference_edges(tmp_path):
    """_walk_refs collects use:/workflow:/subgraph:, not just use:. A workflow:
    ref and a subgraph: ref must each land a consumer edge."""
    cr = tmp_path / "catalog"
    _md(cr / "microskills" / "leaf" / "MICROSKILL.md", "leaf", "a leaf microskill")
    _yaml(cr / "workflow-defs" / "_subgraphs" / "rv" / "SUBGRAPH.yaml",
          "name: rv\ndescription: a subgraph\nnodes:\n  - id: a\n    agent: x\n    prompt: hi\n")
    _yaml(cr / "workflow-defs" / "child" / "WORKFLOW.yaml",
          "name: child\ndescription: a child workflow\nnodes:\n  - id: a\n    agent: x\n    prompt: hi\n")
    _yaml(cr / "workflow-defs" / "parent" / "WORKFLOW.yaml",
          "name: parent\ndescription: nests child + splices rv\nnodes:\n"
          "  - id: n\n    workflow: child\n  - id: s\n    subgraph: rv\n")
    out = tmp_path / "i.json"
    rc, _, err = _run(cr, out)
    assert rc == 0, err
    idx = {e["name"]: e for e in json.loads(out.read_text())}
    assert idx["child"]["consumers"] == ["parent"]   # workflow: edge
    assert idx["rv"]["consumers"] == ["parent"]       # subgraph: edge


def test_harness_custom_workflow_included(tmp_path):
    """F1 guard: a source:custom WORKFLOW is indexed via the `workflows` manifest
    key (closed grammar) resolved to the on-disk `workflow-defs` dir — manifest
    key and dir name must stay decoupled."""
    cr = _world(tmp_path)
    hroot = tmp_path / "harness"
    _yaml(hroot / "workflow-defs" / "custom-flow" / "WORKFLOW.yaml",
          "name: custom-flow\ndescription: a vendored custom workflow\n"
          "nodes:\n  - id: t\n    use: do-thing\n    inputs:\n      x: 1\n")
    harness = hroot / "harness.yaml"
    harness.write_text(
        "version: 2\nworkflows:\n  - name: custom-flow\n    source: custom\n", encoding="utf-8")
    out = tmp_path / "i.json"
    rc, _, err = _run(cr, out, harness=harness)
    assert rc == 0, err
    idx = {e["name"]: e for e in json.loads(out.read_text())}
    assert "custom-flow" in idx and idx["custom-flow"]["kind"] == "workflow"
    # its outgoing use: edge lands on the catalog microskill (sorted with thing-flow)
    assert idx["do-thing"]["consumers"] == ["custom-flow", "thing-flow"]


def test_malformed_input_exits_2_with_json_error(tmp_path):
    """BCI-2: a malformed component file yields a JSON {error} + exit 2, not a raw
    traceback / exit 1 (the documented exit-code contract)."""
    cr = tmp_path / "catalog"
    p = cr / "microskills" / "broken" / "MICROSKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nname: broken\ndescription: {oops: [unclosed\n---\n", encoding="utf-8")
    out = tmp_path / "i.json"
    rc, _, err = _run(cr, out)
    assert rc == 2, err
    assert "invalid YAML" in err


import os

REPO = Path(__file__).resolve().parents[3]
MS_ROOT = REPO / "catalog" / "microskills"
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
VALIDATE_MS = REPO / "catalog" / "scripts" / "validate-microskill"
_ENV = {**os.environ, "MICROSKILLS_TEMPLATES_ROOT": str(REPO / "templates")}


def test_build_catalog_index_microskill_validates():
    skill = MS_ROOT / "build-catalog-index"
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_MS), str(skill / "MICROSKILL.md"),
         str(skill / "profiles" / "base.yaml")],
        capture_output=True, text=True, env=_ENV)
    data = json.loads(proc.stdout)
    blocks = [i for i in data["issues"] if i["severity"] == "block"]
    assert data["pass"] is True, blocks


def test_build_catalog_index_microskill_resolves():
    proc = subprocess.run(
        [sys.executable, str(RESOLVE), "build-catalog-index",
         "--skill-root", str(MS_ROOT)],
        capture_output=True, text=True, env=_ENV)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["unresolved_vars"] == []
