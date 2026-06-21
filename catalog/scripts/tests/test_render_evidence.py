"""
Tests for render-evidence — the deterministic, lossless formatter that turns a
gate's structured evidence value (an object/array recorded in run-state) into a
human-readable markdown block.

WHY THIS EXISTS: a human approval gate must never default to a raw JSON wall.
The conductor renders the gate evidence core VERBATIM (approval-integrity
invariant), so the readable rendering cannot be hand-built by the LLM bookkeeper
— that would be neither byte-deterministic nor guaranteed lossless. This script
is the lossless pretty-printer: every key, every value, nulls explicit, nothing
truncated, byte-stable. The bookkeeper calls it; the conductor prints its output
unchanged. These tests are the integrity guarantee, mechanically enforced.

Hermetic: each test writes a throwaway value JSON under tmp_path and passes the
path as a flag — nothing touches the real repo.

Run: python3 -m pytest catalog/scripts/tests/test_render_evidence.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "render-evidence"


def render(value, tmp_path, name="value.json"):
    p = tmp_path / name
    p.write_text(json.dumps(value))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--value-file", str(p)],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# --- exact formatting (locks the contract) ---------------------------------

def test_flat_object_humanizes_keys_and_keeps_values(tmp_path):
    out = render({"kind": "promote", "reason": "too big"}, tmp_path)
    assert out == "**Kind:** promote\n**Reason:** too big\n"


def test_null_field_renders_as_none_not_null(tmp_path):
    out = render({"plan_path": None}, tmp_path)
    assert out == "**Plan path:** (none)\n"


def test_nested_object_is_indented(tmp_path):
    out = render({"scope_advisory": {"kind": "promote"}}, tmp_path)
    assert out == "**Scope advisory:**\n  **Kind:** promote\n"


def test_array_of_scalars_is_bulleted(tmp_path):
    out = render({"staging_paths": ["a/b.md", "c/d.yaml"]}, tmp_path)
    assert out == "**Staging paths:**\n  - a/b.md\n  - c/d.yaml\n"


def test_array_of_objects_renders_each_item(tmp_path):
    out = render({"issues": [{"severity": "high", "title": "X"}]}, tmp_path)
    assert out == (
        "**Issues:**\n"
        "  -\n"
        "    **Severity:** high\n"
        "    **Title:** X\n"
    )


def test_bool_renders_lowercase(tmp_path):
    out = render({"pass": False}, tmp_path)
    assert out == "**Pass:** false\n"


def test_empty_container_renders_none(tmp_path):
    out = render({"issues": [], "meta": {}}, tmp_path)
    assert out == "**Issues:** (none)\n**Meta:** (none)\n"


# --- the real surfaces this fixes ------------------------------------------

def test_scope_advisory_is_readable_not_json(tmp_path):
    # the exact session object that produced the raw-JSON wall at the gate
    value = {
        "plan_path": None,
        "name": None,
        "scope_advisory": {
            "kind": "promote",
            "reason": "implement-rvs is an inherently multi-stage pipeline",
            "recommendation": "Promote, not microskill.",
        },
    }
    out = render(value, tmp_path)
    # readable, not a JSON dump
    assert "```" not in out
    assert '"kind"' not in out and "{" not in out and "}" not in out
    # lossless: every field + value survives
    assert "**Plan path:** (none)" in out
    assert "**Name:** (none)" in out
    assert "**Scope advisory:**" in out
    assert "**Kind:** promote" in out
    assert "implement-rvs is an inherently multi-stage pipeline" in out
    assert "Promote, not microskill." in out


def test_verdict_issues_array_is_readable(tmp_path):
    value = [
        {"severity": "blocker", "title": "SQL injection", "fix": "parameterize"},
        {"severity": "low", "title": "naming"},
    ]
    out = render(value, tmp_path)
    assert "{" not in out
    for token in ("blocker", "SQL injection", "parameterize", "low", "naming"):
        assert token in out, token


# --- integrity invariants ---------------------------------------------------

def test_lossless_every_leaf_value_present(tmp_path):
    value = {
        "a": "alpha",
        "b": {"c": "charlie", "d": ["delta", "echo"]},
        "e": None,
        "f": [{"g": "golf"}],
    }
    out = render(value, tmp_path)
    for leaf in ("alpha", "charlie", "delta", "echo", "golf"):
        assert leaf in out, leaf
    assert "(none)" in out  # the null leaf


def test_long_string_is_not_truncated(tmp_path):
    long = "x" * 5000
    out = render({"reason": long}, tmp_path)
    assert long in out


def test_deterministic_byte_identical(tmp_path):
    value = {"kind": "promote", "reason": "stable", "nested": {"a": [1, 2, 3]}}
    a = render(value, tmp_path, "a.json")
    b = render(value, tmp_path, "b.json")
    assert a == b


def test_key_order_is_preserved(tmp_path):
    out = render({"zebra": 1, "apple": 2, "mango": 3}, tmp_path)
    assert out == "**Zebra:** 1\n**Apple:** 2\n**Mango:** 3\n"


# --- failure mode -----------------------------------------------------------

def test_malformed_json_exits_nonzero(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--value-file", str(p)],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode != 0
    assert proc.stdout == ""


def test_missing_file_exits_nonzero(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--value-file", str(tmp_path / "nope.json")],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode != 0
