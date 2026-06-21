---
name: assemble-evidence
base: true
description: >-
  Use after a pipeline's stages emit their per-stage record outputs and you need
  them consolidated into one evidence ledger. Reads every supplied record input
  (each an optional, null-safe whole object; a null becomes an explicit "absent"
  entry, recorded never branched on) and folds them per the ledger_scope contract
  variable, which names the expected record set, the escalation derivation, the
  gates-taken sources, and the exhaust-accept derivation. Produces ledger.md and
  ledger.json under output_dir.
---

# Assemble Evidence

## Purpose

Given a set of optional per-stage record inputs and a ledger_scope contract, read every record, consolidate them into one evidence ledger per that contract, and write ledger.md plus ledger.json under output_dir.

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| output_dir | yes | string | Absolute path to the directory where ledger.md and ledger.json are written. | — |
| requirements_evidence | no | object | Optional whole record for the requirements stage. A null is recorded as an explicit "absent" ledger entry, never branched on. | — |
| ux_evidence | no | object | Optional whole record for the UX stage. A null is recorded as an explicit "absent" ledger entry, never branched on. | — |
| design_evidence | no | object | Optional whole record for the design stage. A null is recorded as an explicit "absent" ledger entry, never branched on. | — |
| backlog_evidence | no | object | Optional whole record carrying the backlog fields supplied by the calling node. A null is recorded as an explicit "absent" ledger entry, never branched on. | — |
| classification | no | object | Optional classification record. A null is recorded as an explicit "absent" ledger entry; ledger_scope names which record set the classification implies. | — |
| classification_gate_choice | no | string | The choice recorded at the classification gate; surfaced as a gates_taken source. | — |
| classification_gate_mode | no | string | The gates_mode literal for the classification gate (e.g. auto vs human); a gates_taken source named by ledger_scope. | — |

## Steps

1. **Read scope** — Read the active ledger scope contract — the expected record set, the escalation-derivation field mapping, the gates_taken sources, the exhaust-accept derivation, and the ledger file location under output_dir — given by: {{ledger_scope}}
2. **Read records** — Read every supplied record input the scope names, recording each null as an explicit "absent" entry.
3. **Consolidate** — Consolidate the records per the scope contract into one evidence ledger, deriving escalations from the mapped record fields, the gates_taken entries from their sources, and an exhaust-accept entry for any record whose loop converged is false.
4. **Compute totals** — Compute stages_green (true only when every record the scope expects given the classification is present and green) and unresolved_count.
5. **Write ledger** — Write ledger.md (human-readable) and ledger.json (machine-readable) into output_dir, then return ledger_path, unresolved_count, stages_green, and summary.

## Output

Two files written under output_dir — ledger.md, a human-readable evidence ledger, and ledger.json, its machine-readable counterpart. The returned object carries ledger_path (the ledger.json path), unresolved_count, stages_green, and a one-line summary.

## Failure modes

- **Missing required input** — output_dir is absent; stop, name the input, do not proceed.
- **Ledger scope unresolved** — the {{ledger_scope}} contract is blank or missing; stop, name it, do not proceed (visible blank beats fabricated scope).
- **output_dir unwritable** — the directory cannot be created or written; stop, quote the path, do not proceed.
- **Malformed record input** — a supplied record is non-null but not a parseable object; stop, quote the bad value, do not proceed.
