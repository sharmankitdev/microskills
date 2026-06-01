"""Hermetic tests for the plugin version writer. No real repo files touched."""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "set_plugin_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("set_plugin_version", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample(tmp_path: Path) -> Path:
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({
        "name": "microskills",
        "description": "engine — bootstraps a project — dormant data",
        "version": "0.1.0",
        "author": {"name": "Ankit Sharma"},
    }, indent=2) + "\n")
    return p


def test_updates_version_and_preserves_other_keys(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("0.2.0", path=target)
    data = json.loads(target.read_text())
    assert data["version"] == "0.2.0"
    assert list(data.keys()) == ["name", "description", "version", "author"]
    assert data["author"] == {"name": "Ankit Sharma"}


def test_preserves_non_ascii_and_trailing_newline(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("0.2.0", path=target)
    text = target.read_text()
    assert "—" in text  # em-dash not escaped to \\u2014
    assert text.endswith("}\n")


def test_accepts_prerelease(tmp_path):
    mod = _load()
    target = _sample(tmp_path)
    mod.set_version("1.0.0-rc.1", path=target)
    assert json.loads(target.read_text())["version"] == "1.0.0-rc.1"


@pytest.mark.parametrize("bad", ["v1.2.3", "1.2", "abc", "1.2.3.4", ""])
def test_rejects_non_semver(tmp_path, bad):
    mod = _load()
    target = _sample(tmp_path)
    with pytest.raises(ValueError):
        mod.set_version(bad, path=target)
