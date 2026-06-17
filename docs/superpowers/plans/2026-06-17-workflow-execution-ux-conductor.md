# Workflow Execution UX — "Conductor" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a running `/workflow` feel like a colleague conducting the work — announce intent, show a roadmap with a "you are here" cursor, synthesize rich recaps, frame gates with a conductor's voice over invariant evidence, and hide all orchestration plumbing — backed by optional authored human names carried in the manifest but excluded from `manifest_hash`.

**Architecture:** Two layers. (1) **Presentation** lives entirely in the dispatcher Orchestrator Skill `catalog/skills/workflow/SKILL.md` (prose contract — no script behavior changes). (2) **Structure** adds an optional free-text `name` to the node/gate grammar (`workflow-schema.json`), which `compile-workflow` resolves into a per-step `label` (and per-segment `node_labels` map) stamped into the manifest but stripped before hashing, with a deterministic `humanize_id` fallback for unlabeled ids. The catalog's own defs are backfilled with authored names. During-segment liveness is explicitly out of scope (a blocking segment with no on-disk heartbeat — see the design spec §3).

**Tech Stack:** Python 3.11+ (`pyyaml`, `jsonschema`, `pytest`); JSON Schema (Draft 2020-12); the dispatcher skill is Markdown prose. Reference design spec: `docs/superpowers/specs/2026-06-17-workflow-execution-ux-design.md`.

## Global Constraints

Every task's requirements implicitly include these (copied from `CLAUDE.md` and the design spec):

- **Edit source only.** Plugin components live in `catalog/`; the schema source is `templates/references/workflow-schema.json`. **NEVER hand-edit `.claude/`** — it is generated/gitignored and overwritten on reconcile.
- **Reconcile before e2e tests.** After editing `catalog/` or `templates/`, run `catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog` — the `test_real_*` e2e tests resolve components from the runtime `.claude/`.
- **Authored field is `name`; computed manifest key is `label`.** `name` is the optional author input on a node/gate/over-entry; `label` is the resolved per-step output (`name` or `humanize_id(id)`). Never collapse the two; never call the authored field `label` or the step key `name`.
- **Labels are in the manifest but OUT of `manifest_hash`.** A `name`/`label`/`node_labels` change must never alter `manifest_hash`, never enter `node_fingerprints`, and never invalidate a resumable run.
- **Determinism preserved.** Same inputs → byte-identical compiled output (`test_deterministic`). All labels are deterministic (id-only humanization or static authored strings). Conductor narration is ephemeral — printed, never journaled, never written to run-state.
- **Gate approval integrity.** With `gate.present` declared, evidence renders MECHANICALLY in declared order with no synthesis; the recorded gate choice (`results[gate.id].choice`) stays the author-declared label verbatim. New framing layers AROUND the evidence core, never mutates it.
- **`name` is cosmetic, never a join key.** `${...}` refs, `depends_on`, gate `after`, loop `body`/`carry`, `output.from`, and `present:` paths key off `id` only. `name` is free text (no `pattern`) and unreferenced.
- **Closed grammar stays closed.** Node and gate objects keep `additionalProperties: false`; add `name` as a declared property — do not relax `additionalProperties`.
- **Tests are hermetic, test-first, `tmp_path`.** Build a throwaway world, pass roots as flags, assert on JSON output and on-disk state. No new `conftest.py` — reuse each file's in-module helpers.
- **Conventional Commits, PR not push.** Commit scope `workflow`; land via PR on branch `workflow-conductor-ux` (already created), never direct-push to `main`. End commit messages with the `Co-Authored-By` trailer.
- **Full suite (what CI runs):** `python3 -m pytest catalog/scripts/tests/ scripts/tests/ hooks/tests/ -v`.

Canonical names (use these exact identifiers across all tasks):

| Concept | Name | Where |
|---|---|---|
| Authored display label (node/gate/over-entry) | `name` | `workflow-schema.json` node + gate `properties`; def YAML |
| Resolved per-step display label | `label` | each `manifest["steps"][i]["label"]` |
| Per-segment id→label map | `node_labels` | each segment step's `node_labels` |
| Humanization helper | `humanize_id(s)` | `compile-workflow`, module-level |
| Label-stripped hash input | `hashed_manifest` | `compile-workflow`, before `manifest_hash` |

`humanize_id` transform (pin exactly): `s.replace("_", " ").replace("-", " ").strip().title()` → `fin`→`Fin`, `fin_review`→`Fin Review`, `approve_plan`→`Approve Plan`.

---

### Task 1: Schema — optional `name` on nodes and gates

**Files:**
- Modify: `templates/references/workflow-schema.json` (node items `properties` ~line 54; gate items `properties` ~line 110)
- Test: `catalog/scripts/tests/test_validate_workflow.py`

**Interfaces:**
- Produces: an optional `name` string property on node items and gate items of the workflow schema. No `pattern`, not in `required`. Consumed by Task 2 (compiler resolves it to `label`), Task 4 (defs author it). `additionalProperties: false` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `catalog/scripts/tests/test_validate_workflow.py` (reuse the in-file `run`, `write_wf`, `VALID` helpers; `run(path)` returns `(rc, data, stderr)`):

```python
def test_node_name_accepted_by_schema(tmp_path):
    body = VALID.replace(
        "    agent: some-agent\n    prompt: do a\n",
        "    agent: some-agent\n    name: Do The Thing\n    prompt: do a\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert rc == 0
    assert data["pass"] is True
    assert not any("name" in i["location"] for i in data["issues"] if i["severity"] == "block")

GATE_NAME_OK = VALID + """\
gates:
  - id: g1
    name: Plan approval
    after: b
    type: human_approval
    prompt: approve?
"""

def test_gate_name_accepted_by_schema(tmp_path):
    rc, data, _ = run(write_wf(tmp_path, GATE_NAME_OK))
    assert rc == 0
    assert data["pass"] is True

def test_unknown_node_key_still_blocks(tmp_path):
    # additionalProperties:false must remain intact: a typo'd key is a hard block.
    body = VALID.replace(
        "    agent: some-agent\n    prompt: do a\n",
        "    agent: some-agent\n    nme: typo\n    prompt: do a\n")
    rc, data, _ = run(write_wf(tmp_path, body))
    assert any(i["severity"] == "block" and i["location"].startswith("schema:nodes/")
               for i in data["issues"])
```

> If `VALID`'s node `a` block differs from the `.replace()` anchor above, adjust the anchor to match the actual `VALID` text in the file (read it first). The intent: add a `name:` line under node `a`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest catalog/scripts/tests/test_validate_workflow.py -k "name or unknown_node_key" -v`
Expected: `test_node_name_accepted_by_schema` and `test_gate_name_accepted_by_schema` FAIL (a `schema:nodes/0`/`schema:gates/0` block: "Additional properties are not allowed ('name' …)"); `test_unknown_node_key_still_blocks` PASSES already (it documents the invariant to preserve).

- [ ] **Step 3: Add the `name` property to the node items**

In `templates/references/workflow-schema.json`, in the node `items.properties` block, add a comma after the `id` property line and insert immediately below it:

```json
          "name": { "type": "string", "description": "Optional free-text human-readable display label for this node (progress roadmap, cursor, recaps). Cosmetic — not an identifier, does not obey the id pattern, never referenced by ${...}, and excluded from manifest_hash." },
```

Leave `"required": ["id"]` and `"additionalProperties": false` unchanged.

- [ ] **Step 4: Add the `name` property to the gate items**

In the gate `items.properties` block, add a comma after the gate `id` property line and insert below it:

```json
          "name": { "type": "string", "description": "Optional free-text human-readable display label for this gate (rendered in the approval prompt header). Cosmetic; not an identifier and never referenced." },
```

Leave `"required": ["id", "after", "type"]` and `"additionalProperties": false` unchanged.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest catalog/scripts/tests/test_validate_workflow.py -v`
Expected: all PASS (the new three plus the pre-existing suite — the in-repo `MICROSKILLS_TEMPLATES_ROOT` env in the test file makes it read the edited source schema directly, no reconcile needed).

- [ ] **Step 6: Commit**

```bash
git add templates/references/workflow-schema.json catalog/scripts/tests/test_validate_workflow.py
git commit -m "feat(workflow): allow optional human-readable name on nodes and gates

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Compiler — resolve `name` into per-step `label`, keep `manifest_hash` stable

**Files:**
- Modify: `catalog/scripts/compile-workflow`
  - new `humanize_id` helper (module level, near `_expand_suffix` ~line 268)
  - checkpoint step assembly (~lines 2271–2321): stamp `chk["label"]`, `gstep["label"]`
  - segment step assembly (~lines 2397–2454): stamp `manifest_step["label"]`, `manifest_step["node_labels"]`
  - `manifest_hash` computation (~lines 2562–2565): strip labels on a deep copy before hashing
- Test: `catalog/scripts/tests/test_compile_workflow.py`

**Interfaces:**
- Consumes: the node/gate `name` property from Task 1; the over-entry `{item, name}` map (desugars onto the generated sibling node's `name` with no `expand_static_fanout` change — `name` is not in the illegal-extras set `{id, expand, inputs_each}`).
- Produces:
  - `humanize_id(s: str) -> str` — deterministic, id-only.
  - Every `manifest["steps"][i]` carries `"label": str` (segments: ` & `-join of per-node labels; checkpoints/gates: the node/gate `name` or `humanize_id(id)`).
  - Every segment step carries `"node_labels": {node_id: label}`.
  - `manifest_hash` is identical whether or not `name`/`label`/`node_labels` are present. Consumed by Task 3 (dispatcher reads `label`/`node_labels`).

- [ ] **Step 1: Write the failing tests**

Add to `catalog/scripts/tests/test_compile_workflow.py` (reuse in-file `run`, `make_flow`, `_manifest`):

```python
ORCH_LABELED = """\
version: 1
name: orch-label
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: fin
    delegation: orchestrator
    depends_on: [a]
    name: Finalize Everything
    prompt: finalize ${a.output.x}
"""

def test_node_name_recorded_as_step_label(tmp_path):
    make_flow(tmp_path, "orch-label", ORCH_LABELED)
    rc, data, out, err = run(tmp_path, "orch-label")
    assert rc == 0, err
    m = _manifest(tmp_path, "orch-label")
    chk = next(s for s in m["steps"] if s.get("checkpoint_type") == "orchestrator_node")
    assert chk["label"] == "Finalize Everything"

def test_unlabeled_id_gets_humanized_label(tmp_path):
    body = ORCH_LABELED.replace("    name: Finalize Everything\n", "") \
                       .replace("id: fin", "id: fin_review") \
                       .replace("${a.output.x}", "${a.output.x}")  # ref unchanged
    body = body.replace("depends_on: [a]\n    prompt: finalize",
                        "depends_on: [a]\n    prompt: finalize")  # no-op clarity
    make_flow(tmp_path, "humanize", body)
    rc, data, out, err = run(tmp_path, "humanize")
    assert rc == 0, err
    m = _manifest(tmp_path, "humanize")
    chk = next(s for s in m["steps"] if s.get("checkpoint_type") == "orchestrator_node")
    assert chk["label"] == "Fin Review"

def test_manifest_hash_stable_when_only_name_changes(tmp_path):
    plain = """\
version: 1
name: hash-label
nodes:
  - id: a
    agent: ag
    prompt: do a
  - id: fin
    delegation: orchestrator
    depends_on: [a]
    prompt: finalize ${a.output.x}
"""
    make_flow(tmp_path / "one", "hash-label", plain)
    rc1, d1, _, e1 = run(tmp_path / "one", "hash-label", "--explain")
    assert rc1 == 0, e1
    labeled = plain.replace("    prompt: finalize ${a.output.x}\n",
                            "    name: Finalize\n    prompt: finalize ${a.output.x}\n")
    make_flow(tmp_path / "two", "hash-label", labeled)
    rc2, d2, _, e2 = run(tmp_path / "two", "hash-label", "--explain")
    assert rc2 == 0, e2
    assert d1["manifest_hash"] == d2["manifest_hash"]

def test_segment_node_labels_map_present(tmp_path):
    make_flow(tmp_path, "orch-label", ORCH_LABELED)
    rc, data, out, err = run(tmp_path, "orch-label")
    assert rc == 0, err
    m = _manifest(tmp_path, "orch-label")
    seg = next(s for s in m["steps"] if s["kind"] == "segment")
    assert seg["node_labels"]["a"] == "A"  # humanize_id('a')
```

> Verify `ORCH`-style helpers compile (node `fin` must reference `a` so the edge exists). If the file already has an `ORCH` constant, prefer reusing it over `ORCH_LABELED`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest catalog/scripts/tests/test_compile_workflow.py -k "label or humaniz or node_labels" -v`
Expected: FAIL — `KeyError: 'label'` / `KeyError: 'node_labels'` (the manifest steps carry no such keys yet). `test_manifest_hash_stable_when_only_name_changes` may currently PASS (no `name` → no diff) but will guard the hash-strip once labels exist.

- [ ] **Step 3: Add the `humanize_id` helper**

In `catalog/scripts/compile-workflow`, near `_expand_suffix` (~line 268), add:

```python
def humanize_id(s):
    """Deterministic id -> human-readable display label fallback.

    snake/kebab -> Title Case. id-only, no env -> stays deterministic so the
    label never perturbs byte-identical output. Used only when a node/gate
    declares no authored `name`.
    """
    return s.replace("_", " ").replace("-", " ").strip().title()
```

- [ ] **Step 4: Stamp `label` on checkpoint and gate steps**

In the partition loop (~lines 2271–2321), after each `chk = {...}` is constructed in BOTH the `nested_workflow` and `orchestrator_node` branches, add (with `node` and `nid` in scope):

```python
            chk["label"] = node.get("name") or humanize_id(nid)
```

In the gate branch, after `gstep = {"kind": "checkpoint", "checkpoint_type": "gate", "gate": g}` (with `g` in scope), add:

```python
                gstep["label"] = g.get("name") or humanize_id(g["id"])
```

- [ ] **Step 5: Stamp `label` and `node_labels` on segment steps**

In the segment branch of step assembly (~lines 2397–2454), after `manifest_step = {...}` is built (with `node_by_id` and `step["nodes"]` in scope), add:

```python
            _seg_labels = {nid: (node_by_id[nid].get("name") or humanize_id(nid))
                           for nid in step["nodes"]}
            manifest_step["label"] = " & ".join(_seg_labels[nid] for nid in step["nodes"])
            manifest_step["node_labels"] = _seg_labels
```

- [ ] **Step 6: Exclude labels from `manifest_hash`**

`copy` is already imported (used in `expand_static_fanout`). Immediately before the `manifest_hash = hashlib.sha256(...)` computation (~line 2562), insert and then hash the stripped copy:

```python
    hashed_manifest = copy.deepcopy(manifest)
    for _s in hashed_manifest["steps"]:
        _s.pop("label", None)
        _s.pop("node_labels", None)
        _g = _s.get("gate")
        if isinstance(_g, dict):
            _g.pop("name", None)   # authored gate name rides inside the stored gate dict
    manifest_hash = hashlib.sha256(
        json.dumps({"manifest": hashed_manifest, "node_fingerprints": node_fingerprints},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
```

Do **not** add `name`/`label`/`node_labels` to the `fp` dict in `node_fingerprints` (~lines 2514–2528). The full labeled `manifest` remains what `out_manifest = dict(manifest)` writes.

- [ ] **Step 7: Reconcile the hash-recipe test**

The existing `test_manifest_hash_excludes_itself_and_schema_sha` (~lines 1184–1204) recomputes the hash from the on-disk manifest and will now mismatch (labels are on disk but outside the fold). Update its recompute block to strip the same fields before hashing:

```python
    m = {k: v for k, v in on_disk.items()
         if k not in ("manifest_hash", "schema_sha256", "schema_source")}
    for s in m["steps"]:
        s.pop("label", None)
        s.pop("node_labels", None)
        g = s.get("gate")
        if isinstance(g, dict):
            g.pop("name", None)
    # ... existing node_fingerprints rebuild + sha256 recompute, now matches.
```

> Match the variable names already used in that test (`on_disk`/`m`/`node_fps`). The only change is the new `for s in m["steps"]: …` strip loop.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python3 -m pytest catalog/scripts/tests/test_compile_workflow.py -v`
Expected: all PASS, including `test_deterministic`, `test_phase_group_manifest_hash_unchanged`, and the reconciled hash-recipe test.

If a `test_real_*` or golden test fails **only** because a gate/step dict gained the additive `label` (or a `node_labels` key) — i.e. a deep-equality assertion — update that expectation to include the new key. Do NOT change any `segments`/`checkpoints` count, `sequence` list, node id, or `manifest_hash` assertion; if one of those moved, stop — the change is wrong.

- [ ] **Step 9: Commit**

```bash
git add catalog/scripts/compile-workflow catalog/scripts/tests/test_compile_workflow.py
git commit -m "feat(workflow): stamp resolved step labels into the manifest, out of the hash

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Dispatcher — the conductor voice (presentation, prose contract)

**Files:**
- Modify: `catalog/skills/workflow/SKILL.md` (all eight change-sites below)

**Interfaces:**
- Consumes: `manifest["steps"][i]["label"]` and segment `node_labels` (Task 2); `manifest.description`, `manifest.gate_mode`, `manifest.steps` (existing).
- Produces: no script/IO/manifest change — a pure prose rework. Verified by re-running the suite (must stay green by construction) plus a sample compile + manifest read + trace-through.

This task changes **only narration**. Every Bash command (`compile-workflow`, `run-journal latest|init|record-inputs|append`, `normalize-input`, `check-step-io`, `run-step`) stays byte-identical in flags and argv. The four-key run-state, the fail-loud IO gate, the atomic commit ordering, the gate evidence core, the recorded-choice-verbatim rule, and the SECURITY guidance are all preserved. Apply the edits in order:

- [ ] **Step 1 — Silence the Setup plumbing (5.4).** In `## Setup` (lines 35–143), add a directive right after the header (before step 1):

  > Setup commands are plumbing — run them without echoing command lines, JSON output, run-ids, manifest hashes, byte sizes, or `.cat` paths. On success they are silent; only their plain-language *outcome* surfaces (via the opening beat below and the per-step recaps). On error, surface the error readably and stop.

  Keep the five Bash invocations (steps 3, 5, 6, 7, 8) and all SECURITY guidance (lines 113–134) byte-identical. The resume OFFER (lines 75–89) stays, but phrased in the conductor's voice.

- [ ] **Step 2 — Add the opening announcement + roadmap (5.1).** Insert a new beat **after Setup step 6 (Mint the run, ~line 101) and before step 7 (Gather inputs, ~line 102)** — the manifest is loaded and the run is minted here, and this is the main loop (never a segment):

  > **### Announce the run (conductor opening)**
  >
  > Print one opening beat — the only place a watcher learns what the whole run will do:
  > - **Title:** `🛠  <name> — <manifest.description>`. If `manifest.gate_mode == "auto"`, append ` (headless — gates take their declared defaults)`.
  > - **Roadmap:** one compact line per `manifest.steps[i]`, numbered `1..M`, each showing the step's label (resolved per Step 3 below), tagging human gates with `⏸ you decide` and nested workflows with `▸ nested`. A scannable list, not prose.
  > - **Inputs:** if inputs will be gathered, name them in one line.
  >
  > Ephemeral: printed once, never journaled, never written to run-state. (You MAY mirror the Preflight render logic at lines 160–165 for consistency, but do not merge the modes — Preflight halts before gathering; this opening proceeds.)

- [ ] **Step 3 — Rework the per-step header into a you-are-here cursor (5.1).** Replace the header rule at lines 308–317. Rewrite the note that says *"the manifest has no label field"* (line 310) — it now does. New rule:

  > **Before running each step, print the cursor:** `▶ Step {g}/{T} · {label}`, advancing along the announced roadmap, where `{g}/{T}` is the GLOBAL position (see nesting, Step 7).
  >
  > Resolve `{label}`: (1) the step record's `label` field (the compiler always stamps one — authored `name` or a humanized id); (2) if absent, fall back to the existing kind→label synthesis (segment → `plan`→"Plan", a loop → "Implement & evaluate (loop)"; gate → its label; orchestrator_node → its id as an action; nested_workflow → "<child> (nested workflow)").
  >
  > For a multi-node segment whose stamped `label` is a long ` & `-joined string, you MAY use the concise kind→label synthesis for the header and save the per-dimension names (the step's `node_labels`) for the recap. A skipped step still gets a cursor line, marked `(skipped)`.

- [ ] **Step 4 — Rich recap = synthesis of the returned output (5.2).** Replace the recap rubric at lines 404–417:

  > 4. **Recap the segment as a conductor** — when it returns, brief the user on what it produced and what it means next, synthesizing `step.produces` + the stored `results`. A real briefing, not a status line: say what was accomplished and surface the judgment calls / what's worth attention (a plan's name + shape; the files written and what they are; a loop's verdict + how it got there + open issues). For a review/fan-out segment, name the dimensions from the step's `node_labels` (e.g. "across User-value completeness, Edge-case coverage, NFRs, Testability") and summarize the findings.
  >
  > **Guardrails (unchanged):** never paste raw JSON, plan-file contents, or object arrays — synthesize them. A `null` produced node (guarded/skipped) → say so in one clause, don't invent output. On a fail-loud error, skip the recap and report the failure.
  >
  > **Loop segments** (`step.is_loop`): summarize the journey, not each round — the round count (`<returned>.loop.rounds` when the step declares `on_exhaust: escalate`, else `<returned>.__rounds`) and the final verdict; an extend re-run names the extension ("extension 1: 3 more rounds — …").
  >
  > Ephemeral voice — printed, never journaled (the journal `--label` at line 347 stays the mechanical step-record label).

- [ ] **Step 5 — Silence the 3-move checkpoint, keep failures loud (5.4).** In the post-step checkpoint (lines 319–366), add a directive: on the SUCCESS path, moves 1–3 produce NO human-facing output (no tmp path, no "IO check passed", no byte sizes, no commit confirmation) — the signal a step landed is the recap (Step 4) plus the cursor advancing. The FAILURE path stays LOUD: a non-zero `check-step-io` surfaces the contract failure in plain language ("Step N's output failed its contract: <reason> — stopping; it will re-run on resume") and still stops without committing and stamps `--mark-failed-step`. Keep every Bash call, the four required run-state keys, the candidate/commit ordering, and "never re-synthesize the failed output yourself" verbatim.

- [ ] **Step 6 — Gate: conductor framing over a deterministic evidence core (5.3).** Restructure the gate block (lines 431–479) into two explicit layers:

  > **(a) Conductor framing (ephemeral, may vary run to run):** a brief intro — "We've reached the *<gate label>* gate. Here's what was produced and what I'm asking you to decide." — and a one-line framing of the choice after the evidence. This layer NEVER alters, reorders, summarizes, or substitutes the evidence below.
  >
  > **(b) Evidence core (UNCHANGED — approval-integrity invariant):** keep VERBATIM the `gate.present` MECHANICAL render (declared order, `**<last segment>**: <value>` scalar/fenced-json, `{read_file:}` Read-and-fence, `(not produced)` null), the no-`present` output rubric, and the `AskUserQuestion` mechanics (prompt, options default confirm/stop, per-option consequence descriptions). Use the gate's `label` in the framing header. The auto-mode line `Gate <id>: auto — taking declared default '<default>'` and the recorded choice label stay verbatim.

- [ ] **Step 7 — Cursor survives nesting (5.1).** In the `nested_workflow` checkpoint (lines 561–607), thread a display-only cursor context into the child re-entry so the child's headers continue the parent journey (pass the current global ordinal / a `{parent}.{child}` breadcrumb; the child renders `Step {g}/{T}` or a dotted form instead of restarting at `Step 1/{child M}`), and make the child's opening (Step 2) an "entering nested workflow *<child>*" beat under the parent roadmap rather than a fresh top-level announcement. Keep ALL mechanics verbatim: the child still mints its OWN run dir and records its own inputs (run-state isolation), the depth ≤ 1 bound, the resolve/cross-check, and auto/pickup mode-inheritance flags. The cursor is ephemeral display state — it must NOT touch run-state, `manifest_hash`, or committed bytes. Upgrade step 3's "short recap" to a conductor recap (Step 4).

- [ ] **Step 8 — Conductor finish (5.4).** Rework `## Finish` (lines 609–614): run the `run-journal append --event run_complete` Bash call silently (plumbing); keep reporting `results[<manifest.output.from node>]` when set. Turn the one-line wrap-up into a conductor sign-off that closes the journey announced at the opening (workflow name, outcome, where the result landed, "all N steps complete"). Demote the `run-journal report` mention to an optional "for a full timeline, run …" aside. Keep the guardrail "the per-segment recaps already covered the play-by-play — don't re-summarize each segment here."

- [ ] **Step 9 — Verify Rerun/Pickup degrade sensibly.** Confirm the Rerun (lines 186–240) and Pickup (lines 242–304) walks inherit the new voice and that the opening roadmap/cursor reflect *resuming partway* (start at `i = from.step_index` / `i = step_index`, not Step 1). The mechanical `Gate <id>: replaying recorded choice '<choice>'` line stays verbatim (may get light framing; the recorded choice is unchanged).

- [ ] **Step 10: Verify no script contract was disturbed**

Run: `catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog` then `python3 -m pytest catalog/scripts/tests/ scripts/tests/ hooks/tests/ -v`
Expected: GREEN — the suite covers the scripts, not the skill prose, so a pure-voice edit keeps it green. Then read `catalog/skills/workflow/SKILL.md` end-to-end against `catalog/workflow-defs/microskill-create/.compiled/manifest.json`: confirm the cursor reads `manifest.steps[i].label`, the recap can reach `node_labels`, the gate framing wraps (not replaces) the evidence core, and no Bash argv or recorded-choice label changed.

- [ ] **Step 11: Commit**

```bash
git add catalog/skills/workflow/SKILL.md
git commit -m "feat(workflow): rework the dispatcher into a conductor voice

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Backfill authored `name` across catalog defs

**Files (all `catalog/workflow-defs/<name>/WORKFLOW.yaml`):**
- `refine-requirements`, `technical-design`, `review-changes`, `backlog-publication`, `ux-design`, `develop-product-backlog`, `microskill-create`, `workflow-create`, `decompose-monolith-orchestrator`

**Interfaces:**
- Consumes: Task 1 (schema allows `name`), Task 2 (compiler resolves it to `label`).
- Produces: authored `name` on cryptic nodes, declared gates, and `over:` dimensions, so the catalog dogfoods readable names. The over-entry conversion (bare string → `{item, name}` map) must keep the generated sibling id and fan-in key identical (suffix = item with `-`→`_`). Do NOT touch synthetic/compiler-emitted gates (`loop_exhaust`).

The exact `name` values to add (each `name:` goes on the line below the node/gate `id:`):

- [ ] **Step 1 — `refine-requirements`.** Nodes: `revise_req`→"Apply prior findings to the requirements"; `critique`→"Adversarial completeness critic panel"; `collect_req`→"Collect critic findings"; `synth_req`→"Synthesize critic verdict"; `present_refined`→"Present refined requirements for review"; `extract_claims`→"Extract gap-closure claims"; `assign_refuters`→"Assign closure refuter seats"; `refute_closure`→"Refute each gap-closure claim"; `tally_closures`→"Tally closure refute votes"; `triage_reopened`→"Triage reopened gap closures"; `assemble_req_evidence`→"Consolidate requirements evidence ledger". Gate `approve_requirements`→"Approve requirements". Convert the `critique` `over:` (line ~175) to object form:

```yaml
    expand:
      over:
        - { item: req-user-value, name: "User-value completeness" }
        - { item: req-edge-cases, name: "Edge-case & failure-mode coverage" }
        - { item: req-nfr, name: "Non-functional requirements" }
        - { item: req-testability, name: "Testability & acceptance criteria" }
```

- [ ] **Step 2 — `technical-design`.** Nodes: `synthesize_winner`→"Pick winning architecture"; `author_hld`→"Author the high-level design"; `review_hld`→"Adversarial HLD review panel"; `collect_hld`→"Collect HLD findings"; `synth_hld`→"Synthesize HLD loop verdict"; `refute_hld`→"Refute surviving HLD findings"; `synth_final_hld`→"Synthesize refute-verified HLD verdict"; `lld`→"Author per-component low-level designs"; `review_lld`→"Review LLDs across dimensions"; `collect_lld`→"Collect LLD findings"; `refute_lld`→"Refute LLD findings"; `synth_lld`→"Synthesize initial LLD verdict"; `fix_lld`→"Fix red LLD findings"; `recheck_lld`→"Re-verify fixed LLD findings"; `synth_lld_final`→"Synthesize final LLD verdict"; `triage_lld`→"Triage remaining LLD blockers"; `finalize_design`→"Assemble design stage evidence". Gates: `confirm_architecture`→"Confirm winning architecture"; `confirm_hld`→"Confirm high-level design"; `approve_hld`→"Approve refute-verified design". `over:` names — `candidate`: simplicity-first→"Simplicity-first", scale-first→"Scale-first", delivery-first→"Delivery-first"; `judge`: requirements-fit→"Requirements-fit judge", simplicity→"Simplicity judge", evolvability→"Evolvability judge"; `review_hld` (already mixed-form — add `name` to each entry): internal-consistency→"Internal consistency" (keep its `inputs:`), requirements-fidelity→"Requirements fidelity", nfr-coverage→"NFR coverage", feasibility→"Technical feasibility", security-architecture→"Security architecture"; `review_lld`: lld-fidelity→"LLD fidelity", lld-completeness→"LLD completeness", lld-feasibility→"LLD feasibility".

- [ ] **Step 3 — `review-changes`.** Convert `over:` (line ~67) to object form: correctness→"Correctness", security→"Security", performance→"Performance". (Node ids are already readable; optionally `synthesize`→"Synthesize prioritized review".)

- [ ] **Step 4 — `backlog-publication`.** Nodes: `triage_coverage`→"Triage uncovered traceability links"; `refute`→"Refute INVEST story-quality claims"; `tally`→"Tally INVEST refute votes"; `triage_quality`→"Triage confirmed INVEST violations"; `assemble_evidence`→"Consolidate backlog evidence ledger". Gate `approve_publish`→"Approve backlog publication".

- [ ] **Step 5 — `ux-design`.** Nodes: `pick_winner`→"Pick winning wireframe"; `synth`→"Synthesize heuristic-panel verdict"; `triage_unconverged`→"Triage unconverged wireframe findings"; `final_synthesize`→"Synthesize final UX review"; `finalize`→"Assemble UX stage evidence". `candidate` `over:` (already object-form with `inputs.stance`) — add `name`: task-flow→"Task-flow layout", hub-dashboard→"Hub & dashboard layout", progressive-wizard→"Progressive wizard layout". `panel` `over:` → object form: ux-journey-coverage→"Journey coverage", ux-requirements-fidelity→"Requirements fidelity", ux-flow-consistency→"Flow consistency". Gate `signoff`→"Sign off UX design".

- [ ] **Step 6 — Create-pipeline gates.** `develop-product-backlog` gate `confirm_classification`→"Confirm product classification" (optionally node `backlog_publication`→"Generate & publish backlog"). `microskill-create` gate `approve_plan`→"Approve microskill plan". `workflow-create` gate `approve_plan`→"Approve workflow plan". `decompose-monolith-orchestrator` gate `approve_decomposition`→"Approve decomposition plan".

- [ ] **Step 7 — Validate every edited def**

Run: `for d in refine-requirements technical-design review-changes backlog-publication ux-design develop-product-backlog microskill-create workflow-create decompose-monolith-orchestrator; do echo "== $d =="; catalog/scripts/validate-workflow catalog/workflow-defs/$d/WORKFLOW.yaml; done`
Expected: each reports `pass: true` with no new `block` issues. A `schema:nodes/N` block means a stray `name` landed where the schema/over-form is wrong — fix it.

- [ ] **Step 8 — Confirm over-form parity (ids unchanged)**

Run: `python3 -m pytest catalog/scripts/tests/test_compile_workflow.py -k real -v` (after `catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog`).
Expected: GREEN. If a `test_real_*` gate-dict assertion fails only because a gate dict now carries `name`, update that expectation to include `name` — but any change to a generated node id, `sequence`, `segments`/`checkpoints` count, or `manifest_hash` means the over-form conversion changed a suffix; revert and fix the `item:` value so the suffix matches the old bare string.

- [ ] **Step 9: Commit**

```bash
git add catalog/workflow-defs
git commit -m "feat(workflow): backfill authored human names onto catalog defs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Integration — reconcile runtime, full suite, determinism + voice verification

**Files:**
- No source edits (verification + any golden-test reconcile surfaced here)

**Interfaces:**
- Consumes: Tasks 1–4. Produces: a green full suite, proven determinism, and a verified end-to-end conductor experience.

- [ ] **Step 1: Materialize the runtime**

Run: `catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog`
Expected: the schema, defs, and dispatcher under `.claude/` refresh (plan reports the updated components). Never hand-edit `.claude/`.

- [ ] **Step 2: Full suite**

Run: `python3 -m pytest catalog/scripts/tests/ scripts/tests/ hooks/tests/ -v`
Expected: GREEN. Any remaining red is an additive-`label`/`name` golden expectation not yet reconciled (fix the expectation, never the counts/ids/hash) — or a real regression (stop and investigate per superpowers:systematic-debugging).

- [ ] **Step 3: Prove determinism**

Run: compile a backfilled def twice and diff the compiled output —
`catalog/scripts/compile-workflow refine-requirements && cp -r catalog/workflow-defs/refine-requirements/.compiled /tmp/c1 && catalog/scripts/compile-workflow refine-requirements && diff -r /tmp/c1 catalog/workflow-defs/refine-requirements/.compiled`
Expected: no diff (byte-identical). Confirm `manifest.json` step records carry the authored `label`/`node_labels`, and that `manifest_hash` matches the pre-backfill value for at least one def (labels are out of the hash) — compare against `git show HEAD~4:catalog/workflow-defs/refine-requirements/.compiled/manifest.json` if a committed baseline exists, else assert the hash is unchanged across a name-strip recompile.

- [ ] **Step 4: Verify the conductor experience (voice contract)**

Read `catalog/workflow-defs/microskill-create/.compiled/manifest.json` and `catalog/workflow-defs/refine-requirements/.compiled/manifest.json`. Confirm: every step has a human `label`; gate steps carry `gate.name`; review segments carry `node_labels` with the authored dimension names ("Non-functional requirements", etc.). Trace `catalog/skills/workflow/SKILL.md` against these manifests to confirm the opening roadmap, you-are-here cursor, rich recap, gate framing-over-evidence, silent plumbing, and conductor finish all render coherently and that the determinism/approval invariants in the Global Constraints hold.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin workflow-conductor-ux
gh pr create --title "feat(workflow): conductor-voice execution UX with authored step names" \
  --body "Implements docs/superpowers/specs/2026-06-17-workflow-execution-ux-design.md — see plan docs/superpowers/plans/2026-06-17-workflow-execution-ux-conductor.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: PR opens with a conventional-commit title (squash-merge enforced). Do not merge to `main` directly.

---

## Self-Review

**Spec coverage** (design §5.1–5.5):
- 5.1 Conduct/roadmap/cursor-surviving-nesting → Task 3 Steps 2, 3, 7 (cursor reads Task 2's `label`).
- 5.2 Recap = synthesis → Task 3 Step 4 (uses Task 2's `node_labels`).
- 5.3 Gate framing over evidence core → Task 3 Step 6.
- 5.4 Plumbing silent → Task 3 Steps 1, 5, 8.
- 5.5 Authored names (schema `name` = T1; manifest `label`/`node_labels` + `humanize_id` + hash-exclusion = T2; over-token decouple = T2 desugar + T4 authoring; validate acceptance = T1; backfill = T4) — all covered.
- Design §6 decisions (in-manifest/out-of-hash, optional-name+fallback, no during-work liveness) honored in Global Constraints + T2 + (absence of any engine task). Design §7 determinism boundary enforced as invariants in T2/T3.

**Placeholder scan:** No TBD/TODO. Every code step shows real code; every prose step gives the exact directive/replacement text and anchor lines. The two `.replace()` anchors in test code are flagged to verify against the actual file constants before use.

**Type/name consistency:** Authored field `name` (T1 schema, T4 defs) vs computed `label` (T2 manifest, T3 dispatcher) kept distinct throughout; `humanize_id`, `node_labels`, `hashed_manifest` used identically across tasks; the hash-strip set (`label`, `node_labels`, nested gate `name`) is the same in T2 Step 6 and T2 Step 7.

**Known risk carried into execution:** `test_real_*` golden assertions may need additive-key updates in T2 Step 8 and T4 Step 8 — both steps state the rule (update expectations for additive `label`/`name`; never move counts/ids/hash).
