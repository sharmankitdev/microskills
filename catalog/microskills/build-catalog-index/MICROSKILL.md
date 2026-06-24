---
name: build-catalog-index
description: Use when a cross-artifact review needs catalog-wide grounding — enumerate the component catalog (and any vendored custom components) into a deterministic catalog-index.json the duplicate-capability, naming-collision, and reverse-consumer dimensions read, by running the build-catalog-index script. Produces the index path plus the component and consumer-edge counts.
---

# Build Catalog Index

## Purpose

Given the catalog root and an optional harness manifest, run the deterministic catalog-index builder and return where the index was written plus its component and consumer-edge counts.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| catalog_root | yes | string | Filesystem path to the catalog/ directory to enumerate. | — |
| output_dir | yes | string | Directory the index file (catalog-index.json) is written into. | — |
| harness_path | no | string | Filesystem path to harness.yaml; when supplied, source:custom components it lists are added to the index so the deployed namespace is complete. | — |

## Steps

1. **Run builder** — Run `.claude/scripts/build-catalog-index --catalog-root <catalog_root> --out <output_dir>/catalog-index.json`, adding `--harness <harness_path>` when `harness_path` is supplied.
2. **Parse result** — Parse the script's stdout JSON `{index_path, component_count, consumer_map_count}` and its exit code.
3. **Return result** — Return that JSON object as the structured result.

## Output

A single JSON object `{index_path, component_count, consumer_map_count}` returned as the result: `index_path` is the written catalog-index.json path, `component_count` the number of indexed components, and `consumer_map_count` the total number of consumer edges across the index.

## Failure modes

- **Missing required input** — catalog_root or output_dir is absent; stop, name the missing input, do not proceed.
- **Builder script failed to start** — the script exits non-zero with empty or non-JSON stdout (e.g. catalog_root is not a directory); stop, quote the exit code and stderr, do not fabricate an index.
- **Output directory unwritable** — the index file cannot be written under output_dir; stop, quote the path, do not proceed.
