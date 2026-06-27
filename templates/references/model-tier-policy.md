# Model-tier policy

The model a component runs on is chosen by the **nature of its work**, not by which pipeline
or occasion invokes it. This is an architectural invariant: every microskill this plugin
authors or provisions MUST declare a model tier consistent with it.

Three tiers (the alias is exactly what goes in `runtime.model`):

| Tier | alias | Use for — the component's dominant nature |
|------|-------|-------------------------------------------|
| **Opus** | `opus` | Deep reasoning under ambiguity: planning, system / design authoring, adversarial review or critique, multi-constraint synthesis — judgement a cheaper model would get wrong. |
| **Sonnet** | `sonnet` | General-purpose implementation: structured generation, transformation / extraction with moderate judgement, interactive elicitation — the default when work needs competence but not the deepest reasoning. |
| **Haiku** | `haiku` | Deterministic / mechanical work: formatting, validation, fixed-schema I/O, CRUD or publish to an external target, pure data transforms, bookkeeping. No open-ended judgement. |

Rules:

- **Pick the dominant nature.** A component that mostly transforms data with a thin judgement
  step is Sonnet, not Opus. A component whose core *is* the judgement (a critic, a designer, a
  planner) is Opus.
- **Default to Sonnet** when genuinely unsure between Sonnet and Opus; choose Haiku only when
  the work is provably mechanical.
- **Opus is earned, not free.** Reserve it for plan / design / review / critique-natured work —
  never as a blanket "best model" default.
- The tier is **policy-driven, not requirement-driven**: a plan declares `runtime.model` for
  every minted microskill even when the user's requirement never mentions models.

## Worked example — this plugin's own create-pipeline components

| Component | Nature | Tier |
|-----------|--------|------|
| `task-plan` / `*-planner` agents | planning | **opus** |
| `review-dimension` | quality review | **opus** |
| `verify-finding` | adversarial verification | **opus** |
| `synthesize-review` | structured join + judgement-based dedup of verified findings | **sonnet** |
| `task-implement` / `*-implementer` agents | general implementation | **sonnet** |
| `run-validators`, `bundle-draft`, `build-catalog-index` | deterministic floor | **haiku** |
| `workflow-bookkeeper` | run-state bookkeeping (CLI plumbing) | **haiku** |

## Enforcement

- `validate-microskill` **blocks** a built microskill whose `profiles/base.yaml` does not
  declare `runtime.model` as one of `opus | sonnet | haiku` (the deterministic floor — no
  unpinned components).
- The plan-stage review dimensions `plan-ms-model-tiering` / `plan-wf-model-tiering` check,
  qualitatively, that the chosen tier matches the component's nature.
- `runtime.model` takes effect when the microskill runs as a workflow `use:` node (the compiler
  passes it to the executor `agent()` call); a standalone `/microskill` dispatch ignores it.
