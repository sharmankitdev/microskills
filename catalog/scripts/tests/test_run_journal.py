"""
Tests for run-journal — the per-run ledger helper (.compiled/runs/<run-id>/).
Run: python3 -m pytest catalog/scripts/tests/ -v

Hermetic: every test builds a throwaway runs/ world under tmp_path and passes all
paths as flags. The quarantine tests build a throwaway defs-root / harness world
the same way (test_harness_sync.py pattern) — nothing touches the real repo.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "catalog" / "scripts" / "run-journal"


def run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(REPO))
    data = None
    if proc.stdout.strip().startswith("{"):
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None  # JSONL output (project mode) — callers read raw stdout
    return proc.returncode, data, proc.stdout, proc.stderr


def init_run(tmp, run_id="20260610T120000Z-aaaaaa", manifest_hash="sha256:h1", *extra):
    rc, data, out, err = run("init", "--runs-dir", str(tmp / "runs"),
                             "--manifest-hash", manifest_hash, "--run-id", run_id, *extra)
    assert rc == 0, out + err
    return Path(data["run_dir"]), data


def journal_lines(run_dir):
    return [json.loads(l) for l in
            (run_dir / "journal.jsonl").read_text().splitlines() if l.strip()]


# ------------------------------------------------------------------- init

def test_init_mints_run_dir_config_and_journal(tmp_path):
    run_dir, data = init_run(tmp_path, "20260610T120000Z-aaaaaa", "sha256:h1",
                             "--profile", "autonomous",
                             "--override", "io.x=1", "--override", "io.y=2")
    assert run_dir == tmp_path / "runs" / "20260610T120000Z-aaaaaa"
    assert (run_dir / "run-inputs").is_dir()
    config = json.loads((run_dir / "run-config.json").read_text())
    assert config == {
        "v": 1,
        "run_id": "20260610T120000Z-aaaaaa",
        "manifest_hash": "sha256:h1",
        "profile_used": "autonomous",
        "overrides": ["io.x=1", "io.y=2"],
        "inputs": {},
    }
    # no stray tmp file left by the atomic write
    assert not (run_dir / "run-config.json.tmp").exists()
    lines = journal_lines(run_dir)
    assert len(lines) == 1
    assert lines[0]["event"] == "run_start"
    assert lines[0]["manifest_hash"] == "sha256:h1"
    assert lines[0]["profile"] == "autonomous"
    assert lines[0]["overrides"] == 2
    assert "ts" in lines[0] and lines[0]["v"] == 1


def test_init_default_run_id_is_timestamped_and_unique(tmp_path):
    rc, d1, out, err = run("init", "--runs-dir", str(tmp_path / "runs"),
                           "--manifest-hash", "sha256:h1")
    assert rc == 0, out + err
    rc, d2, *_ = run("init", "--runs-dir", str(tmp_path / "runs"),
                     "--manifest-hash", "sha256:h1")
    assert rc == 0
    assert d1["run_id"] != d2["run_id"]
    # UTC-timestamp prefix (lexical == chronological) + "-" + random hex suffix
    stamp, _, suffix = d1["run_id"].partition("-")
    assert len(stamp) == 16 and stamp[8] == "T" and stamp.endswith("Z")
    assert stamp.replace("T", "").replace("Z", "").isdigit()
    assert len(suffix) == 6 and all(c in "0123456789abcdef" for c in suffix)
    config = json.loads((Path(d1["run_dir"]) / "run-config.json").read_text())
    assert config["profile_used"] is None and config["overrides"] == []


def test_init_collision_fails_loud(tmp_path):
    init_run(tmp_path, "r1")
    rc, data, out, err = run("init", "--runs-dir", str(tmp_path / "runs"),
                             "--manifest-hash", "sha256:h1", "--run-id", "r1")
    assert rc != 0
    assert "already exists" in data["error"]


def test_init_rejects_path_traversal_run_id(tmp_path):
    rc, data, *_ = run("init", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1", "--run-id", "../evil")
    assert rc != 0
    assert "invalid run id" in data["error"]


# ----------------------------------------------------------- record-inputs

def test_record_inputs_merges_config_and_journals_sizes(tmp_path):
    run_dir, _ = init_run(tmp_path)
    scratch = run_dir / "inputs.tmp.json"
    scratch.write_text(json.dumps({"diff_path": "/abs/d.cat", "post_to_pr": False}))
    rc, data, out, err = run("record-inputs", "--run-dir", str(run_dir),
                             "--inputs-file", "inputs.tmp.json")
    assert rc == 0, out + err
    assert data["ok"] is True and data["inputs"] == 2
    config = json.loads((run_dir / "run-config.json").read_text())
    assert config["inputs"] == {"diff_path": "/abs/d.cat", "post_to_pr": False}
    # the scratch file inside the run dir is consumed
    assert not scratch.exists()
    rec = journal_lines(run_dir)[-1]
    assert rec["event"] == "inputs_recorded"
    assert rec["input_bytes"] == {
        "diff_path": len(json.dumps("/abs/d.cat")),
        "post_to_pr": len(json.dumps(False)),
    }


def test_record_inputs_leaves_outside_files_alone(tmp_path):
    run_dir, _ = init_run(tmp_path)
    outside = tmp_path / "caller-supplied.json"
    outside.write_text(json.dumps({"a": 1}))
    rc, data, *_ = run("record-inputs", "--run-dir", str(run_dir),
                       "--inputs-file", str(outside))
    assert rc == 0
    assert outside.exists()  # never consume a file outside the run dir


def test_record_inputs_rejects_non_object(tmp_path):
    run_dir, _ = init_run(tmp_path)
    (run_dir / "bad.json").write_text("[1,2]")
    rc, data, *_ = run("record-inputs", "--run-dir", str(run_dir),
                       "--inputs-file", "bad.json")
    assert rc != 0
    assert "JSON object" in data["error"]


# ------------------------------------------------------------------ append

def test_append_commit_state_promotes_tmp_and_sizes_from_state(tmp_path):
    run_dir, _ = init_run(tmp_path)
    state = {"manifest_hash": "sha256:h1", "step_index": 2,
             "results": {"plan": {"plan_path": "/p.yaml"}, "g1": {"choice": "approve"}}}
    (run_dir / "run-state.json.tmp").write_text(json.dumps(state))
    rc, data, out, err = run("append", "--run-dir", str(run_dir),
                             "--event", "step_complete", "--step-index", "1",
                             "--label", "Approval: g1", "--gate", "g1",
                             "--choice", "approve", "--outcome", "ok",
                             "--commit-state", "run-state.json.tmp")
    assert rc == 0, out + err
    # tmp was atomically promoted (os.replace): no tmp left, state committed
    assert not (run_dir / "run-state.json.tmp").exists()
    assert json.loads((run_dir / "run-state.json").read_text()) == state
    line = journal_lines(run_dir)[-1]
    assert line["event"] == "step_complete"
    assert line["step_index"] == 1
    assert line["label"] == "Approval: g1"
    assert line["gate"] == "g1" and line["choice"] == "approve"
    assert line["outcome"] == "ok"
    # sizes computed by READING the committed state — never passed on argv
    assert line["state_bytes"] == (run_dir / "run-state.json").stat().st_size
    assert line["result_bytes"] == {
        "plan": len(json.dumps({"plan_path": "/p.yaml"}, sort_keys=True)),
        "g1": len(json.dumps({"choice": "approve"}, sort_keys=True)),
    }


def test_append_commit_state_manifest_hash_mismatch_fails_before_replace(tmp_path):
    run_dir, _ = init_run(tmp_path, manifest_hash="sha256:h1")
    bad = {"manifest_hash": "sha256:OTHER", "step_index": 1, "results": {}}
    (run_dir / "run-state.json.tmp").write_text(json.dumps(bad))
    rc, data, *_ = run("append", "--run-dir", str(run_dir),
                       "--event", "step_complete", "--step-index", "0",
                       "--commit-state", "run-state.json.tmp")
    assert rc != 0
    assert "manifest_hash" in data["error"]
    # fail-loud BEFORE the replace: no run-state.json landed, tmp untouched
    assert not (run_dir / "run-state.json").exists()
    assert (run_dir / "run-state.json.tmp").exists()


def test_append_commit_state_malformed_shape_fails(tmp_path):
    run_dir, _ = init_run(tmp_path)
    (run_dir / "s.tmp").write_text(json.dumps(
        {"manifest_hash": "sha256:h1", "step_index": "two", "results": {}}))
    rc, data, *_ = run("append", "--run-dir", str(run_dir),
                       "--event", "step_complete", "--commit-state", "s.tmp")
    assert rc != 0
    assert "step_index" in data["error"]


def test_append_unknown_event_and_outcome_fail_loud(tmp_path):
    run_dir, _ = init_run(tmp_path)
    rc, data, *_ = run("append", "--run-dir", str(run_dir), "--event", "step_started")
    assert rc != 0 and "unknown event" in data["error"]
    rc, data, *_ = run("append", "--run-dir", str(run_dir),
                       "--event", "step_complete", "--outcome", "fine")
    assert rc != 0 and "invalid outcome" in data["error"]


def test_append_without_state_omits_size_fields(tmp_path):
    run_dir, _ = init_run(tmp_path)
    rc, data, out, err = run("append", "--run-dir", str(run_dir),
                             "--event", "run_error", "--step-index", "0",
                             "--outcome", "error", "--label", "segment failed")
    assert rc == 0, out + err
    line = journal_lines(run_dir)[-1]
    assert line["event"] == "run_error"
    assert "state_bytes" not in line and "result_bytes" not in line
    # omitted-when-absent keys: no nulls ride in journal lines
    assert "gate" not in line and "choice" not in line and "node" not in line


# ------------------------------------------------------------------ latest

def seed_run(tmp, run_id, manifest_hash, step_index, finished=False):
    run_dir, _ = init_run(tmp, run_id, manifest_hash)
    state = {"manifest_hash": manifest_hash, "step_index": step_index, "results": {}}
    (run_dir / "s.tmp").write_text(json.dumps(state))
    rc, *_ = run("append", "--run-dir", str(run_dir), "--event", "step_complete",
                 "--step-index", str(step_index - 1), "--commit-state", "s.tmp")
    assert rc == 0
    return run_dir


def test_latest_picks_newest_matching_run(tmp_path):
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 1)
    seed_run(tmp_path, "20260610T110000Z-bbbbbb", "sha256:h1", 3)
    seed_run(tmp_path, "20260610T120000Z-cccccc", "sha256:OTHER", 5)
    rc, data, out, err = run("latest", "--runs-dir", str(tmp_path / "runs"),
                             "--manifest-hash", "sha256:h1")
    assert rc == 0, out + err
    assert data["found"] is True
    assert data["run_id"] == "20260610T110000Z-bbbbbb"  # newest MATCHING, not newest overall
    assert data["step_index"] == 3
    assert data["run_state"].endswith("run-state.json")
    assert data["run_config"].endswith("run-config.json")


def test_latest_no_match_or_empty(tmp_path):
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is False
    seed_run(tmp_path, "r1", "sha256:OTHER", 1)
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is False


def test_latest_skips_finished_runs_via_steps(tmp_path):
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 2)
    seed_run(tmp_path, "20260610T110000Z-bbbbbb", "sha256:h1", 5)  # finished (M=5)
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1", "--steps", "5")
    assert rc == 0
    assert data["found"] is True
    assert data["run_id"] == "20260610T100000Z-aaaaaa"


def test_latest_skips_corrupt_state_without_dying(tmp_path):
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 1)
    bad = tmp_path / "runs" / "20260610T110000Z-bbbbbb"
    bad.mkdir(parents=True)
    (bad / "run-state.json").write_text("{not json")
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0
    assert data["found"] is True and data["run_id"] == "20260610T100000Z-aaaaaa"
    assert data["skipped"] == ["20260610T110000Z-bbbbbb"]


def test_latest_ignores_run_dir_without_state(tmp_path):
    # a run that crashed before its first step_complete has no run-state.json
    init_run(tmp_path, "20260610T100000Z-aaaaaa")
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is False


# --------------------------------------------------------- project / report

def make_played_run(tmp, run_id):
    """One full run: init -> inputs -> two steps -> complete."""
    run_dir, _ = init_run(tmp, run_id, "sha256:h1", "--profile", "lite")
    (run_dir / "in.json").write_text(json.dumps({"diff_path": "/d.cat"}))
    rc, *_ = run("record-inputs", "--run-dir", str(run_dir), "--inputs-file", "in.json")
    assert rc == 0
    for i, results in enumerate([{"a": {"x": 1}}, {"a": {"x": 1}, "g1": {"choice": "approve"}}]):
        (run_dir / "s.tmp").write_text(json.dumps(
            {"manifest_hash": "sha256:h1", "step_index": i + 1, "results": results}))
        rc, *_ = run("append", "--run-dir", str(run_dir), "--event", "step_complete",
                     "--step-index", str(i), "--label", f"Step{i}",
                     "--commit-state", "s.tmp")
        assert rc == 0
    rc, *_ = run("append", "--run-dir", str(run_dir),
                 "--event", "run_complete", "--outcome", "ok")
    assert rc == 0
    return run_dir


def test_project_strips_timestamps_and_is_diffable_across_runs(tmp_path):
    d1 = make_played_run(tmp_path, "20260610T100000Z-aaaaaa")
    d2 = make_played_run(tmp_path, "20260610T110000Z-bbbbbb")
    rc1, _, out1, err1 = run("project", "--run-dir", str(d1))
    rc2, _, out2, err2 = run("project", "--run-dir", str(d2))
    assert rc1 == 0 and rc2 == 0, err1 + err2
    # mechanically diffable: identical activity -> byte-identical projection
    assert out1 == out2
    assert "ts" not in json.loads(out1.splitlines()[0])
    events = [json.loads(l)["event"] for l in out1.splitlines()]
    assert events == ["run_start", "inputs_recorded",
                      "step_complete", "step_complete", "run_complete"]


def test_project_fails_loud_on_corrupt_journal(tmp_path):
    run_dir, _ = init_run(tmp_path)
    with open(run_dir / "journal.jsonl", "a") as f:
        f.write("garbage\n")
    rc, data, *_ = run("project", "--run-dir", str(run_dir))
    assert rc != 0
    assert "unparseable" in data["error"]


def test_report_renders_human_readably(tmp_path):
    run_dir = make_played_run(tmp_path, "20260610T100000Z-aaaaaa")
    rc, _, out, err = run("report", "--run-dir", str(run_dir))
    assert rc == 0, err
    assert "20260610T100000Z-aaaaaa" in out
    assert "sha256:h1" in out
    assert "lite" in out
    assert "Step0" in out and "Step1" in out
    assert "run_complete" in out
    # timestamps DO appear in the report (it is the human view, not the projection)
    assert "T" in out.splitlines()[-1]
