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

---

# Phase 2 — The bookkeeper split

> **Spec:** `docs/superpowers/specs/2026-06-17-workflow-conductor-bookkeeper-design.md`.
> Phase 1 (Tasks 1–5 above) shipped the conductor *voice* + authored names but tried to
> hide plumbing by **instructing terseness** — which the live run disproved (the harness
> renders every main-loop tool call regardless). Phase 2 hides plumbing **structurally**:
> a dedicated locked-down `workflow-bookkeeper` agent runs every deterministic CLI call
> off the main loop; the conductor skill keeps only human-facing narration, segments, and
> gates. Phase 2 supersedes the *mechanism* of Phase 1's §5.4/§9.2; everything else stands.
> Phase 1's Task 5 "open PR" is folded into Phase 2's Task 10 — one PR for the whole branch.

**Goal:** Make a running `/workflow` show only conductor prose + one "working…" beat per
segment + gate questions — no `Bash`/`Write`/`Read` plumbing blocks, no run-ids/paths/argv
in the prose — by delegating all deterministic CLI work to a `workflow-bookkeeper` subagent.

**Architecture:** Two roles. The **conductor** (`catalog/skills/workflow/SKILL.md`, main
loop) owns the opening+roadmap, the `▶ Step` cursor, recaps, the `Workflow` segment calls,
gates (`AskUserQuestion`), orchestrator-node prompt execution, and nested re-entry. The
**bookkeeper** (`catalog/agents/workflow-bookkeeper/AGENT.md`, tools `Bash`/`Read`/`Write`)
owns `compile-workflow`, `run-journal *`, `normalize-input`, `run-step args|eval`,
`check-step-io`, and the run-state `Write`. The conductor dispatches it once per step
boundary (commit-prior + prep-next) via `Agent(subagent_type:"workflow-bookkeeper")` and
parses its fenced-JSON digest. No run-scripts change — the same commands/flags, relocated.

**Tech Stack:** Markdown prose (skill + agent def); Python 3.11+ (`pyyaml`, `pytest`) for
the one new hermetic test. Agents + dispatcher skills are part of the **engine** bundle
(`ENGINE_SUBDIRS = ["scripts","skills","agents","commands"]` in `initialize-harness`), so a
new agent under `catalog/agents/` needs **no `harness.yaml` entry** — it rides the engine and
registers on the next session restart.

## Phase 2 Global Constraints

Every Phase 2 task implicitly includes these (in addition to the Phase 1 Global Constraints):

- **Edit source only** — `catalog/agents/workflow-bookkeeper/AGENT.md` and
  `catalog/skills/workflow/SKILL.md`. Never hand-edit `.claude/`.
- **No run-script changes.** `compile-workflow`, `run-step`, `check-step-io`, `run-journal`,
  `normalize-input` keep byte-identical flags/argv. The bookkeeper calls them exactly as the
  current SKILL.md does — the plumbing prose is *moved verbatim*, not rewritten.
- **Determinism preserved.** On-disk run-state/journal/compiled bytes unchanged (same commands,
  different caller). The full suite (`catalog/scripts/tests/ scripts/tests/ hooks/tests/`) stays
  green; the only new test is the engine-inclusion + frontmatter guard in Task 6.
- **Approval-integrity invariant.** Gate `present` is resolved by the bookkeeper into a
  render-ready ordered payload; the conductor prints it **verbatim** (no reorder/synthesis) and
  layers ephemeral framing around it; the recorded choice stays the author-declared label verbatim.
- **Relay verbatim.** The conductor passes a step's produced result(s) to the bookkeeper's
  `commit` op **verbatim** (no summarization); the bookkeeper merges them into the on-disk
  results map and writes run-state. (Lower transcription risk than today: the conductor relays
  only the *fresh* result, not the whole accumulated map.)
- **Locked toolset.** The bookkeeper declares `tools: Bash, Read, Write` — enforced, so it
  cannot `AskUserQuestion` or launch a `Workflow`. All human/segment actions stay in the conductor.
- **Conventional Commits, PR not push.** Scope `workflow`; branch `workflow-conductor-ux`;
  `Co-Authored-By` trailer.

## The bookkeeper dispatch protocol (canonical interface — all Phase 2 tasks key off this)

The conductor dispatches `Agent(subagent_type: "workflow-bookkeeper", prompt: <one JSON object {op, ...}>)`.
The bookkeeper runs the op's pinned CLI and returns its **final message as a single fenced
` ```json ` digest** (and nothing else). Ops and their digests:

| op | conductor passes | bookkeeper does (pinned CLI) | digest |
|---|---|---|---|
| `open` | `{name, profile?, overrides?, headless_from_args}` | check env `MICROSKILLS_HEADLESS`; `compile-workflow` (gate-mode auto iff headless); read manifest; `run-journal latest …` resume-scan | `{ok, manifest_hash, gate_mode, description, output_from, required_inputs, materialize_inputs, input_defaults, steps:[{i,kind,checkpoint_type,label,is_loop,severity,workflow,conditional}], resume:{found,run_id,run_dir,step_index,failed_step}}` \| `{ok:false,error}` |
| `record` | `{name, manifest_hash, profile?, overrides?, gate_mode, inputs, materialize:[{name,provenance,value}]}` | `run-journal init`; per materialize input write inline content via **Write** then `normalize-input` (SECURITY rules); `run-journal record-inputs` (seeds run-state) | `{ok, run_dir, inputs}` \| `{ok:false,error}` |
| `resume` | `{name, run_dir, mode:"resume"\|"pickup"}` | journal the resume/pickup event; read run-state | `{ok, step_index, failed_step, gate_mode}` |
| `prep` | `{name, run_dir, step, extend?}` | by step kind: `run-step args` (segment, `--extend` iff extend); `run-step eval` + resolve `present` evidence incl. `{read_file}` Reads (gate); `run-step eval` (orch/nested) | per-kind (see Task 6 Step 4) \| `{kind:"done"}` \| `{ok:false,error}` |
| `commit` | `{name, run_dir, step, results:{<nodeid>:<value>}, gate?:{id,choice}, outcome?, label}` | read run-state; merge `results`; **Write** candidate (`step_index=step+1`); `check-step-io --step`; on pass `run-journal append --commit-state`; on fail `run-journal append … --mark-failed-step` | `{ok}` \| `{ok:false, reason, errors}` |
| `fold-guidance` | `{name, run_dir, notes_input, notes, extension_n}` | append notes to the materialized file (copy-if-outside-run_dir) or string input; commit inputs-only run-state | `{ok, inputs}` |
| `finish` | `{name, run_dir}` | `run-journal append --event run_complete --outcome ok` | `{ok}` |
| `fail` | `{name, run_dir, step?, label, mark_failed_step?}` | `run-journal append --event run_error …` | `{ok}` |
| `preflight` | `{name, profile?, overrides?, headless_from_args}` | `compile-workflow --plan --explain` | `{ok, summary}` (full `manifest`+`classification`) \| `{ok:false,error}` |
| `rerun-locate` | `{name, run?}` | `run-journal latest …` (no hash) / read `run-config` | `{ok, run_id, manifest_hash, profile_used, overrides, gate_mode, failed_step}` |
| `rerun-seed` | `{name, source_run, from?}` | `run-journal rerun …` | `{ok, run_dir, from_step_index, snapped, replayed_gates, confirm_steps}` \| `{ok:false,error}` |
| `pickup-locate` | `{name, run?}` | `run-journal latest …` (no hash); read run-config + run-state | `{ok, run_id, manifest_hash, profile_used, overrides, gate_mode, step_index, failed_step}` |

The conductor NEVER issues a raw orchestration CLI call; the bookkeeper NEVER calls
`AskUserQuestion` or `Workflow`. A `{ok:false,error}` / `{ok:false,reason,errors}` digest →
the conductor surfaces it in plain language and stops (the bookkeeper already journaled it
where the op specifies `--mark-failed-step`).

---

### Task 6: Create the `workflow-bookkeeper` agent (execute-mode ops)

**Files:**
- Create: `catalog/agents/workflow-bookkeeper/AGENT.md`
- Create: `catalog/scripts/tests/test_workflow_bookkeeper_agent.py`

**Interfaces:**
- Produces: the `workflow-bookkeeper` agent def (frontmatter `name`, `description`,
  `model: sonnet`, `tools: Bash, Read, Write`) implementing ops `open`, `record`, `resume`,
  `prep`, `commit`, `fold-guidance`, `finish`, `fail` per the protocol table. Consumed by
  Tasks 7–8 (conductor dispatches these ops). The mode ops (`preflight`, `rerun-*`,
  `pickup-*`) are added in Task 9 alongside their consumers.

- [ ] **Step 1: Write the failing test**

Create `catalog/scripts/tests/test_workflow_bookkeeper_agent.py` (mirror the loader pattern in
`test_initialize_harness.py`):

```python
import importlib.machinery, importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
INIT = REPO / "catalog" / "scripts" / "initialize-harness"
AGENT = REPO / "catalog" / "agents" / "workflow-bookkeeper" / "AGENT.md"


def _load_init():
    loader = importlib.machinery.SourceFileLoader("initialize_harness", str(INIT))
    spec = importlib.util.spec_from_loader("initialize_harness", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _frontmatter(path):
    import yaml
    text = path.read_text()
    assert text.startswith("---\n"), "AGENT.md must open with YAML frontmatter"
    fm = text.split("---\n", 2)[1]
    return yaml.safe_load(fm)


def test_bookkeeper_frontmatter_locks_toolset(tmp_path):
    fm = _frontmatter(AGENT)
    assert fm["name"] == "workflow-bookkeeper"
    tools = [t.strip() for t in fm["tools"].split(",")] if isinstance(fm["tools"], str) else fm["tools"]
    assert sorted(tools) == ["Bash", "Read", "Write"], "locked toolset: no AskUserQuestion/Workflow"


def test_bookkeeper_rides_engine_bundle(tmp_path):
    mod = _load_init()
    outs = mod.engine_outputs(REPO / "catalog", tmp_path / ".claude")
    srcs = [str(s) for s, _ in outs]
    assert any(s.endswith("catalog/agents/workflow-bookkeeper/AGENT.md") for s in srcs), \
        "bookkeeper must materialize via the engine bundle (no harness.yaml entry)"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest catalog/scripts/tests/test_workflow_bookkeeper_agent.py -v`
Expected: FAIL — `AGENT.md` does not exist yet (`FileNotFoundError` in `_frontmatter` / the
`endswith` assertion is False).

- [ ] **Step 3: Write the agent frontmatter + role intro**

Create `catalog/agents/workflow-bookkeeper/AGENT.md` starting with:

```markdown
---
name: workflow-bookkeeper
description: Deterministic plumbing worker for the workflow conductor. Runs the pinned orchestration CLI (compile-workflow, run-journal, run-step, check-step-io, normalize-input) and the run-state Write off the main loop, returning a single fenced-JSON digest. Never speaks to the user, never launches a segment — its locked toolset (Bash/Read/Write) cannot. Dispatched once per step boundary by the workflow skill.
model: sonnet
tools: Bash, Read, Write
---

You are the workflow **bookkeeper** — the conductor's deterministic plumbing worker. The
`workflow` skill (the conductor, in the main loop) handles everything the human sees or
decides; you run the orchestration CLI and persist run-state so none of it clutters the
user's transcript.

**You receive ONE JSON object** `{op, ...}` as your task and **return exactly ONE fenced
` ```json ` block** as your final message — the op's digest, nothing else (no prose, no
command echoes). On any CLI non-zero exit, return `{"ok": false, "error": "<readable>"}`
(or `{"ok": false, "reason": ..., "errors": ...}` for `commit`) — do NOT retry, improvise,
hand-assemble args, summarize a result, or repair a failed output. You have no
`AskUserQuestion` and no `Workflow` tool — never attempt human interaction or segment launch.

Pin every command and flag EXACTLY as written below. Node outputs and input values ride only
in files / stdin-free paths, never in argv.
```

- [ ] **Step 4: Write the execute-mode op bodies (move the plumbing verbatim)**

Below the intro, add one `## op: <name>` section per execute-mode op. The CLI in each is
**lifted verbatim** from the current `catalog/skills/workflow/SKILL.md` (read it first; line
refs are the pre-Phase-2 file), reframed from a conductor step into a bookkeeper op. Use this
mapping — copy the cited commands/flags/SECURITY text exactly, then add the digest it returns:

- `## op: open` — from SKILL.md **Setup steps 2–5** (lines 46–96): the env headless check
  (`echo "${MICROSKILLS_HEADLESS:-}"`), `compile-workflow … [--gate-mode auto]` (50–57), read
  the manifest (65–66), `run-journal latest --manifest-hash … --steps …` (71). Build the digest
  from the manifest (`manifest_hash`, `gate_mode`, `description`, `output.from`,
  `required_inputs`, `materialize_inputs`, `input_defaults`, and a `steps[]` list of
  `{i, kind, checkpoint_type, label, is_loop, severity, workflow, conditional}` read from each
  step record) and the resume scan (`resume:{found, run_id, step_index, failed_step}`).
- `## op: record` — from SKILL.md **Setup step 6** (`run-journal init …`, 97–104) + **step 7**
  normalization incl. the **full SECURITY block verbatim** (131–157: inline→Write tool,
  path→single-quote + metachar reject, `normalize-input --value '<path>' --out …`) + **step 8**
  (`Write inputs.tmp` + `run-journal record-inputs …`, 158–166). Digest `{ok, run_dir, inputs}`.
- `## op: resume` — from SKILL.md **Setup step 5 resume branch** (88–94: seed from run-state,
  `run-journal append --event resume --step-index <i>`) and **Pickup step 4** (302–305:
  `--event pickup … --label 'interactive pickup of parked auto run'`) per the `mode` arg.
  Digest `{ok, step_index, failed_step, gate_mode}`.
- `## op: prep` — from SKILL.md **segment step 1** (`run-step args … [--extend]`, 420–444),
  **gate conditional + present** (480–508: `run-step eval` for `when`; resolve each `present`
  entry — scalar→`{kind:"scalar",label,value}`, object/array→`{kind:"json",label,value}`,
  `{read_file:}`→Read the file and return `{kind:"file",label,contents,lang}`), and **orch/nested
  eval** (`run-step eval …`, 590–609 / 631–647). Return the per-kind digest:
  - segment → `{kind:"segment", script, args, label, node_labels, produces, is_loop}`
  - gate → `{kind:"gate", gate:{id,label,prompt,options,severity,default,on_headless,after}, when, skipped, evidence:[…ordered…], gate_mode}`
  - orchestrator_node → `{kind:"orchestrator_node", node, prompt|iterations, skipped, io_schema, gate_mode}`
  - nested_workflow → `{kind:"nested_workflow", node, workflow, profile, child_inputs|iterations, skipped}`
  - `step >= M` → `{kind:"done"}`
- `## op: commit` — from SKILL.md **the three-move checkpoint** (370–405): read the current
  `<run_dir>/run-state.json`, merge the passed `results` into its `results` map, **Write** the
  four-key candidate to `run-state.json.tmp` with `step_index = step+1`, `check-step-io …
  --step <step>`; on exit 0 → `run-journal append --event step_complete --commit-state
  run-state.json.tmp` (+ `--gate/--choice`, `--outcome skipped` as passed) → `{ok:true}`; on
  non-zero → `run-journal append --event run_error … --mark-failed-step <step>`, leave the tmp
  in place → `{ok:false, reason, errors}`.
- `## op: fold-guidance` — from SKILL.md **extend step 1** (557–566): append notes under a
  `## Loop-extension guidance (extension N)` heading to the materialized `notes_input` file
  (copy into `run_dir/run-inputs/` first if it lives outside `run_dir`, update `inputs[name]`),
  or append to the string input; commit the inputs-only run-state (Write tmp → `run-journal
  append --commit-state`, no step advance). Digest `{ok, inputs}`.
- `## op: finish` — SKILL.md **Finish** (689): `run-journal append --event run_complete
  --outcome ok`. Digest `{ok}`.
- `## op: fail` — SKILL.md **stop form** (416): `run-journal append --event run_error
  --step-index <step> --outcome error --label '<reason>' [--mark-failed-step <step>]`. Digest `{ok}`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest catalog/scripts/tests/test_workflow_bookkeeper_agent.py -v`
Expected: PASS (both tests). If `test_bookkeeper_rides_engine_bundle` fails, the file is not
under `catalog/agents/` or `engine_outputs` skips it — confirm the path and that the dir name
is exactly `workflow-bookkeeper`.

- [ ] **Step 6: Commit**

```bash
git add catalog/agents/workflow-bookkeeper/AGENT.md catalog/scripts/tests/test_workflow_bookkeeper_agent.py
git commit -m "feat(workflow): add locked-down workflow-bookkeeper plumbing agent

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Conductor — voice ban-list + Setup delegation

**Files:**
- Modify: `catalog/skills/workflow/SKILL.md` (the `## Setup` section, lines 35–166, and the
  intro at 15–33)

**Interfaces:**
- Consumes: bookkeeper ops `open`, `record`, `resume` (Task 6).
- Produces: a Setup that issues zero raw CLI calls — it dispatches the bookkeeper for
  compile/resume-scan/mint/normalize/record, and keeps announce + input-gather +
  resume-offer (the only human-facing setup work) in the conductor. Establishes the voice
  ban-list consumed by Tasks 8–9.

- [ ] **Step 1: Add the conductor voice ban-list.** Replace the soft directive at SKILL.md
  lines 37–40 ("Setup commands are plumbing — run them without echoing…") with a hard
  ban-list block:

  > **Conductor voice (applies to ALL conductor output, every mode).** You are the conductor;
  > the **bookkeeper** subagent runs the plumbing and its tool calls never reach this
  > transcript. Your prose must never contain: run-ids, manifest hashes; `run_dir` / `.tmp` /
  > `.cat` / `.compiled` / `seg-N.js` paths; CLI command names or argv (`run-step`,
  > `check-step-io`, `run-journal …`, `--commit-state`, `--mark-failed-step`); process phrases
  > ("Committing state", "IO check passed", "Minting the run", "Recording inputs", "Building
  > the segment args", "Resolving … against committed run-state"); raw JSON, byte counts, or
  > schema field names; an internal node id when the step carries a `label`. **Do** emit: plain
  > outcomes ("Saved.", "Ready.", "Done."); the `▶ Step g/T · <label>` cursor; artifact
  > references by purpose **with the user-facing product path** they'd open (a `/tmp/...`
  > output path is fine — it is the user's artifact, not runtime plumbing); recaps that
  > synthesize. A bookkeeper digest with `ok:false` → surface its meaning in plain language and
  > stop (don't paste the JSON).

- [ ] **Step 2: Rework Setup steps 1–5 into the `open` dispatch.** Replace SKILL.md lines
  42–96 with:

  > 1. **Name / profile / overrides** — parse `<name>` (position 1), `<profile>` (slash
  >    position 2 or "with <profile> profile"), `override workflow-config:` clauses, and the
  >    args headless signal (`--gate-mode auto` / `--headless`). No `WORKFLOW.yaml` for `<name>`
  >    → stop and report.
  > 2. **Open the run (bookkeeper).** Dispatch the bookkeeper with
  >    `{op:"open", name, profile, overrides, headless_from_args}`. It compiles, reads the
  >    manifest, and scans for a resumable run. Note the digest's `manifest_hash`, `gate_mode`
  >    (authoritative for the whole run — auto means no `AskUserQuestion` anywhere; the Pickup
  >    exception below still applies), the roadmap fields, and `resume`. `ok:false` → surface
  >    `error` and stop.
  > 3. **Resume offer.** `resume.found` and not auto mode → offer via `AskUserQuestion` in the
  >    conductor's voice ("Looks like a previous run stopped at step {step_index+1}…"; if
  >    `failed_step` is non-null, say that step's last attempt failed and resuming re-runs it).
  >    On resume → dispatch `{op:"resume", name, run_dir, mode:"resume"}`, set `i = step_index`,
  >    skip steps 4–6, go to Execute. Under auto mode never offer — start fresh.

  (Keep the auto-mode authority + Pickup-exception wording from the old step 3, folded into the
  step-2 note above.)

- [ ] **Step 3: Rework the Announce beat (unchanged content, now after `open`).** Keep the
  existing "Announce the run (conductor opening)" block (lines 110–124) verbatim — it already
  reads the roadmap from the manifest fields, which now come from the `open` digest. It renders
  after step 2 (open) and before input gathering.

- [ ] **Step 4: Rework Setup steps 7–8 into gather + `record`.** Replace lines 125–166 with:

  > 4. **Gather inputs** — `inputs = {}`; for each `required_inputs` name use the caller's
  >    literal value or `AskUserQuestion`; apply `input_defaults` for unsupplied non-required.
  >    Auto mode + a missing required input → dispatch `{op:"fail", …}` and stop (never invent).
  >    Build the `materialize` list: for each `materialize_inputs` name with a value, an entry
  >    `{name, provenance:"inline"|"path", value}` (inline = a literal string/pasted content;
  >    path = a filesystem path the caller gave).
  > 5. **Record the run (bookkeeper).** Dispatch `{op:"record", name, manifest_hash, profile,
  >    overrides, gate_mode, inputs, materialize}`. It mints the run, materializes/normalizes,
  >    and records inputs (seeding run-state). Note `run_dir` and the returned `inputs` (with
  >    materialized paths). `ok:false` → surface `error` and stop.

- [ ] **Step 5: Verify Setup reads coherently.** Read the reworked `## Setup` end-to-end.
  Confirm: no raw CLI argv remains in conductor prose; the SECURITY guidance now lives only in
  the bookkeeper (Task 6); `inputs`/`run_dir` flow from the `record` digest; the conductor holds
  no in-memory `results` map (the bookkeeper reads run-state from disk thereafter).

- [ ] **Step 6: Commit**

```bash
git add catalog/skills/workflow/SKILL.md
git commit -m "feat(workflow): conductor delegates Setup plumbing to the bookkeeper; hard voice ban-list

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Conductor — Execute-the-manifest + Finish delegation

**Files:**
- Modify: `catalog/skills/workflow/SKILL.md` (lines 335–417 framing + 419–686 kind subsections
  + 688–696 Finish)

**Interfaces:**
- Consumes: bookkeeper ops `prep`, `commit`, `fold-guidance`, `finish`, `fail` (Task 6).
- Produces: an execute loop where the conductor does ONLY the `Workflow` segment call,
  `AskUserQuestion` gates, orchestrator-node prompt execution, nested re-entry, cursor, and
  recaps; every CLI step is a bookkeeper dispatch. The gate evidence core is the `prep` digest's
  `evidence[]`, printed verbatim.

- [ ] **Step 1: Rework the loop framing (335–417).** Keep the cursor rule (342–361) and the
  `label` resolution verbatim. Replace the three-move checkpoint prose (364–417) with:

  > **After a step's main-loop work, persist via the bookkeeper.** Dispatch
  > `{op:"commit", name, run_dir, step:<i>, results:<the produced node(s) verbatim>, gate?, outcome?, label}`.
  > On the SUCCESS path (`{ok:true}`) print NOTHING — the recap + the cursor advancing are the
  > signal. On `{ok:false}` the step's output failed its contract: say so in plain language
  > ("Step {i+1}'s output didn't meet its contract — stopping; it'll re-run on resume."), do
  > NOT continue, do NOT repair it (the bookkeeper already stamped the failed step). Then, for
  > the NEXT step, dispatch `{op:"prep", name, run_dir, step:<i+1>}` and branch on the digest
  > `kind` (sections below). After Setup (or resume), the first step is reached with one initial
  > `{op:"prep", step:i}`.

- [ ] **Step 2: Rework `### kind: "segment"` (419–475).** Replace with:

  > 1. The `prep` digest gave `{script, args, label, node_labels, produces, is_loop}`.
  > 2. Print the cursor + a one-line "working…" intent; invoke the **Workflow tool** with
  >    `scriptPath = .claude/workflow-defs/<name>/<script>` and `args` **verbatim**.
  > 3. On return, its value is keyed by `produces`. **Recap as a conductor** (keep the rubric at
  >    455–470 verbatim — synthesize, never dump; loop segments summarize rounds + verdict).
  > 4. Dispatch `{op:"commit", step:<i>, results:<returned, verbatim>, label}`. A loop step's
  >    `produces` includes `loop` — pass it too.
  > 5. A `Workflow` error (fail-loud node) → skip the recap, surface it, stop.

- [ ] **Step 3: Rework `### gate` (477–586).** Replace the mechanics, keep the invariant:

  > The `prep` digest gave `{gate, when, skipped, evidence[], gate_mode}`. `skipped:true`
  > (converged `loop_exhaust`) → `{op:"commit", step:<i>, results:{<gate.id>:null}, outcome:"skipped", label}`,
  > print the skipped cursor line, continue.
  > **(a) Framing (ephemeral):** intro naming `gate.label`, then the evidence core (b)
  > UNCHANGED, then a one-line choice framing.
  > **(b) Evidence core (verbatim):** print each `evidence[]` entry in order — `scalar` →
  > `**<label>**: <value>`; `json` → fenced ```json; `file` → fenced block of `contents` (lang
  > from `lang`). Never reorder/synthesize/substitute. (Resolution happened in the bookkeeper;
  > you only render.)
  > **Auto mode** (keep 521–535 semantics): `on_headless:"fail"` → `{op:"fail", label:'gate <id> on_headless:fail'}` and STOP; else record `gate.default` verbatim and `{op:"commit", … gate:{id,choice:default}, label}`, print `Gate <id>: auto — taking declared default '<default>'`, then act per the choice; a `revise`/`extend` default → `{op:"fail"}` + stop.
  > **Interactive:** `AskUserQuestion` (`gate.prompt`, `gate.options`, per-option consequence
  > descriptions). Record `{op:"commit", … gate:{id, choice:<pick>}, label}`, then act:
  > - approve/confirm → continue.
  > - revise → ask what to change; `{op:"prep", step:<segment i>, …}` to rebuild args, fold the
  >   notes into the relevant `args` value (conductor-side), re-invoke the segment, recap,
  >   re-`commit`, re-present.
  > - extend (`loop_exhaust`) → `{op:"fold-guidance", notes_input, notes, extension_n}`; then
  >   `{op:"prep", step:<loop i>, extend:true}`; re-invoke the loop segment; recap ("extension
  >   N"); `{op:"commit", step:<loop i>, results:<refreshed incl. loop>}`; unconverged →
  >   re-present, converged → continue (choice stays `extend`).
  > - abandon/stop → stop cleanly.
  > **warn gates:** render evidence, record `{choice:default|null}` via `{op:"commit", … outcome:"skipped"}`, continue.

- [ ] **Step 4: Rework `### orchestrator_node` (588–626).** Replace with:

  > The `prep` digest gave `{node, prompt|iterations, skipped, io_schema, gate_mode}`.
  > `skipped:true` → `{op:"commit", step:<i>, results:{<node>:null}, outcome:"skipped", label}`,
  > continue. Else **execute the resolved `prompt` here in the main loop** (this is the node's
  > work — file side effects / `AskUserQuestion` as the node needs; these tool calls are work,
  > not plumbing, and legitimately show). `for_each` → run each `iterations[k].prompt`, collect
  > an array. When `io_schema` is non-null, the result must be an object with exactly those
  > fields. Then `{op:"commit", step:<i>, results:{<node>:<result>}, label}`. Auto mode + a
  > prompt that requires asking → `{op:"fail", label:'<node> needs a human'}` + stop. (Keep the
  > nested-workflow-from-orch-node note at 624–626.)

- [ ] **Step 5: Rework `### nested_workflow` (628–686).** Replace the eval/normalize plumbing
  with the `prep` digest, keep the re-entry + cursor-threading verbatim:

  > The `prep` digest gave `{node, workflow, profile, child_inputs|iterations, skipped}`.
  > `skipped:true` → `{op:"commit", step:<i>, results:{<node>:null}, outcome:"skipped", label}`,
  > continue. Else **re-enter this `workflow` skill for `workflow`**, passing `--profile` when
  > carried and (auto mode, non-Pickup) `--gate-mode auto`; supply `child_inputs` as the child's
  > gathered inputs — the child runs its OWN conductor+bookkeeper pair (its `record` op
  > materializes any raw-string child materialize inputs). Thread the parent cursor (display
  > only — keep 672–681 verbatim). Store the child's `output.from` result; recap as a conductor;
  > `{op:"commit", step:<i>, results:{<node>:<child result>}, label}`. `for_each` → once per
  > `iterations` entry, collect an array. Child failure → stop, surface it.

- [ ] **Step 6: Rework `## Finish` (688–696).** Replace the `run-journal` call with
  `{op:"finish", name, run_dir}`; keep the conductor sign-off + the `output.from` result report
  + the optional "for a full timeline…" aside, all verbatim.

- [ ] **Step 7: Verify the execute walk reads coherently** against
  `catalog/workflow-defs/microskill-create/.compiled/manifest.json` (Read it). Trace each step:
  the conductor only `Workflow`/`AskUserQuestion`/executes-prompt/re-enters; every CLI is a
  bookkeeper op; gate evidence is `prep`-resolved and printed verbatim; no banned prose remains.

- [ ] **Step 8: Commit**

```bash
git add catalog/skills/workflow/SKILL.md
git commit -m "feat(workflow): conductor delegates execute-loop + finish plumbing to the bookkeeper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Conductor — Preflight / Rerun / Pickup + their bookkeeper ops

**Files:**
- Modify: `catalog/agents/workflow-bookkeeper/AGENT.md` (add ops `preflight`, `rerun-locate`,
  `rerun-seed`, `pickup-locate`)
- Modify: `catalog/skills/workflow/SKILL.md` (Preflight 168–207, Rerun 209–266, Pickup 268–333)

**Interfaces:**
- Consumes: Task 6 ops + Task 7/8 conductor patterns.
- Produces: the three non-execute modes converted to the same split — no raw CLI in the
  conductor; their bespoke plumbing (compile --plan, locate, seed, recompile-with-provenance)
  runs in the bookkeeper.

- [ ] **Step 1: Add the mode ops to the agent def.** Append to `AGENT.md`: `## op: preflight`
  (`compile-workflow --plan --explain …`, return `{ok, summary}` with the full
  `manifest`+`classification`); `## op: rerun-locate` and `## op: pickup-locate`
  (`run-journal latest` with no `--manifest-hash`/`--steps`, read `run-config`(+`run-state` for
  pickup), return the provenance fields); `## op: rerun-seed` (`run-journal rerun
  --runs-dir … --manifest … --source-run … [--from …]`, return
  `{ok, run_dir, from_step_index, snapped, replayed_gates, confirm_steps}`). Lift the exact
  commands from SKILL.md Preflight step 1 (174–176), Rerun steps 1/3 (217–244), Pickup step 1
  (279–287). The recompile-with-recorded-provenance is an `open`-style call with the recorded
  profile/overrides/gate_mode — reuse `op:"open"` with those args.

- [ ] **Step 2: Rework Preflight (168–207).** Replace the inline `compile-workflow --plan`
  (step 1) with `{op:"preflight", …}`; render EXCLUSIVELY from the digest `summary` (the same
  render rules at 179–205, verbatim). STOP before any run (unchanged).

- [ ] **Step 3: Rework Rerun (209–266).** Replace: locate → `{op:"rerun-locate", name, run?}`;
  recompile-with-provenance → `{op:"open", … recorded profile/overrides/gate_mode}` and compare
  `manifest_hash` (mismatch → stop, verbatim rule); seed → `{op:"rerun-seed", …}`. Keep the
  replay-never-re-ask lines (245–250), the conductor opening-for-rerun (start at `i =
  from_step_index`), and the confirm_steps re-exec `AskUserQuestion` (258–266) in the conductor.
  The from-step execute walk reuses Task 8.

- [ ] **Step 4: Rework Pickup (268–333).** Replace: locate → `{op:"pickup-locate", …}`;
  recompile → `{op:"open", … recorded provenance}` + hash-equality check; adopt-in-place →
  `{op:"resume", name, run_dir, mode:"pickup"}`. Keep the sanity-checks (293–301), the
  interactive-gate-override semantics (309–324), and the soundness note (326–333) verbatim.

- [ ] **Step 5: Verify all four modes** read coherently (Preflight/Rerun/Pickup + the execute
  walk they share). Confirm no raw CLI in conductor prose anywhere in SKILL.md; the bookkeeper
  agent def now carries every op the conductor references.

- [ ] **Step 6: Commit**

```bash
git add catalog/skills/workflow/SKILL.md catalog/agents/workflow-bookkeeper/AGENT.md
git commit -m "feat(workflow): convert preflight/rerun/pickup modes to the bookkeeper split

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Integration — reconcile, suite, determinism, real run, PR

**Files:** none (verification + any additive golden reconcile surfaced here)

**Interfaces:** Consumes Tasks 6–9. Produces a green suite, a registered bookkeeper, and a
verified clean-transcript run.

- [ ] **Step 1: Materialize the runtime.** Run
  `catalog/scripts/initialize-harness --apply --project-root . --catalog ./catalog`.
  Expected: the engine action is `update` (the new agent + edited skill changed the engine
  hash); `.claude/agents/workflow-bookkeeper/AGENT.md` and the reworked
  `.claude/skills/workflow/SKILL.md` are materialized. Never hand-edit `.claude/`.

- [ ] **Step 2: Full suite.** Run
  `python3 -m pytest catalog/scripts/tests/ scripts/tests/ hooks/tests/ -v`.
  Expected: GREEN, including Task 6's new tests. If a test asserts a literal engine **file
  count** or a golden engine listing, it now sees +1 file (the bookkeeper) — update that
  expectation to include it (additive). Do NOT change any run-state/journal/manifest_hash
  assertion; if one moved, a run-script was disturbed — stop and fix (no script was supposed to
  change).

- [ ] **Step 3: Prove determinism.** Run
  `catalog/scripts/compile-workflow refine-requirements && cp -r catalog/workflow-defs/refine-requirements/.compiled /tmp/c1 && catalog/scripts/compile-workflow refine-requirements && diff -r /tmp/c1 catalog/workflow-defs/refine-requirements/.compiled`.
  Expected: no diff (the run-scripts and compiler are untouched).

- [ ] **Step 4: Restart, then verify the conductor experience.** The new agent + skill register
  on the **next session restart** — note this for the human running the verification. After
  restart, run a small real workflow (e.g. `/workflow review-changes --plan` for a no-side-effect
  preflight, then a real `/workflow microskill-create` with a throwaway requirement). Assert the
  transcript shows ONLY: opening + roadmap, `▶ Step` cursors, per-segment "working…" beats,
  conductor recaps, gate questions, finish — and collapsed `workflow-bookkeeper` agent lines —
  with NO `Bash`/`Write`/`Read` plumbing blocks and NO run-ids/paths/argv in the prose. Confirm a
  gate renders its evidence and the recorded choice is verbatim.

- [ ] **Step 5: Open the PR (covers Phase 1 + Phase 2).**

```bash
git push -u origin workflow-conductor-ux
gh pr create --title "feat(workflow): conductor-voice execution UX + bookkeeper plumbing split" \
  --body "Implements docs/superpowers/specs/2026-06-17-workflow-execution-ux-design.md (Phase 1: conductor voice + authored names) and docs/superpowers/specs/2026-06-17-workflow-conductor-bookkeeper-design.md (Phase 2: the bookkeeper split that actually hides plumbing). Plan: docs/superpowers/plans/2026-06-17-workflow-execution-ux-conductor.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: PR opens with a conventional-commit title (squash-merge enforced). Do not merge to
`main` directly.

---

## Phase 2 Self-Review

**Spec coverage** (bookkeeper-design §3–§12):
- §3 split (conductor vs bookkeeper) → the protocol table + Tasks 7–9 (conductor) + Task 6 (agent).
- §4 rhythm (commit-prior + prep-next per boundary) → Task 8 Step 1 + the `prep`/`commit` ops.
- §5 integrity contracts → relay-verbatim (Global Constraints + Task 8 Steps 2/4/5), exact-CLI
  (Task 6 "lift verbatim" + no-script-change constraint), present-in-bookkeeper (Task 6 `prep` +
  Task 8 Step 3).
- §6 determinism/resume → no-script-change constraint, `resume` op (Task 6), Task 10 Step 3;
  loud failures stay in the conductor (Task 8 Step 1).
- §7 prose ban-list → Task 7 Step 1.
- §8 the agent (component, locked tools, contract, dedicated) → Task 6 (+ its test).
- §9 decisions / §10 YAGNI (no Workflow-in-subagent, no script/engine change) → honored
  throughout; modes converted (Task 9), not deferred.
- §11 success criteria → Task 10 Step 4 (clean transcript), Step 2 (suite), Step 3 (determinism).
- §12 testing → Task 6 hermetic test; Task 10 suite + trace-throughs (Tasks 7/8/9 verify steps)
  + real run.

**Placeholder scan:** none — the agent-def op bodies cite exact SKILL.md line ranges to lift
verbatim (DRY, not "similar to"); the one new test is shown in full; every conductor edit gives
the replacement prose and the exact lines it replaces.

**Type/name consistency:** the op names (`open`/`record`/`resume`/`prep`/`commit`/
`fold-guidance`/`finish`/`fail`/`preflight`/`rerun-locate`/`rerun-seed`/`pickup-locate`) and
their digest fields are used identically in the protocol table, Task 6 (producer), and Tasks
7–9 (consumers). Authored field `tools: Bash, Read, Write` matches the Task 6 test assertion.

**Known risk carried into execution:** the bookkeeper is dispatched per step boundary, so a
long nested run makes many dispatches (cost, accepted per §9). The conductor↔bookkeeper result
relay is verbatim text in an Agent prompt — same transcription class as today's run-state
`Write`, but smaller (fresh result only); `check-step-io` remains the schema guard. The new
agent + skill are co-dependent and only live after `initialize-harness --apply` + a session
restart (Task 10 Steps 1/4) — do not attempt a real run before both.
