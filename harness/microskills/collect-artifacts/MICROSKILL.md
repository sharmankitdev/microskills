---
name: collect-artifacts
description: "Use when a fan-out produced several optional artifact-entry objects (each shaped {document_path, label, ...} or null) and you need them fanned in. The artifact-level twin of collect-findings: concatenates the supplied entries into one ordered candidates array, dropping nulls and preserving supply order in a pure concatenate-and-count pass. Base declares generic optional entries; profiles rename or declare their own input set. Produces a single JSON object {candidates, candidate_count}."
---

# Collect Artifacts

## Purpose

Given a set of optional artifact-entry objects, concatenate the non-null entries into one ordered candidates array in supply order and count them, returning {candidates, candidate_count}.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| artifact_1 | no | object | First optional artifact-entry object, shaped {document_path, label, ...}, or null when absent; included in candidates when non-null. | — |
| artifact_2 | no | object | Second optional artifact-entry object, shaped {document_path, label, ...}, or null when absent; included in candidates when non-null. | — |
| artifact_3 | no | object | Third optional artifact-entry object, shaped {document_path, label, ...}, or null when absent; included in candidates when non-null. | — |

## Steps

1. **Gather entries** — Gather the supplied optional artifact-entry inputs in declared input order.
2. **Drop nulls** — Drop every entry that is null, keeping only the non-null artifact-entry objects.
3. **Concatenate** — Concatenate the surviving entries into one ordered candidates array, preserving supply order.
4. **Count** — Count the candidates to set candidate_count.
5. **Return result** — Return a single JSON object carrying the candidates array and the integer candidate_count.

## Output

A single JSON object {candidates, candidate_count} returned as the structured result (nothing is written to disk). candidates is the ordered list of the non-null artifact-entry objects in supply order; candidate_count is the integer number of candidates. Consumable directly by a downstream workflow node.

## Failure modes

- **Every artifact-entry input absent** — every declared artifact-entry input is absent (all null); stop, state that at least one artifact entry is required, do not proceed.
- **Entry not an object** — a supplied entry is not an object (it is a non-null scalar or array); stop, name the offending input, do not proceed.
