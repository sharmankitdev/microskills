---
name: bundle-draft
description: Use when a create-pipeline review or verify stage needs a SINGLE artifact_path but the implementer staged a LIST of files — concatenate the staged draft files (and an optional grounding file) into one review bundle with per-file provenance markers, by running the bundle-draft script. Produces the bundle path plus the staged-file count.
---

# Bundle Draft

## Purpose

Given the staged draft's file paths, an output path, and an optional grounding file to append, run the deterministic bundle builder and return where the bundle was written plus how many staged files it carries.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| staging_paths | yes | array | Absolute paths to the staged draft files to concatenate, in order; each is emitted under a `===== FILE: <path> =====` marker so a reviewer maps a finding back to its real source file and line. | — |
| out | yes | string | Filesystem path the concatenated bundle is written to. | — |
| append | no | string | One extra grounding file (e.g. catalog-index.json) concatenated AFTER the staged files, for the cross-artifact verify seat whose grounding must ride inside artifact_path. | — |

## Steps

1. **Run builder** — Run `.claude/scripts/bundle-draft --out <out>` followed by every element of `staging_paths` as a separate shell-quoted positional argument, one per path, in order; insert `--append <append>` (also shell-quoted) immediately after `--out` and before the positional paths when `append` is supplied.
2. **Parse result** — Parse the script's stdout JSON `{bundle_path, file_count}` and its exit code.
3. **Return result** — Return that JSON object as the structured result.

## Output

A single JSON object `{bundle_path, file_count}` returned as the result: `bundle_path` is the written bundle path and `file_count` is the number of staged (positional) files concatenated, excluding any appended grounding file.

## Failure modes

- **Missing required input** — staging_paths or out is absent; stop, name the missing input, do not proceed.
- **Empty staging_paths** — staging_paths carries no file paths; stop, state that at least one staged file is required, do not proceed.
- **Builder script failed to start** — the script exits non-zero with a JSON error on stderr (a missing or unreadable input file, an unwritable out path); stop, quote the exit code and the error, do not fabricate a bundle.
