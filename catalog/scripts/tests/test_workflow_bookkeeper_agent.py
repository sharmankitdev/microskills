import importlib.machinery, importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "catalog" / "scripts" / "initialize-harness"
AGENT = REPO / "catalog" / "agents" / "workflow-bookkeeper" / "AGENT.md"


def _load_init():
    loader = importlib.machinery.SourceFileLoader("initialize_harness", str(INIT))
    spec = importlib.util.spec_from_loader("initialize_harness", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _frontmatter(path):
    import yaml
    text = path.read_text()
    assert text.startswith("---\n"), "AGENT.md must open with YAML frontmatter"
    fm = text.split("---\n", 2)[1]
    return yaml.safe_load(fm)


def test_bookkeeper_frontmatter_locks_toolset(tmp_path):
    fm = _frontmatter(AGENT)
    assert fm["name"] == "workflow-bookkeeper"
    tools = [t.strip() for t in fm["tools"].split(",")] if isinstance(fm["tools"], str) else fm["tools"]
    assert sorted(tools) == ["Bash", "Read", "Write"], "locked toolset: no AskUserQuestion/Workflow"


def test_bookkeeper_rides_engine_bundle(tmp_path):
    mod = _load_init()
    outs = mod.engine_outputs(REPO / "catalog", tmp_path / ".claude")
    srcs = [str(s) for s, _ in outs]
    assert any(s.endswith("catalog/agents/workflow-bookkeeper/AGENT.md") for s in srcs), \
        "bookkeeper must materialize via the engine bundle (no harness.yaml entry)"


SKILL = REPO / "catalog" / "skills" / "workflow" / "SKILL.md"


def _body(path):
    """Component body with the YAML frontmatter stripped."""
    text = path.read_text()
    return text.split("---\n", 2)[2] if text.startswith("---\n") else text


def test_conductor_body_issues_no_orchestration_cli(tmp_path):
    # §11.1 success criterion: every deterministic CLI call lives in the
    # bookkeeper (off the main transcript). The conductor body must invoke none
    # of the scripts directly — a `.claude/scripts/<x>` string in the body would
    # mean a raw Bash/Read tool block leaking into the user-facing transcript.
    assert ".claude/scripts/" not in _body(SKILL), \
        "conductor body must issue no script CLI — all of it lives in the bookkeeper"


def test_bookkeeper_owns_the_orchestration_cli(tmp_path):
    # the flip side: the CLI did not vanish — it MOVED to the bookkeeper.
    body = AGENT.read_text()
    for cli in ("compile-workflow", "run-journal", "run-step",
                "check-step-io", "normalize-input"):
        assert f".claude/scripts/{cli}" in body, f"bookkeeper must own the {cli} CLI"


def test_bookkeeper_commit_uses_deterministic_merge(tmp_path):
    # DRI-1: op:commit overlays via the helper + checks with --full, never an
    # LLM Read-merge-reWrite of the whole accumulated run-state.
    body = AGENT.read_text()
    assert "run-journal merge-result" in body, \
        "op:commit must use the deterministic merge helper (no LLM whole-state re-serialize)"
    assert "check-step-io" in body and "--full" in body, \
        "the pre-commit IO check must run --full (prior-result corruption backstop)"


def test_bookkeeper_renders_structured_evidence_via_script(tmp_path):
    # A human gate must never get a raw JSON wall: object/array evidence is
    # rendered readable by the tested render-evidence formatter, NOT hand-built.
    body = AGENT.read_text()
    assert ".claude/scripts/render-evidence" in body, \
        "the bookkeeper must own the render-evidence CLI (deterministic readable render)"
    assert '"kind": "structured"' in body, \
        "object/array evidence must resolve to a structured entry (value + render)"
    # the render is delegated to tested code, never summarized by the LLM
    assert "NEVER hand-write, summarize" in body or "never hand-write, summarize" in body.lower(), \
        "the render must come from the script verbatim, never hand-built by the bookkeeper"


def test_conductor_renders_structured_evidence_readably(tmp_path):
    # The conductor prints the structured render verbatim; raw json is opt-in,
    # not the default for a human approval gate.
    body = SKILL.read_text()
    assert "`structured`" in body, "evidence-core must document the structured kind"
    assert "raw JSON wall" in body, \
        "the contract must state a human gate never gets a raw JSON wall"
