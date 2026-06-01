---
name: greet-user
description: >
  Emit a short personalized greeting for a named person. Minimal custom harness
  demo component used to prove the harness-sync reconcile loop. Produces one greeting line.
---

# Greet User

## Purpose

Given a person's name, produce a one-line friendly greeting.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| person | no | string | Who to greet | — |

## Steps

1. **Read the name** — take `person` from inputs; if absent or empty, use `there`.
2. **Greet** — return exactly one line: `Hello, <person>! Welcome aboard.`

## Output

A single greeting line. No prose before or after it.

## Failure modes

- **Empty name after gathering** — if `person` resolves empty, greet `there` rather than stopping.
