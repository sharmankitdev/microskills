---
name: apply-findings-to-document
base: true
description: Use when you hold a structured markdown document and a set of structured findings each citing a section, and need the document revised to address them in one pass. Rewrites the document in place — applying every addressable finding in its cited section, folding in any freeform revision notes, and recording each unresolvable finding as an explicit "Open question for human review" line. Null or empty findings and notes make it a no-op that returns the document unchanged. Produces the document path plus addressed and deferred counts and a summary.
---

# Apply Findings To Document

## Purpose

Given a document_path plus optional findings and notes, rewrite the document in place to address every applicable finding and record the rest as open-question lines, then return the path with addressed/deferred counts and a summary.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| document_path | yes | string | Filesystem path to the structured markdown document rewritten in place. | — |
| findings | no | array | Structured findings to apply, each citing the section it targets; null or empty means no findings to apply. | — |
| notes | no | string | Freeform revision guidance folded in alongside the findings; null or empty means none. | — |

## Steps

1. **Read document** — Read the structured markdown document at document_path.
2. **Partition findings** — Partition the supplied findings into those addressable in their cited section and those that cannot be resolved from the document alone.
3. **Rewrite sections** — Rewrite each addressable finding's cited section to resolve it, folding in any freeform notes guidance.
4. **Record open questions** — Append each unresolvable finding as an explicit "Open question for human review" line.
5. **Write document** — Write the revised document back to document_path in place, counting addressed and deferred findings.
6. **Return result** — Return the structured result with document_path, addressed_count, deferred_count, and summary.

## Output

The document at document_path is overwritten in place with the revised markdown. The returned structured result is a JSON object carrying document_path (the rewritten path), addressed_count (findings resolved in their section), deferred_count (findings recorded as open-question lines), and a one-line summary of the revision.

## Failure modes

- **Missing required input** — document_path is absent; stop, name the input, do not proceed.
- **Document unreadable** — the file at document_path does not exist or is not readable; stop, quote the path, do not proceed.
- **Both findings and notes null or empty** — no-op; return the document unchanged with addressed_count and deferred_count both 0.
- **Malformed finding entry** — a finding lacking a cited section (or otherwise not addressable to a location) is treated as unresolvable and recorded as an explicit "Open question for human review" line, counted in deferred_count, never silently dropped or rejected.
