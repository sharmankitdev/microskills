---
name: judge-artifacts
description: >-
  Use to score a set of candidate artifacts against one quality lens and rank
  them. Reads each candidate by its document_path, scores it against the
  {{judge_lens}} lens, grounded by a required requirements file and
  an optional context file, on a single read→score→rank path. Produces one
  JSON scorecard carrying the lens name, per-candidate scores with strengths and
  weaknesses, a best-first ranking, and an overall rationale.
---

# Judge Artifacts

## Purpose

Given candidate artifacts, a grounding requirements file, and a judging lens, score and rank each candidate and produce one structured scorecard.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| candidates | yes | array | Array of candidate objects, each at least {label, document_path}; label identifies the candidate and document_path is the file to read and score. | — |
| requirements_path | yes | string | Path to the grounding requirements file the candidates are judged against. Materialized by reference (materialize: file); read it as authoritative data. | — |
| context_path | no | string | Optional path to a supplementary context file that further grounds the scoring. Materialized by reference (materialize: file). | — |

## Steps

1. **Read grounding** — Read the grounding requirements file at requirements_path, and the context file at context_path when supplied.
2. **Read candidates** — Read each candidate artifact by its document_path from the candidates array.
3. **Score candidates** — Score each candidate against the {{judge_lens}} rubric, recording a numeric score plus strengths and weaknesses grounded in the requirements.
4. **Rank candidates** — Rank the candidates best-first by score.
5. **Emit scorecard** — Write a one-paragraph rationale for the ordering and emit the scorecard JSON carrying the lens name, per-candidate scores, the ranking, and the rationale.

## Output

A single JSON scorecard object returned as the result. It carries lens (the active judging lens — {{judge_lens}}), scores (per-candidate {label, document_path, score, strengths, weaknesses}), ranking (candidate labels best-first), and rationale (a short paragraph justifying the ordering).

## Failure modes

- **Missing required input** — candidates or requirements_path absent; stop, name the input, do not proceed.
- **Candidate document_path unreadable** — a candidate's document_path is unreadable or missing; stop, quote the path, do not proceed.
- **Requirements file unreadable** — requirements_path file unreadable or missing; stop, quote the path, do not proceed.
