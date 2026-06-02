"""Hermetic tests for the harness-drift-check PreToolUse hook. No real repo touched."""
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "harness-drift-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("harness_drift_check", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emit(mod, text):
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.emit_advisory(text)
    return json.loads(buf.getvalue())


def test_advisory_surfaces_offer_on_user_console_via_system_message():
    """systemMessage is the only documented channel that reaches the user's console;
    it carries the factual summary plus a plain-language offer to run the journey."""
    out = _emit(_load(), "microskills harness drift: 2 new base components")
    assert out["systemMessage"].startswith("microskills harness drift: 2 new base components")
    assert "/initialize-harness" in out["systemMessage"]


def test_advisory_directs_model_to_offer_running_the_skill():
    """additionalContext carries the summary plus an actionable directive for the model."""
    out = _emit(_load(), "microskills harness drift: 2 new base components")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("microskills harness drift: 2 new base components")
    assert "initialize-harness" in ctx
    assert "offer" in ctx.lower()
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_build_message_none_when_no_drift():
    assert _load().build_message({"available_base": [], "summary": {}}) is None


def test_build_message_is_factual_summary_only():
    """build_message returns just the drift facts; the call-to-action lives in emit_advisory."""
    msg = _load().build_message({"available_base": [{"name": "foo"}, {"name": "bar"}]})
    assert msg.startswith("microskills harness drift:")
    assert "2 new base component(s) available (foo, bar)" in msg
    assert "/initialize-harness" not in msg
