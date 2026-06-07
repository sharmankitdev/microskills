"""
Tests for catalog/scripts/normalize-input — the tested implementation of the dispatcher's
multi-shape input-by-reference normalization (file | directory PATH -> one canonical file).

normalize-input takes PATHS only — never untrusted content on argv (a literal-string
materialize value is written to a file by the dispatcher via the Write tool, then its path
is passed here). Invokes the script as a subprocess and asserts on stdout JSON + on-disk
state. Hermetic: everything under tmp_path.
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


def test_file_is_passed_through_not_copied(tmp_path):
    # A real file (e.g. a diff the caller wrote, or a string the dispatcher Write-tool'd to
    # a file) -> its own absolute path, contents untouched, no copy to --out.
    src = tmp_path / "diff.patch"
    src.write_text("diff --git a/x b/x\n")
    out = tmp_path / "run-inputs" / "diff_path"
    rc, data, _o, err = run(src, out)
    assert rc == 0, err
    assert data["shape"] == "file"
    assert data["path"] == str(src.resolve())
    assert not out.exists()  # pass-through, nothing written to --out
    assert data["warning"] is None


def test_missing_path_is_an_error_not_a_string(tmp_path):
    # A value that is not an existing path is a hard error — never silently treated as a
    # literal string (closes the shape-sniffing ambiguity). The dispatcher passes only paths.
    out = tmp_path / "out"
    rc, data, _o, err = run("a requirement: lint commit messages", out)
    assert rc == 1
    assert "does not exist" in (data or {}).get("error", "")
    assert not out.exists()


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


def test_directory_out_inside_source_is_not_self_included(tmp_path):
    # If --out lives inside the source dir and pre-exists, it must NOT be enumerated by the
    # walk (would corrupt byte-determinism by appending prior output to itself).
    d = tmp_path / "specs"
    d.mkdir()
    (d / "a.txt").write_text("A")
    (d / "b.txt").write_text("B")
    out = d / "out.txt"
    out.write_text("STALE")  # pre-exists inside the source dir
    rc, data, _o, err = run(d, out)
    assert rc == 0, err
    assert out.read_text() == "=== a.txt ===\nA\n=== b.txt ===\nB\n"  # out.txt excluded


def test_size_guard_warns_on_large_file(tmp_path):
    big = tmp_path / "huge.diff"
    big.write_text("y" * (256 * 1024 + 10))
    out = tmp_path / "ignored"
    rc, data, _o, err = run(big, out)
    assert rc == 0, err
    assert data["shape"] == "file"
    assert data["warning"] is not None and "distillation" in data["warning"]


def test_small_file_no_warning(tmp_path):
    src = tmp_path / "small.txt"
    src.write_text("tiny")
    rc, data, _o, err = run(src, tmp_path / "out")
    assert rc == 0, err
    assert data["warning"] is None
