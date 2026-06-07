"""
Tests for catalog/scripts/normalize-input — the tested implementation of the dispatcher's
large/multi-shape input-by-reference normalization (string | file | directory -> one file).

Invokes the script as a subprocess (the path the dispatcher uses via Bash) and asserts on
stdout JSON + on-disk state. Hermetic: everything under tmp_path.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "normalize-input"


def run(value, out):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--value", str(value), "--out", str(out)],
        capture_output=True, text=True)
    data = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, data, proc.stdout, proc.stderr


def test_string_is_written_to_file(tmp_path):
    # The provision case: a literal requirement STRING -> a canonical file the body Reads.
    out = tmp_path / "run-inputs" / "requirement_path"
    rc, data, _o, err = run("a requirement: lint commit messages", out)
    assert rc == 0, err
    assert data["shape"] == "string"
    assert data["path"] == str(out.resolve())
    assert out.read_text() == "a requirement: lint commit messages"
    assert data["warning"] is None


def test_file_is_passed_through_not_copied(tmp_path):
    # A real file -> its own absolute path, contents untouched, no copy to --out.
    src = tmp_path / "diff.patch"
    src.write_text("diff --git a/x b/x\n")
    out = tmp_path / "run-inputs" / "diff_path"
    rc, data, _o, err = run(src, out)
    assert rc == 0, err
    assert data["shape"] == "file"
    assert data["path"] == str(src.resolve())
    assert not out.exists()  # pass-through, nothing written to --out


def test_directory_concatenates_sorted_with_headers(tmp_path):
    d = tmp_path / "specs"
    (d / "sub").mkdir(parents=True)
    (d / "b.md").write_text("Bee")
    (d / "a.md").write_text("Aye")
    (d / "sub" / "c.md").write_text("Cee")
    out = tmp_path / "cat.txt"
    rc, data, _o, err = run(d, out)
    assert rc == 0, err
    assert data["shape"] == "dir"
    # byte-stable relative-path order (codepoint == C locale for ASCII), per-file headers
    assert out.read_text() == (
        "=== a.md ===\nAye\n"
        "=== b.md ===\nBee\n"
        "=== sub/c.md ===\nCee\n"
    )


def test_directory_concat_is_byte_deterministic(tmp_path):
    d = tmp_path / "specs"
    d.mkdir()
    for n in ("z", "m", "a"):
        (d / f"{n}.txt").write_text(n * 3)
    out1, out2 = tmp_path / "1.txt", tmp_path / "2.txt"
    run(d, out1)
    run(d, out2)
    assert out1.read_text() == out2.read_text()  # same inputs -> byte-identical


def test_size_guard_warns_over_threshold(tmp_path):
    out = tmp_path / "big.txt"
    rc, data, _o, err = run("x" * (256 * 1024 + 1), out)
    assert rc == 0, err
    assert data["bytes"] > 256 * 1024
    assert data["warning"] is not None and "distillation" in data["warning"]


def test_small_input_no_warning(tmp_path):
    out = tmp_path / "small.txt"
    rc, data, _o, err = run("tiny", out)
    assert rc == 0, err
    assert data["warning"] is None
