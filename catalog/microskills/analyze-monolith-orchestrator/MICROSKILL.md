---
name: analyze-monolith-orchestrator
base: true
description: Use when you have a monolith orchestrator SKILL.md — a fat skill bundling many responsibilities and inline steps — and need to decompose it for the microskills pattern. Reads the file at skill_path and produces a structured decomposition brief that classifies each responsibility by control-flow shape, routes only the genuinely atomic ones to microskills (as pure single-action cores), hoists cross-cutting orchestration to the workflow layer, flags required sub-agents, and emits a single planner-ready decomposition_requirement string.
---

# Analyze Monolith Orchestrator

## Purpose

Given skill_path to a monolith orchestrator SKILL.md, read it and produce a decomposition brief that a workflow planner can turn into a correct microskill DAG on the first pass — by classifying each responsibility's control-flow shape, mapping only atomic responsibilities to microskills (each reduced to its pure single-action core), routing every non-atomic responsibility to a workflow construct, recording the cross-cutting concerns that belong on the workflow layer, and naming the sub-agents the rebuilt workflow will depend on.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| skill_path | yes | string | Filesystem path to the monolith orchestrator SKILL.md to decompose. | — |

## Steps

1. **Read monolith** — Read the monolith orchestrator SKILL.md at skill_path.
2. **Enumerate responsibilities** — List its discrete responsibilities, giving each a kebab-case candidate name and a one-line purpose.
3. **Map control flow** — Record the step ordering and the conditional, iterative, fan-out, and human-checkpoint structure the instructions imply.
4. **Trace data flow** — Record what every step consumes and produces.
5. **Classify and route** — Tag every responsibility with one shape from {atomic, conditional, iterative, fan_out, interactive} read off the control-flow map, and set its target form: atomic→microskill; conditional→orchestrator_node or gate; iterative→loop; fan_out→sibling nodes or for_each; interactive→orchestrator_node. Atomic means one fixed entry to one fixed exit with no decision point, repetition, parallel write, or human pause.
6. **Reduce microskill targets to atomic_core** — Set atomic_core on every microskill target to its single straight-line action from inputs to one output, stripped of all cross-cutting concerns.
7. **Hoist cross-cutting concerns** — Record under hoisted_concerns the orchestration to push to the workflow layer: single re-attempt policy, report-and-ask-the-user handoff, tool-failure fallback, partial-entry self-hydration, and mode selection — each naming its owning node, loop, or gate.
8. **Detect agent dependencies** — Record under agent_dependencies every sub-agent the monolith names, the responsibility that uses it, and whether it exists under .claude/agents/.
9. **Resolve nested-workflow needs** — Set exists on every nested_workflow target — true where the monolith invokes an already-available skill, command, or workflow; false where the sub-pipeline is described inline with no standalone unit. Give every false-exists target a workflow-create requirement, and give every nested_workflow target an invoke_target naming the unit the parent's orchestrator node runs.
10. **Synthesize and return** — Write decomposition_requirement instructing the planner to define a microskill solely from every atomic_core (a pure single-action unit carrying no re-attempt, human pause, fallback, mode fork, or self-hydration), realize every non-atomic responsibility as its assigned construct, realize every nested_workflow target as an orchestrator node that invokes its invoke_target at runtime (never a use: import, never inline-expansion; a missing nested workflow is built via workflow-create before its node), place every hoisted concern on its owning node/loop/gate, provision or port every missing agent_dependency before its node, and preserve the original ordering and human checkpoints; then return the full decomposition brief as JSON.

## Output

A single JSON object emitted as the skill's result (no prose), conforming to output_schema: decomposition_requirement (planner-ready string), responsibilities (each with name, purpose, shape, target, atomic_core, hoisted_concerns), control_flow, data_flow, agent_dependencies, and nested_workflows (each with name, requirement, exists, invoke_target). Only responsibilities with target=microskill are meant to become microskills; the rest are workflow constructs, and nested_workflow targets become orchestrator-node invokes — built via workflow-create when exists is false, invoked as-is when true.

## Failure modes

- **skill_path missing** — skill_path is missing or blank; stop, name the input, do not proceed.
- **skill_path unreadable** — skill_path does not exist or is not readable; stop, quote the path, do not proceed.
- **No decomposable structure** — file read but contains no discernible responsibilities or steps; stop, report that no decomposable structure was found, do not fabricate responsibilities.
