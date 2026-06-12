---
name: expand-assignments
description: >-
  Use when you have an item set and need every review or verification
  assignment enumerated. Deterministically crosses the union of an optional
  inline items array and the rows of an optional pipe-separated table
  (id|path|epic|title) with an optional lenses list (absent means a single
  null lens) and seat numbers 1..seats, emitting one flat assignment array.
  Produces a JSON object of pairs plus the item, lens, seat, and product
  counts a downstream tally cross-checks.
---

# Expand Assignments

## Purpose

Given an optional items array, an optional items_path table, an optional lenses list, and a seats integer, compute the items×lenses×seats cross-product and emit a flat array of {item, lens, seat} assignments with reconciling counts.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| items | no | array | Inline item objects to expand. A null or absent value contributes no entries; merged (unioned) with rows parsed from items_path. | — |
| items_path | no | string | Filesystem path to a machine-readable pipe-separated table whose columns are id, path, epic, title; each row becomes one item object. A null or absent value contributes no entries. | — |
| lenses | no | array | List of lens strings to cross every item with. Absent or empty yields a single null lens, so each item is emitted once with lens null. | — |
| seats | no | integer | Number of seats; each item×lens pair is emitted once per seat number in 1..seats. | — |

## Steps

1. **Read inline items** — Read the inline items array, treating a null or absent value as zero entries.
2. **Parse table rows** — Read items_path when supplied and parse each pipe-separated row into an item object with id, path, epic, and title fields; treat a null or absent path as zero entries.
3. **Union items** — Union the inline items and the parsed table rows into a single ordered item list.
4. **Normalize lenses** — Normalize lenses to the supplied list, or to a single-element list containing null when lenses is absent or empty.
5. **Enumerate cross-product** — Enumerate the cross-product of items, lenses, and seat numbers 1..seats, emitting one {item, lens, seat} assignment per combination into a flat ordered array.
6. **Assemble result** — Compute item_count, lens_count, seats, and pair_count (item_count × lens_count × seats) and assemble the result object.

## Output

A single JSON object returned as the structured result, carrying pairs (the flat array of {item, lens, seat} assignments), pair_count, item_count, lens_count, and seats. pair_count equals item_count × lens_count × seats, the arithmetic a downstream tally cross-checks.

## Failure modes

- **items_path unreadable** — items_path supplied but unreadable or not a file; stop, quote the path, do not proceed.
- **Malformed table row** — a row in the items_path table does not have the pipe-separated id|path|epic|title shape; stop, quote the offending row, do not proceed.
- **Bad seats value** — seats is not a positive integer; stop, quote the bad value, do not proceed.
- **Empty item set** — both items and items_path resolve to zero entries; stop, report an empty item set, do not emit an empty cross-product silently.
