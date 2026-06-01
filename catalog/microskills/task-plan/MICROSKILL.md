---
name: task-plan
base: true
description: >
  Plan a domain artifact from a natural-language requirement, running as the domain
  planner agent against its phase contract, and return a structured plan object — or
  a scope advisory for work that does not fit one artifact.
---

<!--
Generic plan phase of the plan→build→check agentic model. In a workflow use: node
this microskill executes AS the domain planner agent (runtime.agent) on the pinned
model (runtime.model) — there is no nested sub-agent dispatch, because background
workflow segments cannot spawn nested agents; the executor IS the planner. Domain is
selected by profile: `microskill` (default, = base) → microskill-planner; `workflow`
→ workflow-planner. The phase contract it reads is named by {{contract_doc}}. Runtime
contract (profile resolution, input gathering) is owned by the `microskill` dispatcher
Skill — do NOT add a `## Setup` section.
-->

# Task Plan

## Purpose

Given a natural-language requirement, plan the domain artifact by following the phase contract, and return a structured plan object (or a scope advisory).

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| requirement | yes | string | Verbatim natural-language description of the artifact to create. | — |
| name_override | no | string | Optional kebab-case name override to fold into the plan. | — |
| staging_dir | yes | string | Absolute path to the sandbox dir where the plan YAML file is written. | — |

## Steps

1. **Read contract** — Read the phase contract at {{contract_doc}} and the substrate it references.
2. **Draft plan** — Following that contract, design the plan for the requirement and the optional name_override.
3. **Write plan file & return path** — Ensure staging_dir exists, write the drafted plan YAML to `<staging_dir>/plan.yaml`, then return the structured output: `plan_path` set to that written path, or a scope_advisory with `plan_path: null` for work that does not fit one artifact.

## Output

A structured JSON object carrying the path to the written plan. The `microskill` profile returns `{plan_path, scope_advisory}`; the `workflow` profile returns `{plan_path, name, scope_advisory, missing_microskills[]}`. `plan_path` is the path to the plan YAML file written under staging_dir (null when a scope advisory applies); `scope_advisory` is null unless the requirement should not become one artifact.

## Failure modes

- **Missing required input** — requirement or staging_dir is absent; stop, name the input, do not proceed.
- **Contract unreadable** — {{contract_doc}} does not exist or is not readable; stop, quote the path, do not proceed.
