---
name: generate-backlog
description: >-
  Use when you have a finalized requirements document, an HLD, and per-component
  LLDs (optionally wireframes) and need them decomposed into a prioritized,
  traceable product backlog. Reads each source by path and writes epics, user
  stories with acceptance criteria, MoSCoW priority, rough estimates, dependency
  and traceability links, and an MVP/release slice in place under output_dir.
  Produces a backlog directory plus an index file carrying a machine-readable
  story table.
---

# Generate Backlog

## Purpose

Given a requirements document, an HLD, per-component LLDs, and optional wireframes, decompose them into a prioritized, traceable backlog and write it under output_dir, returning the backlog location and counts.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| requirements_path | yes | string | Filesystem path to the finalized requirements document. Read it; its sections are the traceability anchors stories link back to. Materialized by reference. | — |
| hld_path | yes | string | Filesystem path to the high-level design document. Read it for the architectural shape stories are sliced against. Materialized by reference. | — |
| lld_set | yes | array | Array of {document_path, component_name} entries; each names a per-component low-level design document to Read. Components are the design anchors stories trace to. | — |
| wireframes | no | object | Optional UX-stage output object or null. When a non-null object, its wireframes_path field names a wireframes document to Read; null means no wireframes and is never dereferenced. | — |
| prior_findings | no | array | Optional array of {kind, ref} entries naming defects a regeneration pass must address. Consumed as guidance data folded into the decomposition, not as control flow. | — |
| regeneration_notes | no | string | Optional free-text guidance for a regeneration pass, folded into the decomposition as data. Empty or absent on a first pass. | — |
| output_dir | yes | string | Filesystem directory under which the backlog and its index file are written in place. | — |

## Steps

1. **Read sources** — Read the requirements document at requirements_path, the HLD at hld_path, and each LLD named in lld_set, plus the wireframes document when wireframes is a non-null object.
2. **Fold guidance** — Fold prior_findings and regeneration_notes in as decomposition guidance to honor on this pass.
3. **Decompose into stories** — Decompose the sources into epics, breaking each epic into user stories with acceptance criteria, MoSCoW priority, a rough estimate, dependency links, and traceability links to the requirement sections and design components each story realizes.
4. **Select MVP slice** — Select the MVP/release slice across the stories.
5. **Write backlog** — Write the backlog under output_dir as per-story files plus an index file whose machine-readable story table has pipe-separated columns id|path|epic|title.
6. **Return result** — Return the structured result naming the backlog directory, index path, and the epic, story, and MVP-story counts.

## Output

A product backlog written in place under output_dir: per-story files plus a backlog index file. The index carries a machine-readable story table with pipe-separated columns id|path|epic|title. The returned JSON object names the backlog directory and index path, the epic/story/MVP-story counts, and a one-line summary.

## Failure modes

- **Missing required input** — requirements_path, hld_path, lld_set, or output_dir absent; stop, name the input, do not proceed.
- **Source document unreadable** — requirements_path, hld_path, any lld_set document_path, or a non-null wireframes_path does not exist or cannot be read; stop, quote the path, do not proceed.
- **Malformed lld_set** — lld_set is empty or an entry is missing document_path or component_name; stop, quote the bad entry, do not proceed.
