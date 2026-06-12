---
name: extract-closure-claims
description: Use when a requirements-refinement loop holds the round-1 gaps array, the accumulated answers ledger, and the current document, and needs to determine which gaps the document has since closed. Reads the document and answers, and for each gap the document no longer leaves open emits one closure claim {id, gap_id, section, question, closure_excerpt} — id pinned as claim-<gap_id> so a downstream verifier echoes it as finding_id — alongside the still-open remainder. Produces a JSON object carrying claims, claim_count, still_open, and still_open_count.
---

# Extract Closure Claims

## Purpose

Given the round-1 gaps, the answers ledger, and the current document, classify each gap as closed or still-open and produce per-closure claims plus the open remainder.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| gaps | yes | array | The round-1 gaps array; each entry carries the gap id, section, and clarifying question that the closure check is evaluated against. | — |
| document_path | yes | string | Filesystem path to the current document; its contents are read and scanned for the excerpt that closes each gap. | — |
| answers_path | no | string | Path to the accumulated answers ledger (passed by reference) contributing every gap id raised in later clarification rounds; absent when no later-round answers exist. | — |
| refinement_record | no | object | Optional refinement-record object carried through as supplementary context; treated as a whole value and never dereferenced (a null value is left untouched). | — |

## Steps

1. **Read document** — Read the document contents from the path at document_path.
2. **Read answers ledger** — Read the accumulated answers ledger from answers_path for a supplied path whose file exists (a supplied path with no file behind it contributes nothing — the legal no-gaps case), treating the refinement_record object as an opaque whole value.
3. **Judge gaps** — Judge every entry in the gaps array against the document and answers, marking it closed or still open.
4. **Build closure claims** — Build one closure claim per closed gap, shaped {id, gap_id, section, question, closure_excerpt} with id set to claim-<gap_id> and closure_excerpt quoting the document text that resolves it.
5. **Collect open remainder** — Collect every still-unresolved gap into the still-open remainder.
6. **Emit result** — Emit the JSON object with claims, claim_count, still_open, and still_open_count.

## Output

A single JSON object returned to the caller (not a written file) carrying claims (the per-closed-gap claim objects, each {id, gap_id, section, question, closure_excerpt} with id as claim-<gap_id>), claim_count (their count), still_open (the unresolved gap entries), and still_open_count (their count).

## Failure modes

- **Missing required input** — gaps or document_path is absent; stop, name the input, do not proceed.
- **Document unreadable** — the file at document_path does not exist or is not readable; stop, quote the path, do not proceed.
- **Answers ledger absent** — answers_path is supplied but no file exists at it; treat it exactly as the absent case (the ledger is only ever written by a gaps>0 clarification round, so a missing file on a no-gaps run is the legal happy path), contributing no later-round gap ids. A file that exists but cannot be parsed is different: stop, quote the path, do not proceed.
