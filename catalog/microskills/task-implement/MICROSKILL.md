---
name: task-implement
description: >
  Build a domain artifact from an approved plan, running as the domain implementer
  agent against its phase contract, writing the files into a staging directory and
  returning their absolute staging paths.
---

<!--
Generic implement phase of the plan→build→check agentic model. In a workflow use:
node this microskill executes AS the domain implementer agent (runtime.agent) on the
pinned model (runtime.model) — no nested sub-agent dispatch; the executor IS the
implementer. Domain is selected by profile: `microskill` (default, = base) →
microskill-implementer; `workflow` → workflow-implementer. The phase contract it
reads is named by {{contract_doc}}. On a remediation pass the caller supplies
last_findings. Runtime contract is owned by the `microskill` dispatcher Skill — do
NOT add a `## Setup` section.
-->

# Task Implement

## Purpose

Given an approved plan and a staging directory, build the domain artifact by following the phase contract and return the absolute staging paths of the files written.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| plan_path | yes | string | Path to the approved plan YAML file (from the task-plan microskill); Read it to get the plan. | — |
| staging_dir | yes | string | Absolute path to the sandbox directory where the files are written. | — |
| last_findings | no | object | Evaluator findings object from the prior round; present only on remediation passes. | — |

## Steps

1. **Read contract** — Read the phase contract at {{contract_doc}}.
2. **Build artifact** — Read the plan from plan_path; following that contract, write the artifact files into staging_dir from that plan, using last_findings to revise the flagged sections on a remediation pass.
3. **Assert sandbox** — Confirm every written path is under staging_dir; stop and surface any path that falls outside staging_dir rather than proceeding.
4. **Return implement output** — Return staging_paths as the structured output.

## Output

A structured JSON object `{staging_paths: string[]}` — the absolute paths of the files written inside staging_dir.

## Failure modes

- **Missing required input** — plan_path or staging_dir is absent; stop, name the input, do not proceed.
- **Path outside sandbox** — a written path falls outside staging_dir; stop, quote the path, do not proceed.
