import json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RESOLVE = REPO / "catalog" / "scripts" / "resolve-microskill"
SKILL_ROOT = REPO / "catalog" / "microskills"


def _resolve(profile):
    p = subprocess.run([sys.executable, str(RESOLVE), "synthesize-review",
                        "--profile", profile, "--skill-root", str(SKILL_ROOT)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_create_profile_resolves_with_floor_and_carry():
    r = _resolve("create")
    cfg = r["config"]
    names = set(cfg["inputs"].keys())
    assert {"findings", "floor_findings"} <= names          # floor_findings declared
    assert cfg["inputs"]["findings"].get("required") is True
    assert cfg["inputs"]["floor_findings"].get("required") in (False, None)  # optional
    # output schema REPLACED but RETAINS findings (loop carry) + the verdict trio
    props = cfg["output_schema"]["properties"]
    assert "findings" in cfg["output_schema"]["required"]
    assert set(props["verdict"]["enum"]) == {"approve", "comment", "request_changes"}
    assert "blocker_count" in props
    # the fold step is present in the rendered body, floor severities named
    body = r["rendered_skill_body"]
    assert "floor_findings" in body
    assert "pre-confirmed" in body.lower()
    # the fold PRESERVES the floor finding's id (load-bearing: the loop carry + the
    # body's missing-id failure mode both key on it) and nulls line
    assert "keep each floor finding's id" in body.lower()
    assert "set line to null" in body.lower()


def test_create_profile_validates_clean():
    p = subprocess.run([sys.executable, str(REPO / "catalog" / "scripts" / "validate-microskill"),
                        str(SKILL_ROOT / "synthesize-review" / "MICROSKILL.md"),
                        str(SKILL_ROOT / "synthesize-review" / "profiles" / "create.yaml")],
                       capture_output=True, text=True)
    data = json.loads(p.stdout)
    blocks = [i for i in data.get("issues", []) if i["severity"] == "block"]
    assert not blocks, blocks            # the steps.add fold step must carry NO branching vocab


def test_verdict_mapping_covers_floor_and_review_tiers():
    r = _resolve("create")
    body = r["rendered_skill_body"].lower()
    # Pin the EXACT verdict-mapping clauses, not bare tokens (a bare "warn"/"comment"
    # exists independently in the fold step + output schema, so dropping the floor-warn
    # tier from verdict_mapping would slip a token-only check). These substrings fail if
    # the create mapping is swapped for the design mapping (which has no floor tiers).
    assert "deterministic-floor blocker" in body          # floor blocker → request_changes side
    assert "request_changes" in body and "major-severity finding gives request_changes" in body
    assert "deterministic-floor warn survivors give comment" in body   # floor warn → comment (NOT dropped)
    # the fold normalizes floor shape into the common finding fields
    assert "location to the file" in body and "message to the title" in body


def test_base_and_design_unchanged_by_create_profile():
    # The create profile is a NEW file; base/design must resolve exactly as on main —
    # the shared MICROSKILL.md/base.yaml body must carry NONE of the create-only markers.
    # (Token-set guard, not a brittle golden hash: catches any shared-body create-leak —
    # a steps.add fold, a floor input, a floor-tier verdict clause — without breaking on a
    # legitimate future base/design edit. Full byte-identity is proven catalog-wide by the
    # consumer --check / golden CI gates.)
    create_only = ["floor_findings", "deterministic-floor", "pre-confirmed",
                   "keep each floor finding's id"]
    for prof in ("base", "design"):
        body = _resolve(prof)["rendered_skill_body"].lower()
        leaked = [m for m in create_only if m in body]
        assert not leaked, f"{prof} body leaked create-only marker(s) {leaked} — shared body was modified"
