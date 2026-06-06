---
name: summarize-diff
description: >
  Use when you have a unified git diff and need a structured, file-grouped
  summary for downstream code review. Parses the diff into per-file entries
  (path, change kind, hunk count, insertions, deletions, inferred language),
  aggregates overall stats, and writes a plain-language intent overview plus a
  riskiest-areas note, optionally grounded by caller-supplied context text.
  Produces a single JSON object with summary, files, risk_surface, and stats.
---

# Summarize Diff

## Purpose

Given a unified git diff and optional caller-supplied grounding text, parse and analyze the changes to produce a structured, file-grouped JSON summary carrying an intent overview, a risk-surface note, and overall stats.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| diff | yes | string | The unified git diff text (e.g. the output of `git diff`) to summarize, passed directly as a string. | — |
| context | no | string | Optional free-text grounding material — a PR/change description, a spec excerpt, or a requirements snippet — supplied directly by the caller and used only to ground the intent overview. Always literal text: the skill never reads files and never treats this value as a path. | — |

## Steps

1. **Parse diff** — Parse the unified git diff into per-file sections, taking the path and change kind (added, modified, deleted, renamed) from each file's diff header.
2. **Extract per-file metadata** — Extract per-file metadata across all changed files: hunk count, insertion and deletion line counts, and the programming language inferred from the file extension (using a neutral fallback such as "unknown" for unrecognized extensions).
3. **Aggregate stats** — Aggregate the overall stats across the parsed files: total files changed, total insertions, and total deletions.
4. **Compose intent overview** — Compose a one-to-three sentence plain-language overview of the change set's intent, grounded by the caller-supplied context text.
5. **Write risk-surface note** — Write a short risk-surface note naming the riskiest touched areas, such as auth, data migrations, concurrency, or public API surface.
6. **Assemble output** — Assemble and return the single JSON object with summary, files, risk_surface, and stats.

## Output

A single structured JSON object returned to the caller (not written to disk): `summary` (the plain-language intent overview), `files` (an array of per-file objects each with path, change_kind, hunks, insertions, deletions, and language), `risk_surface` (the riskiest-areas note), and `stats` (files_changed, insertions, deletions). Consumable directly by downstream code-review steps.

## Failure modes

- **Missing required input** — diff is absent; stop, name the input, do not proceed.
- **Diff malformed** — the input is not a parseable unified diff (no recognizable file headers or hunks); stop, quote the offending text, do not proceed.
- **Empty change set** — the diff parses but contains no changed files; stop and report there is nothing to summarize rather than emitting empty fields.
