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
    # record-inputs SEEDS the resume checkpoint with the full run-state shape
    # so the run-step kernel can build the first segment's args from it
    assert data["run_state"].endswith("run-state.json")
    state = json.loads((run_dir / "run-state.json").read_text())
    assert state == {"manifest_hash": "sha256:h1", "step_index": 0,
                     "inputs": {"diff_path": "/abs/d.cat", "post_to_pr": False},
                     "results": {}}
    assert not (run_dir / "run-state.json.tmp").exists()
    rec = journal_lines(run_dir)[-1]
    assert rec["event"] == "inputs_recorded"
    assert rec["input_bytes"] == {
        "diff_path": len(json.dumps("/abs/d.cat")),
        "post_to_pr": len(json.dumps(False)),
    }


def test_record_inputs_refreshes_existing_state_keeping_progress(tmp_path):
    # re-recording must not clobber step_index/results of an existing state
    run_dir, _ = init_run(tmp_path)
    state = {"manifest_hash": "sha256:h1", "step_index": 2,
             "inputs": {"old": 1}, "results": {"plan": {"x": 1}}}
    (run_dir / "s.tmp").write_text(json.dumps(state))
    rc, *_ = run("append", "--run-dir", str(run_dir), "--event", "step_complete",
                 "--step-index", "1", "--commit-state", "s.tmp")
    assert rc == 0
    (run_dir / "in.json").write_text(json.dumps({"new": 2}))
    rc, *_ = run("record-inputs", "--run-dir", str(run_dir),
                 "--inputs-file", "in.json")
    assert rc == 0
    got = json.loads((run_dir / "run-state.json").read_text())
    assert got == {"manifest_hash": "sha256:h1", "step_index": 2,
                   "inputs": {"new": 2}, "results": {"plan": {"x": 1}}}


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
             "inputs": {"diff_path": "/d.cat"},
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
    bad = {"manifest_hash": "sha256:OTHER", "step_index": 1,
           "inputs": {}, "results": {}}
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
        {"manifest_hash": "sha256:h1", "step_index": "two",
         "inputs": {}, "results": {}}))
    rc, data, *_ = run("append", "--run-dir", str(run_dir),
                       "--event", "step_complete", "--commit-state", "s.tmp")
    assert rc != 0
    assert "step_index" in data["error"]


def test_append_commit_state_requires_inputs_map(tmp_path):
    # the legacy {manifest_hash, step_index, results} shape is no longer
    # committable — the run-step kernel reads inputs from the run-state
    run_dir, _ = init_run(tmp_path)
    (run_dir / "s.tmp").write_text(json.dumps(
        {"manifest_hash": "sha256:h1", "step_index": 1, "results": {}}))
    rc, data, *_ = run("append", "--run-dir", str(run_dir),
                       "--event", "step_complete", "--commit-state", "s.tmp")
    assert rc != 0
    assert "inputs must be an object" in data["error"]
    assert not (run_dir / "run-state.json").exists()


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
    state = {"manifest_hash": manifest_hash, "step_index": step_index,
             "inputs": {}, "results": {}}
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


def test_latest_skips_pre_shape_state_without_inputs(tmp_path):
    # a legacy state without an inputs map cannot be resumed by the run-step
    # kernel — never offer it, even when its manifest_hash matches
    legacy = tmp_path / "runs" / "20260610T110000Z-bbbbbb"
    legacy.mkdir(parents=True)
    (legacy / "run-state.json").write_text(json.dumps(
        {"manifest_hash": "sha256:h1", "step_index": 1, "results": {}}))
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is False
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 1)
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is True
    assert data["run_id"] == "20260610T100000Z-aaaaaa"


# ------------------------------------------------- latest (rerun source scan)

def test_latest_without_hash_filter_returns_newest_committed_any_hash(tmp_path):
    # The rerun source scan: --manifest-hash omitted -> newest committed run
    # regardless of hash, FINISHED runs included (no --steps), with the
    # recorded compile provenance surfaced so the caller can reproduce it.
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 2)
    run_dir, _ = init_run(tmp_path, "20260610T110000Z-bbbbbb", "sha256:h2",
                          "--profile", "lite", "--override", "io.x=1")
    state = {"manifest_hash": "sha256:h2", "step_index": 4,
             "inputs": {"diff_path": "/d.cat"}, "results": {"a": {"x": 1}}}
    (run_dir / "s.tmp").write_text(json.dumps(state))
    rc, *_ = run("append", "--run-dir", str(run_dir), "--event", "run_complete",
                 "--outcome", "ok", "--commit-state", "s.tmp")
    assert rc == 0
    rc, data, out, err = run("latest", "--runs-dir", str(tmp_path / "runs"))
    assert rc == 0, out + err
    assert data["found"] is True
    assert data["run_id"] == "20260610T110000Z-bbbbbb"  # newest, finished, h2
    assert data["manifest_hash"] == "sha256:h2"
    assert data["profile_used"] == "lite"
    assert data["overrides"] == ["io.x=1"]


def test_latest_resume_scan_still_filters_on_hash(tmp_path):
    # the resume scan (--manifest-hash given) is unchanged by the relaxation
    seed_run(tmp_path, "20260610T100000Z-aaaaaa", "sha256:h1", 1)
    seed_run(tmp_path, "20260610T110000Z-bbbbbb", "sha256:OTHER", 1)
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1")
    assert rc == 0 and data["found"] is True
    assert data["run_id"] == "20260610T100000Z-aaaaaa"
    assert data["manifest_hash"] == "sha256:h1"


# ------------------------------------------------------------------- rerun

# A 4-step manifest: segment[a,b] -> gate g1 -> segment[c] -> orchestrator fin.
RERUN_STEPS = [
    {"kind": "segment", "index": 1, "script": ".compiled/seg-1.js",
     "nodes": ["a", "b"], "produces": ["a", "b"], "needs": {}, "io": {}},
    {"kind": "checkpoint", "checkpoint_type": "gate",
     "gate": {"id": "g1", "type": "human_approval", "after": "b",
              "prompt": "approve?", "options": ["approve", "abandon"]}},
    {"kind": "segment", "index": 2, "script": ".compiled/seg-2.js",
     "nodes": ["c"], "produces": ["c"], "needs": {}, "io": {}},
    {"kind": "checkpoint", "checkpoint_type": "orchestrator_node",
     "node": "fin", "prompt": "finalize", "io": {}},
]
RERUN_RESULTS = {"a": {"x": 1}, "b": {"y": 2}, "g1": {"choice": "approve"},
                 "c": {"z": 3}, "fin": {"done": True}}
RERUN_INPUTS = {"diff_path": "/runs/src/run-inputs/diff.cat", "depth": "lite"}


def write_manifest(tmp, manifest_hash="sha256:h1"):
    path = tmp / "manifest.json"
    path.write_text(json.dumps({
        "name": "flow", "manifest_hash": manifest_hash, "steps": RERUN_STEPS}))
    return path


def make_source_run(tmp, run_id="20260610T100000Z-source", step_index=4,
                    results=RERUN_RESULTS, inputs=RERUN_INPUTS,
                    manifest_hash="sha256:h1"):
    run_dir, _ = init_run(tmp, run_id, manifest_hash,
                          "--profile", "lite", "--override", "io.x=1")
    (run_dir / "in.json").write_text(json.dumps(inputs))
    rc, *_ = run("record-inputs", "--run-dir", str(run_dir),
                 "--inputs-file", "in.json")
    assert rc == 0
    state = {"manifest_hash": manifest_hash, "step_index": step_index,
             "inputs": inputs, "results": results}
    (run_dir / "s.tmp").write_text(json.dumps(state))
    rc, *_ = run("append", "--run-dir", str(run_dir), "--event", "step_complete",
                 "--step-index", str(step_index - 1), "--commit-state", "s.tmp")
    assert rc == 0
    return run_dir


def do_rerun(tmp, *extra, manifest_hash="sha256:h1"):
    manifest = write_manifest(tmp, manifest_hash)
    return run("rerun", "--runs-dir", str(tmp / "runs"),
               "--manifest", str(manifest),
               "--source-run", "20260610T100000Z-source",
               "--run-id", "20260610T200000Z-rerun", *extra)


def test_rerun_seeds_pre_from_results_replays_gates_and_flags_confirms(tmp_path):
    src = make_source_run(tmp_path)
    rc, data, out, err = do_rerun(tmp_path, "--from", "c")
    assert rc == 0, out + err
    assert data["from"] == {"selector": "c", "step_index": 2,
                            "snapped_to_segment": False}
    # gates BEFORE the from-point replay their recorded choice — never re-asked
    assert data["replayed_gates"] == [
        {"step_index": 1, "gate": "g1", "choice": "approve"}]
    # orchestrator-world steps at/after the from-point re-execute side effects
    assert data["confirm_steps"] == [
        {"step_index": 3, "checkpoint_type": "orchestrator_node", "node": "fin"}]
    assert data["seeded_results"] == ["a", "b", "g1"]
    run_dir = Path(data["run_dir"])
    state = json.loads((run_dir / "run-state.json").read_text())
    # results at/after the from-point are DROPPED (c, fin never leak forward);
    # inputs are FROZEN verbatim from the record
    assert state == {"manifest_hash": "sha256:h1", "step_index": 2,
                     "inputs": RERUN_INPUTS,
                     "results": {"a": {"x": 1}, "b": {"y": 2},
                                 "g1": {"choice": "approve"}}}
    config = json.loads((run_dir / "run-config.json").read_text())
    assert config == {
        "v": 1,
        "run_id": "20260610T200000Z-rerun",
        "manifest_hash": "sha256:h1",
        "profile_used": "lite",            # provenance copied from the source
        "overrides": ["io.x=1"],
        "inputs": RERUN_INPUTS,
        "rerun_of": "20260610T100000Z-source",
        "from_step_index": 2,
    }
    assert (run_dir / "run-inputs").is_dir()
    lines = journal_lines(run_dir)
    assert [l["event"] for l in lines] == ["run_start", "rerun"]
    assert lines[0]["rerun_of"] == "20260610T100000Z-source"
    assert lines[0]["from_step_index"] == 2
    assert lines[1]["replayed_gates"] == {"g1": "approve"}
    assert "snapped_to_segment" not in lines[1]
    # PIN clean-finish retention: the source run dir is provenance — untouched
    assert src.is_dir()
    src_state = json.loads((src / "run-state.json").read_text())
    assert src_state["step_index"] == 4 and src_state["results"] == RERUN_RESULTS
    assert [l["event"] for l in journal_lines(src)] == [
        "run_start", "inputs_recorded", "step_complete"]


def test_rerun_snaps_mid_segment_node_to_segment_start(tmp_path):
    # --from b names a node INSIDE segment[a,b]: segments are atomic, so the
    # from-point snaps to the segment start (the whole segment re-runs)
    make_source_run(tmp_path)
    rc, data, out, err = do_rerun(tmp_path, "--from", "b")
    assert rc == 0, out + err
    assert data["from"] == {"selector": "b", "step_index": 0,
                            "snapped_to_segment": True}
    assert data["replayed_gates"] == [] and data["seeded_results"] == []
    state = json.loads((Path(data["run_dir"]) / "run-state.json").read_text())
    assert state["step_index"] == 0 and state["results"] == {}
    assert state["inputs"] == RERUN_INPUTS  # inputs frozen even for a full replay


def test_rerun_from_gate_re_presents_it(tmp_path):
    # the gate AT the from-point is re-presented (a fresh choice), not replayed
    make_source_run(tmp_path)
    rc, data, out, err = do_rerun(tmp_path, "--from", "g1")
    assert rc == 0, out + err
    assert data["from"]["step_index"] == 1
    assert data["from"]["snapped_to_segment"] is False
    assert data["replayed_gates"] == []
    assert data["seeded_results"] == ["a", "b"]
    state = json.loads((Path(data["run_dir"]) / "run-state.json").read_text())
    assert "g1" not in state["results"]


def test_rerun_integer_from_and_default_zero(tmp_path):
    make_source_run(tmp_path)
    rc, data, out, err = do_rerun(tmp_path, "--from", "3")
    assert rc == 0, out + err
    assert data["from"] == {"selector": "3", "step_index": 3,
                            "snapped_to_segment": False}
    assert data["seeded_results"] == ["a", "b", "c", "g1"]
    assert data["confirm_steps"] == [
        {"step_index": 3, "checkpoint_type": "orchestrator_node", "node": "fin"}]
    # no --from -> full replay from step 0 on the frozen inputs
    rc2, data2, *_ = run("rerun", "--runs-dir", str(tmp_path / "runs"),
                         "--manifest", str(tmp_path / "manifest.json"),
                         "--source-run", "20260610T100000Z-source")
    assert rc2 == 0
    assert data2["from"] == {"selector": "0", "step_index": 0,
                             "snapped_to_segment": False}


def test_rerun_requires_manifest_hash_equality(tmp_path):
    # the recorded run is sha256:h1; the fresh compile is sha256:h2 -> hard
    # stop, exit 1, and NO new run dir is minted
    make_source_run(tmp_path)
    rc, data, out, err = do_rerun(tmp_path, "--from", "c", manifest_hash="sha256:h2")
    assert rc == 1
    assert "manifest_hash equality" in data["error"]
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == [
        "20260610T100000Z-source"]


def test_rerun_beyond_recorded_progress_fails(tmp_path):
    # the source only committed through step 0 (step_index 1) — a from-point
    # past it has no recorded results to seed
    make_source_run(tmp_path, step_index=1, results={"a": {"x": 1}, "b": {"y": 2}})
    rc, data, *_ = do_rerun(tmp_path, "--from", "2")
    assert rc == 1
    assert "step_index 1" in data["error"]
    rc, data, out, err = do_rerun(tmp_path, "--from", "1")
    assert rc == 0, out + err  # from == recorded progress is legal (resume-like)


def test_rerun_missing_recorded_result_fails(tmp_path):
    # progressed far enough, but a pre-from produced node was never stored
    results = {k: v for k, v in RERUN_RESULTS.items() if k != "b"}
    make_source_run(tmp_path, results=results)
    rc, data, *_ = do_rerun(tmp_path, "--from", "c")
    assert rc == 1
    assert "'b'" in data["error"]
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == [
        "20260610T100000Z-source"]


def test_rerun_unknown_from_selector_fails_listing_steps(tmp_path):
    make_source_run(tmp_path)
    rc, data, *_ = do_rerun(tmp_path, "--from", "zz")
    assert rc == 1
    assert "matches no manifest step" in data["error"]
    assert "segment[a,b]" in data["error"] and "gate:g1" in data["error"]
    rc, data, *_ = do_rerun(tmp_path, "--from", "9")
    assert rc == 1 and "out of range" in data["error"]


def test_rerun_pre_shape_source_state_fails(tmp_path):
    run_dir, _ = init_run(tmp_path, "20260610T100000Z-source")
    (run_dir / "run-state.json").write_text(json.dumps(
        {"manifest_hash": "sha256:h1", "step_index": 2, "results": {}}))
    rc, data, *_ = do_rerun(tmp_path, "--from", "1")
    assert rc == 1
    assert "pre-shape" in data["error"]


def test_rerun_run_is_resumable_via_latest(tmp_path):
    # a crashed rerun is offered for RESUME exactly like any other run, while
    # the finished source stays filtered out by --steps
    make_source_run(tmp_path)
    rc, data, *_ = do_rerun(tmp_path, "--from", "c")
    assert rc == 0
    rc, data, *_ = run("latest", "--runs-dir", str(tmp_path / "runs"),
                       "--manifest-hash", "sha256:h1", "--steps", "4")
    assert rc == 0 and data["found"] is True
    assert data["run_id"] == "20260610T200000Z-rerun"
    assert data["step_index"] == 2


# --------------------------------------------------------- project / report

def make_played_run(tmp, run_id):
    """One full run: init -> inputs -> two steps -> complete."""
    run_dir, _ = init_run(tmp, run_id, "sha256:h1", "--profile", "lite")
    (run_dir / "in.json").write_text(json.dumps({"diff_path": "/d.cat"}))
    rc, *_ = run("record-inputs", "--run-dir", str(run_dir), "--inputs-file", "in.json")
    assert rc == 0
    for i, results in enumerate([{"a": {"x": 1}}, {"a": {"x": 1}, "g1": {"choice": "approve"}}]):
        (run_dir / "s.tmp").write_text(json.dumps(
            {"manifest_hash": "sha256:h1", "step_index": i + 1,
             "inputs": {"diff_path": "/d.cat"}, "results": results}))
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
