---
name: publish-backlog
description: Use when you have a finalized backlog directory and need to publish it to a configured target, then report where it landed. Reads the backlog from backlog_dir and follows the active publish-target contract variable — the base target writes the canonical published layout plus an index under output_dir, while a github profile retargets the variable to gh milestones and issues. One linear path, where the variable carries all target-specific instruction. Produces a JSON object naming the published target, the location it landed at, and a summary.
---

# Publish Backlog

## Purpose

Given a backlog directory and a publish-target contract variable, publish the backlog to that target and report where it landed.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| backlog_dir | yes | string | Filesystem path to the directory containing the source backlog to publish (epics, stories, index). | — |
| output_dir | yes | string | Filesystem path to the directory where the base local target writes the published backlog artifact set plus its index. | — |
| github_repo | no | string | Target GitHub repository (owner/name) for the github publish target; empty means gh uses the current repository. Unused by the base local target. | — |

## Steps

1. **Read backlog** — Read the source backlog from the directory at backlog_dir.
2. **Publish to target** — Publish the backlog to the destination named by the {{publish_target}} contract variable, following that variable's target-specific instruction.
3. **Confirm artifacts** — Confirm the published artifacts exist at their destination.
4. **Return result** — Return the structured result naming the published_target, the location it landed at, and a summary.

## Output

A structured JSON object reporting the publish outcome. Side effects depend on the active {{publish_target}}: the base local target writes the canonical published backlog layout plus an index under output_dir. The returned object carries published_target (the target that was published to), location (the path or URL where the backlog landed), and summary (a one-line description of what was published).

## Failure modes

- **Missing required input** — backlog_dir or output_dir is absent; stop, name the input, do not proceed.
- **Backlog directory unreadable or empty** — the directory at backlog_dir does not exist or has no backlog to publish; stop, quote the path, do not proceed.
- **Publish destination unwritable** — the {{publish_target}} destination cannot be written (e.g. output_dir not creatable); stop, quote the destination, do not proceed.
