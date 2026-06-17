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
