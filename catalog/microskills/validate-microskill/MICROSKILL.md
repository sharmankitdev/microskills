---
name: validate-microskill
base: true
description: >
  Use when you have an existing microskill (by name or by explicit file paths to its
  MICROSKILL.md and profiles/base.yaml) and want to run the canonical microskill-evaluator
  agent against it. Dispatches the evaluator and returns the evaluator's JSON verdict
  (a pass flag and an issues array) verbatim.
---

<!--
This is a MICROSKILL.md template. It lives at `.claude/microskills/<name>/MICROSKILL.md`
and contains only the declarative content of the microskill (Purpose, Inputs, Steps,
Output, Failure modes). The runtime contract — profile detection, calling
`.claude/scripts/resolve-microskill`, gathering missing inputs, honoring directives —
is owned by the `microskill` dispatcher Skill at `.claude/skills/microskill/SKILL.md`.
Do NOT add a `## Setup` section here; the dispatcher injects nothing into the rendered
body, but treats the resolver's `rendered_skill_body` as the operative body for every
section below. The dispatcher is invoked either explicitly (`/microskill <name> [profile]`)
or implicitly through an auto-generated `.claude/commands/<name>.md` shim that delegates
to the dispatcher.
-->

# Validate Microskill

## Purpose

Given a microskill name or explicit file paths to its MICROSKILL.md and profiles/base.yaml, dispatch the microskill-evaluator sub-agent against those files and return its JSON verdict (pass + issues[]).

## Inputs

<!--
Every Default cell is literally `—`. Defaults for optional inputs live in
profiles/base.yaml under `inputs.<name>.default`; the resolver substitutes
them into this column at render time. Do not hardcode defaults here.
-->

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| microskill_name | no | string | Registered microskill name (subdirectory under .claude/microskills/). Used to derive file paths when explicit paths are not supplied. | — |
| microskill_md_path | no | string | Absolute or project-relative path to the target MICROSKILL.md. Overrides path derivation from microskill_name when supplied. | — |
| base_yaml_path | no | string | Absolute or project-relative path to the target profiles/base.yaml. Overrides path derivation from microskill_name when supplied. | — |

## Steps

1. **Verify input source** — Assert that at least one of microskill_name or the explicit-path pair (microskill_md_path and base_yaml_path together) is present in the inputs.
2. **Compute effective absolute paths** — Apply microskill_md_path and base_yaml_path as authoritative overrides on top of the defaults derived from microskill_name (`<project_root>/.claude/microskills/<microskill_name>/MICROSKILL.md` and `<project_root>/.claude/microskills/<microskill_name>/profiles/base.yaml`), and expand any project-relative result against the project root to produce absolute paths.
3. **Verify files exist** — Confirm both resolved absolute paths are readable files; stop and name any missing path rather than proceeding with an unreadable input.
4. **Read both files** — Load the contents of the resolved MICROSKILL.md and profiles/base.yaml into working context.
5. **Dispatch evaluator** — Invoke the microskill-evaluator sub-agent, passing the resolved absolute MICROSKILL.md path, the resolved absolute profiles/base.yaml path, and instructing the agent to follow its full research mandate (run validate-microskill script, then perform semantic checks).
6. **Surface verdict** — Return the evaluator's verdict as a JSON object (pass + issues[]) as the sole output; do not add prose before or after it.

## Output

A JSON object produced by the microskill-evaluator agent: a boolean `pass` field (true iff zero block-severity issues) and an `issues` array where each entry carries a severity (block|warn), a location, and a one-sentence message.

## Failure modes

- **Neither input source present** — neither microskill_name nor the explicit-path pair (microskill_md_path + base_yaml_path) is supplied; stop, name the missing inputs, do not proceed.
- **File not found** — derived or supplied file path does not exist or is not readable; stop, quote the bad path, do not proceed.
- **No verdict returned** — microskill-evaluator agent returns no parseable JSON verdict (fenced block absent or its content is malformed JSON); stop, report that the evaluator produced no parseable verdict, do not fabricate one.
