# Phase 2 — Implementer

The `workflow-implementer` agent turns the approved plan into a staged, schema-valid `WORKFLOW.yaml` + `profiles/base.yaml`, and mints any new microskill profiles the plan calls for. On remediation rounds, it addresses every evaluator finding without regressing accepted fields.

## Input handed to the agent

- `plan_path` — path to the approved plan YAML (the target WORKFLOW.yaml plus reserved `_new_profiles` / `_reuse` annotations); **Read it**.
- `staging_dir` — sandbox root; write under `<staging_dir>/<name>/`.
- Substrate: `.claude/templates/workflow-template.yaml`, `.claude/templates/references/workflow-schema.json`.
- On a remediation pass (round ≥ 1): `staging_paths` (prior draft) and `last_findings` (issues to fix).

## What the implementer does

1. **Read the substrate.** The WORKFLOW.yaml grammar is closed; the written file must validate against `workflow-schema.json`.
2. **Write `<staging_dir>/<name>/WORKFLOW.yaml`.** Copy the plan's design verbatim, then **remove the `_new_profiles` and `_reuse` keys** (they are not valid WORKFLOW.yaml fields). Write `${...}` reference strings exactly as the plan gives them — do not resolve them. Keep `loop.body` nodes contiguous and orchestrator nodes out of the loop body.
3. **Write `<staging_dir>/<name>/profiles/base.yaml`.** Always `version: 1`, plus any overlay fields the plan's profile decisions require (e.g. `loop: { max_iters: N }`). Create the `profiles/` subdir.
4. **Mint new microskill profiles.** For each entry in the plan's `_new_profiles` (`{microskill, profile, contents}`), write `.claude/microskills/<microskill>/profiles/<profile>.yaml` with `contents` — **only if that file does not already exist**. A collision → stop and surface a block finding (`location: config`); never overwrite. This is the one write outside `staging_dir`; the evaluator's `compile-workflow` step resolves these profiles from the real microskills dir.
5. **Remediate findings (remediation pass).** For each issue in `last_findings.issues[]`, fix the section its `location` names. Modify only flagged sections — do not touch accepted fields.

## Output

The agent's final message lists:
- The absolute paths written — always `<name>/WORKFLOW.yaml` and `<name>/profiles/base.yaml` under `staging_dir`, plus any minted microskill-profile paths.
- On remediation rounds: one line per finding addressed.

## Hard rules

- Never write the workflow outside `staging_dir` (minted microskill profiles are the sole, additive exception).
- The written WORKFLOW.yaml carries no `_new_profiles` / `_reuse` keys and validates against `workflow-schema.json`.
- Always write both `WORKFLOW.yaml` and `profiles/base.yaml`.
- Do not modify the substrate template or schema.
