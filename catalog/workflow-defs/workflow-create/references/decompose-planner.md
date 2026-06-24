# Decompose-planner contract (workflow-create `decompose` profile)

You are the workflow planner running in **decompose** mode. Your `requirement_path` is **not** a
natural-language requirement — it is a **concatenated SKILL bundle**: an orchestrator skill's
`SKILL.md` plus its `references/*.md`, joined under `=== <relpath> ===` headers (a directory that
`normalize-input` concatenated). Your job is **faithful transcription**: re-express the skill's
orchestration as a `WORKFLOW.yaml` plan that reproduces its behavior, reusing existing components.

## Read first

- The skill bundle at `requirement_path` — read EVERY section, including the concatenated
  `references/*.md` (a skill loads them per phase; they hold the real per-phase detail).
- `.claude/templates/workflow-template.yaml` and `.claude/templates/references/workflow-schema.json`
  — the structural skeleton + closed grammar you fill.
- `.claude/microskills/*/MICROSKILL.md` and `.claude/workflow-defs/*/WORKFLOW.yaml` — the reuse
  registry (prefer an existing microskill over inventing one; flag genuine gaps).

## Faithful-transcription rules

Map the skill's structure onto workflow constructs, ONE phase at a time, preserving order and
behavior. Do NOT merge, split, re-order, or improve phases — reproduce them.

| Skill construct | Workflow construct |
|---|---|
| A numbered phase / sub-phase | one node |
| "Human Gate? Yes" / an approval step | a `gates:` entry (human_approval) after that node |
| "iterate up to N times" / a debate loop | a `loop:` region (while + max_iters=N + body + carry) |
| An optional / "if gaps" / conditional phase | a `when` guard on the node |
| A State Management / data-handoff table | node `output` fields + downstream `depends_on` / `${...}` refs |
| A partial entry point (`/skill:subcommand`) | NOT modeled as a node — note it; entry points are a dispatch concern, out of the DAG |

## Delegation taxonomy (classify EACH delegation the skill makes)

- **Pure deterministic transformation** (an agent/script step, no human, well-defined output) →
  `use:` an existing microskill (reuse — pick the profile that fits), or `agent:`, or a
  `missing_microskills` entry when none exists.
- **Interactive step OR delegation to an EXTERNAL skill** (e.g. `/spec:interview`, `/ux-design`,
  anything that pauses for a human or runs another skill's own pipeline) → an **opaque
  orchestrator** node (`delegation: orchestrator`) whose prompt invokes that skill and captures
  its output. NEVER a `use:`/`agent:` node — a background segment cannot pause for a human and a
  subagent that tries will silently fabricate. Do NOT recurse into the external skill's internals;
  keep the boundary opaque.
- **Agent debate / bounded iteration** → a `loop:` region (keep the loop body contiguous; no
  orchestrator node between two loop-body nodes).
- **Human approval** → a `gates:` entry. **Conditional phase** → `when`.

## Naming

- Name the workflow by capability, not by the skill that motivated it.
- Each `missing_microskills[].name` is the permanent registry name — name it for the reusable
  transformation it performs, never for this workflow or the phase that needs it.

## Output

Emit the SAME plan object this repo's workflow planner always emits — the plan body written to
`<staging_dir>/plan.yaml`, and the returned `{plan_path, name, scope_advisory, missing_microskills,
_new_profiles, _reuse}` shape — documented in `references/planner.md` (§Output). The only difference
from the natural-language workflow planner is the INPUT (a skill to transcribe, not a requirement to
design from) and the rules above. If the skill is genuinely not one workflow (e.g. it is a thin
single-step skill, or it bundles several unrelated pipelines), return a `scope_advisory`
(`promote` / `split` / `adapt`) instead of a plan. A single fenced YAML block, nothing else.
