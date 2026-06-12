---
name: refute-story-quality
description: Use when you hold one user story and the name of a single quality criterion, and need an adversarial seat to test whether the story truly meets it. Selects that criterion's section from the built-in rubric, builds the strongest violation case and the strongest compliance case from the story's text and acceptance criteria, weighs them, defaults to refuted on ambiguous evidence, and produces one seat-stamped JSON verdict carrying refuted, severity, rationale, confidence, and a needs_human flag.
---

# Refute Story Quality

## Purpose

Given a user story and a criterion name, adversarially weigh violation against compliance evidence and produce one seat-stamped quality verdict.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| story | yes | object | The user story under test — carries id, path or text, epic, and title. When path is given, read the story text from it; otherwise use the inline text. | — |
| criterion | yes | string | The single quality criterion to test; one of the criterion names the rubric var defines (base = the six INVEST names). Selects which rubric section is read as data. | — |
| seat | no | integer | Adversarial seat number echoed verbatim in the verdict; absent means single-judge mode and the verdict seat is null. | — |

## Steps

1. **Read story** — Resolve the story text by reading story.path, falling back to story.text inline.
2. **Select rubric section** — Select the rubric section for criterion from the built-in invest_rubric contract variable.
3. **Build violation case** — Build the strongest violation case against the story using only the story text and its acceptance criteria, grounded in that rubric section.
4. **Build compliance case** — Build the strongest compliance case for the story from the same evidence.
5. **Weigh evidence** — Weigh the two cases on the story evidence, defaulting to refuted when the evidence is ambiguous.
6. **Emit verdict** — Emit the seat-stamped JSON verdict carrying refuted, severity, rationale, confidence, and needs_human.

## Output

A single JSON object returned as the result (not written to a file), carrying the story id, the tested criterion, the echoed seat (or null), the refuted boolean, a severity (blocker|major|minor, or null when not refuted), a rationale, a confidence band, and a needs_human flag. A caller fanning seats over a story consumes this verdict directly.

## Failure modes

- **Missing required input** — story or criterion is absent; stop, name the input, do not proceed.
- **criterion not a rubric-defined name** — the value names no section in the rubric var; stop, quote the bad value, do not proceed.
- **Story text unavailable** — story.path is given but unreadable and no inline text is present; stop, quote the path, do not proceed.
