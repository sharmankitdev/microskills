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


def test_create_profile_validates_clean():
    p = subprocess.run([sys.executable, str(REPO / "catalog" / "scripts" / "validate-microskill"),
                        str(SKILL_ROOT / "synthesize-review" / "MICROSKILL.md"),
                        str(SKILL_ROOT / "synthesize-review" / "profiles" / "create.yaml")],
                       capture_output=True, text=True)
    data = json.loads(p.stdout)
    blocks = [i for i in data.get("issues", []) if i["severity"] == "block"]
    assert not blocks, blocks            # the steps.add fold step must carry NO branching vocab
