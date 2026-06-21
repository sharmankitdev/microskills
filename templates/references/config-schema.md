# Microskill profile YAML — schema reference

> **Canonical grammar:** [`config-schema.json`](./config-schema.json) (JSON Schema draft 2020-12). This document is a human-readable companion derived from it; when the two diverge, the JSON Schema wins. Any tool with a JSON Schema validator (ajv, python-jsonschema, etc.) can validate a profile file in one call.

Profile YAMLs live under each microskill in a dedicated `profiles/` subdirectory:

```
.claude/microskills/<name>/
  MICROSKILL.md
  profiles/
    base.yaml                # always loaded; required
    <profile>.yaml           # optional overlay, deep-merged over base
```

`base.yaml` is the baseline — every microskill ships one. Other profiles are partial specs: they declare only the keys they override and are deep-merged over base at load time. No multi-level inheritance: every overlay inherits only from base. A microskill without `profiles/base.yaml` is rejected by the resolver at load time.

**Closed grammar.** Every recognized field is enumerated below. Unknown fields at any level are rejected (`additionalProperties: false` throughout the schema), so typos and stale fields fail fast at load time rather than degrading silently.

## Top-level shape

| Path | Type | Required | Constraint |
|---|---|---|---|
| `version` | integer | yes | const `1` |
| `profile` | object | no | base-only; semantic check at resolve time |
| `vars` | object | no | flat string→string map |
| `inputs` | object | no | see [`inputs`](#inputs) |
| `steps` | object | no | see [`steps`](#steps) |
| `gates` | object | no | see [`gates`](#gates) |
| `context` | object | no | see [`context`](#context) |
| `runtime` | object | no | see [`runtime`](#runtime) |
| `output_schema` | object | no | structured-output contract; see [`output_schema`](#output_schema) |

```yaml
version: 1
profile:        { ... }   # base.yaml only
vars:           { ... }
inputs:         { ... }
steps:          { ... }
gates:          { ... }
context:        { ... }
runtime:        { ... }
output_schema:  { ... }   # structured result contract (base.yaml)
```

## Profile activation

The resolver picks the effective profile in this priority order:

1. `--profile <name>` passed on the CLI (or by the dispatcher when the caller named a profile).
2. `base.profile.default` (set in `profiles/base.yaml`).
3. `base` itself — when neither of the above is set, base.yaml alone is the effective profile.

If the effective profile is anything other than `base`, the resolver reads `profiles/<effective>.yaml` and deep-merges it over base. An unknown profile name produces a warning and falls back to base.

The caller can name a profile in two ways:

- Slash-arg: `/<skill-name> <profile>` (e.g. `/ears strict`).
- Natural language: `"run <skill-name> with <profile> profile"`.

The literal value `base` is reserved — passing `--profile base` means "use base alone, ignore any `profile.default` setting".

## Profile resolution and runtime preflight

Profile activation, deep-merge, `{{key}}` substitution, `inputs.inject_from`, and the rendered MICROSKILL.md body are produced by the preflight script at `.claude/scripts/resolve-microskill`. The runtime contract — calling the preflight, parsing its JSON, gathering missing inputs, honoring directives — is owned by the `microskill` dispatcher Skill at `.claude/skills/microskill/SKILL.md`. Per-microskill `MICROSKILL.md` files do **not** carry a `## Setup` section; the dispatcher owns it once and applies it to every microskill it dispatches. End users invoke microskills either explicitly via `/microskill <name> [profile]` or implicitly through auto-generated `.claude/commands/<name>.md` slash shims that delegate to the dispatcher.

CLI:

```
.claude/scripts/resolve-microskill <name>
    [--profile <profile>]
    [--override <dotted.path=value>]...
    [--skip-step <n>]...
    [--skill-root <dir>]
    [--inject-only | --skip-inject]
```

- `<name>` is the subdirectory under `.claude/microskills/` (override with `--skill-root` for tests).
- `--profile` selects `profiles/<profile>.yaml`. Missing file → warn + fall back to base. Omitted entirely → resolver uses `base.profile.default` if set, else base alone.
- `--override` accepts dotted paths into the merged config (`gates.foo.severity=warn`, `vars.x=y`, `context.extend=null`). Values are YAML-parsed scalars; `null` deletes the field. Repeatable.
- `--skip-step <n>` removes step `n` from the rendered body when (and only when) the merged config marks that step `optional: true`. Repeatable.
- `--inject-only` executes ONLY the `inject_from` sources and emits `injected_inputs` (no body rendering) — the execution-time companion of compile-workflow's frozen resolver payloads.
- `--skip-inject` never executes `inject_from` sources at all (compile-time mode: compile-workflow / validate-workflow pass it so the compile path is side-effect-free and env-independent). Mutually exclusive with `--inject-only`.

Stdout is a single JSON document with: `skill_name`, `profile_used` (the effective profile name, `"base"` when base alone was used), `profile_requested` (the caller's literal `--profile` argument or `null` when omitted), `warnings` (array), `config` (merged), `rendered_skill_body` (MICROSKILL.md body with `{{key}}` substituted, optional steps removed, per-step reinforcement tags inserted, step-anchored gates inlined, `## Gates (resolved)` block appended for non-inlined gates, Inputs table rewritten with profile-flipped required rows nullified and base-declared input defaults filled in), `unresolved_vars` (keys present in body but absent from `vars`), `injected_inputs` (`inject_from` resolutions), `required_inputs` (input names where merged config declares `required: true` and no `inject_from` source is DECLARED — declared-inject inputs are execution-environment-supplied, never caller-gathered, so the ledger is independent of whether the source resolves on this machine), `profile_overrides_inputs` (map of input name → `{required: true, default_nullified: true}` for inputs where the overlay flipped the declared-optional to required), `context_block` (rendered `refs`/`snippets`/`extend`), and `directives` (`allowed_tools`, `allowed_mcps`, `skip_steps`, `mandated_tools`, `gates`).

Exit codes: `0` success (possibly with non-blocking warnings) · `1` block (failed schema validation, missing `inject_from` source, missing `context.refs`/`context.extend` path, malformed override, `profile` block in non-base overlay) · `2` environment error (missing PyYAML, skill directory not found, `profiles/base.yaml` not found).

Deterministic merge rules: scalars and strings replace, maps recurse, lists replace — except `gates.add` which appends across layers (every entry has its own `id`). A `null` value in the higher layer deletes the lower layer's key.

`{{key}}` substitution leaves any unmatched placeholder verbatim and reports the key name in `unresolved_vars`; the dispatcher's setup preamble instructs the LLM to gather each missing value from the user via `AskUserQuestion` and substitute in its working copy before proceeding. This preserves "visible blank beats silent fabrication" while keeping the resolver pure.

`runtime.allowed_tools` and `runtime.allowed_mcps` are advisory — the Claude Code harness cannot hard-restrict tool calls, so the resolver emits the lists in `directives`, inlines `[ALLOWED TOOLS: …]` / `[ALLOWED MCPs: …]` reinforcement tags on every retained step, and the dispatcher's setup preamble instructs the LLM to surface a blocker rather than reach for an unlisted tool.

### Per-step inline reinforcement

The resolver also rewrites each retained step with bracketed tags that repeat profile-driven constraints at point of use. Tags appear in this order on a step's opening line, after the step number:

| Tag | Source | When emitted |
|---|---|---|
| `[REQUIRED TOOL: T]` | `steps."<n>".mandate_tool` | On the matching step. |
| `[ALLOWED TOOLS: a, b, c]` | `runtime.allowed_tools` | On every retained step when set. |
| `[ALLOWED MCPs: x, y]` | `runtime.allowed_mcps` | On every retained step when set. |
| `[INPUT <name> ∈ a\|b\|c]` | `inputs.<name>.allowed_values` | On steps whose prose contains a word-boundary match for `<name>`. |
| `[CITE SNIPPET: <name>]` | `context.snippets[].name` | On steps whose prose contains a word-boundary match for the snippet name. |

Input-name and snippet-name matches are heuristic (`\b<name>\b`, case-sensitive). Every match adds a `warnings` entry naming the step number, so authors can spot false positives.

In addition, any `gates.add[]` entry whose `after` value resolves to a numeric step id (e.g. `"3"` or `"step_3"`) is emitted inline immediately after that step's opening line as `[GATE AFTER STEP n: <id> (severity: <s>)]` rather than in the trailing `## Gates (resolved)` block. Phase-anchored gates (e.g. `after: phase_2`) and gates whose anchor step was removed via `--skip-step` continue to flow into the trailing block; orphaned anchors surface a warning so a gate is never silently dropped.

## `version`

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `version` | integer | yes | — | `const: 1` |

Identifies the schema version. Only `1` is recognized; any other value blocks.

## `profile`

Profile routing metadata. Allowed only in `profiles/base.yaml`. The resolver rejects any overlay containing a top-level `profile` block (semantic check, not schema-enforced).

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `profile` | object | no | — | closed; only `default` subfield |
| `profile.default` | string | no | — | non-empty; names a file `profiles/<value>.yaml` |

```yaml
# profiles/base.yaml
profile:
  default: strict
```

When `profile.default` is set and the caller does not supply `--profile`, the resolver loads `profiles/<default>.yaml` as the overlay. When `profile.default` is absent, base alone is the effective profile.

## `vars`

Flat map of template variable substitutions. Every key becomes available as `{{key}}` inside `MICROSKILL.md` prose and inside the generated output.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `vars.<key>` | string | — | — | values must be strings; no nesting |

```yaml
vars:
  project_name: "Atlas Checkout"
  owner: "Sarah Chen"
```

Skills may reserve specific key names (documented in the skill's own reference). User-defined keys are accepted and substituted, but do not have reserved meaning. When a key has no value, the placeholder `{{key}}` is left visible in the output and logged under any "Open Questions" section the skill produces. Visible blank beats silent fabrication.

## `inputs`

Customize entries from the skill's Inputs table. Keys are input names declared in `MICROSKILL.md`. Each entry is a **closed** object exposing exactly four axes.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `inputs.<name>` | object | no | — | closed; only the four subfields below |
| `inputs.<name>.required` | boolean | no | — | when true: adds input to `required_inputs` ledger; Setup gates execution; declared default is inert |
| `inputs.<name>.allowed_values` | array of string | no | — | `minItems: 1`, unique, each non-empty |
| `inputs.<name>.inject_from` | object | no | — | exactly one source key (see below) |
| `inputs.<name>.default` | string | no | — | default value rendered into the MICROSKILL.md Inputs table Default column. Base.yaml only — overlays must not redeclare. |
| `inputs.<name>.inject_from.git_config` | string | * | — | non-empty; runs `git config <key>` |
| `inputs.<name>.inject_from.env` | string | * | — | non-empty; reads environment variable |
| `inputs.<name>.inject_from.file` | string | * | — | non-empty; path relative to skill directory (trimmed) |
| `inputs.<name>.inject_from.command` | string | * | — | non-empty; trimmed stdout of shell command |

`*` Exactly one of `git_config | env | file | command` is required when `inject_from` is present (enforced by `oneOf`). Zero or two-or-more source keys block.

```yaml
inputs:
  owner:
    inject_from:
      git_config: user.name
  audience:
    allowed_values: [eng, pm, exec, regulator]
  approver:
    required: true
  output_path:
    default: ./out.md
```

Unresolved `inject_from` source at runtime (empty git config value, missing env var, missing file, command failure) blocks with a gap message. A caller-supplied value outside `allowed_values` blocks with a gap message.

When `required: true` is set in the merged config and the input declares no `inject_from` source, the resolver adds the input to `required_inputs` in the payload (a DECLARED `inject_from` always excludes it — the value comes from the execution environment, and an unresolvable source blocks loud at execution time rather than re-routing to caller gathering). If the overlay flipped the MICROSKILL.md-declared optional to required, the resolver also rewrites the Inputs table row to show `yes` and `—` and lists the input under `profile_overrides_inputs`. The dispatcher's setup preamble treats any default shown in the rendered Inputs table for that input as inert and uses `AskUserQuestion` when the caller's prompt does not supply a literal value.

When `default` is set (in base.yaml only) the resolver rewrites the Inputs table row's Default cell from the literal `—` to the declared value. Rows already nullified by `profile_overrides_inputs` are not overwritten — required-flipped inputs always render `—`.

## `steps`

Customize the `MICROSKILL.md` `## Steps` section. Two distinct mechanisms live under `steps`, both keyed by the step's **ORIGINAL** number.

**(a) Annotate a step in place** — numeric-string keys (`^[0-9]+$`) carrying a **closed** `{optional, mandate_tool}` object.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `steps."<n>"` | object | no | — | key must match `^[0-9]+$`; closed |
| `steps."<n>".optional` | boolean | no | — | when true, runtime skips step *n* |
| `steps."<n>".mandate_tool` | string | no | — | non-empty tool name (e.g. `WebSearch`, `Bash`, `Read`) |

```yaml
steps:
  "3":
    optional: true
    mandate_tool: WebSearch
```

When `mandate_tool` is set and the step cannot be performed with that tool, the runtime blocks rather than substituting another.

**(b) Restructure the Steps markdown** — the reserved keys `remove` / `patch` / `add` drop, rewrite, or insert steps. These are **markdown-line directives**, *not* the `deep_merge` list-verb engine used for workflow `nodes`/`gates` (see `DAG-RULES.md §11`). They are applied **REMOVE → PATCH → ADD**, after which the surviving + added steps are **renumbered contiguously**.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `steps.remove` | array<int≥1> | no | — | ORIGINAL step numbers to elide; unique; a number naming no step is an **error** |
| `steps.patch."<n>"` | object | no | — | replace step *n*'s body in place (position kept); key `^[0-9]+$`; closed |
| `steps.patch."<n>".text` | string | no | — | new step body (everything after the renumbered `N. ` prefix); single linear path only |
| `steps.add[]` | array | no | — | insert new steps; applied after remove+patch, in declared order |
| `steps.add[].after` | int≥0 | yes | — | ORIGINAL step to insert after; `0` prepends before the first surviving step; a non-`0` value naming no step is an **error** |
| `steps.add[].text` | string | yes | — | new step body; single linear path only |

```yaml
steps:
  remove: [2]                                            # drop original step 2
  patch:
    "4": { text: "Validate the payload against the schema." }
  add:
    - { after: 0, text: "Read the input contract." }     # prepend
    - { after: 5, text: "Emit the result JSON." }        # insert after original step 5
```

**Atomicity is preserved.** A patched/added `text` that introduces branching language (`if`/`else`/`for each`/`retry`/`when…then`) is **rejected at resolve time** — the same vocabulary `validate-microskill` enforces, shared via `catalog/scripts/microskill_steps.py` so the two cannot drift. More than 10 merged steps stays an advisory **warning**, not a block. A gate anchored after a *removed* step falls back to the trailing `## Gates (resolved)` block — it is not silently dropped.

## `gates`

Customize validation / approval gates. A `gates` map may carry:

- One or more **existing-gate overrides** keyed by gate id (declared in `MICROSKILL.md` with `<!-- gate-id: <id> -->` adjacent to the gate). Ids must match `^[a-z][a-z0-9_-]*$` and cannot be the literal `add`.
- A literal `add` array that **inserts new gates** the skill does not declare.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `gates.<id>` | object | no | — | id matches `^[a-z][a-z0-9_-]*$`, id ≠ `add`; closed |
| `gates.<id>.severity` | enum | no | — | `warn` \| `hard` |
| `gates.add` | array | no | — | items have shape below |
| `gates.add[].id` | string | yes | — | matches `^[a-z][a-z0-9_-]*$` |
| `gates.add[].after` | string | yes | — | step id or phase id from `MICROSKILL.md` |
| `gates.add[].type` | enum | yes | — | `human_approval` \| `verification` \| `tool_check` |
| `gates.add[].prompt` | string | conditional | — | required when `type == human_approval`; non-empty |
| `gates.add[].severity` | enum | no | `hard` | `warn` \| `hard` |

```yaml
gates:
  phase4_human_approval:
    severity: hard
  add:
    - id: review_gap_list
      after: phase_2
      type: human_approval
      prompt: "Approve gap list before proceeding?"
```

**Severity semantics.**

| Value | Meaning |
|---|---|
| `warn` | Skill emits a warning and continues. |
| `hard` | Skill blocks until the gate is satisfied. |

## `context`

Inject reference material into the skill's working context.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `context.refs` | array of string | no | — | each item non-empty path; relative to skill directory |
| `context.snippets` | array | no | — | items have shape below |
| `context.snippets[].name` | string | yes | — | matches `^[a-z][a-z0-9_-]*$` |
| `context.snippets[].text` | string | yes | — | non-empty |
| `context.extend` | string | no | — | non-empty path to existing artifact (relative to skill directory) |

```yaml
context:
  refs:
    - ./docs/style-guide.md
  snippets:
    - name: regulatory_constraint
      text: "Section 5.2 of compliance doc applies."
  extend: ./requirements/v0.1.md
```

- **`refs`** read as authoritative reference at runtime; constrain vocabulary, naming, and prior decisions but do not by themselves create output. Missing path blocks.
- **`snippets`** are short inline facts folded in where relevant (regulatory clauses, inherited contracts, prior decisions). The skill cites by `name`.
- **`extend`** points at an existing output artifact to revise in place: skill writes to that path (not the default location); existing identifiers preserved verbatim; new entries append; removed entries marked `(REMOVED)` in place; ids never reused. Missing path blocks.

## `runtime`

Tool & MCP restrictions.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `runtime.allowed_tools` | array of string | no | — | each item a non-empty tool name |
| `runtime.allowed_mcps` | array of string | no | — | each item a non-empty MCP server id |

```yaml
runtime:
  allowed_tools: [Read, Write, WebSearch]
  allowed_mcps:  [linear, github]
```

When `allowed_tools` is present, the skill confines itself to only those tool names during the workflow. If a phase appears to need a tool that's not allowed, the skill surfaces a blocker rather than working around it. If absent, the skill uses tools at its discretion.

When `allowed_mcps` is absent or empty, the skill does not query any MCP. The skill cites every MCP result it embeds.

## `output_schema`

Optional JSON Schema fragment declaring the microskill's **structured output contract**. Belongs in `base.yaml` — it is a skill-level contract, not a profile knob.

| Path | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `output_schema` | object | no | — | `minProperties: 1`; a JSON Schema fragment (free-form inside) |

```yaml
output_schema:
  type: object
  required: [keys]
  properties:
    keys:
      type: array
      items: { type: string }
```

When present, the resolver (1) surfaces `output_schema` as a top-level field in its JSON payload and (2) appends an `## Output (required structured result)` directive to `rendered_skill_body` instructing the skill to return **only** a JSON object matching this schema (no prose, no fences). This makes the skill emit structured output whether run standalone (via the dispatcher) or composed inside a workflow `use:` node — a node can then rely on the declared shape, and `validate-workflow` checks the node's `output_schema` against it.

Declare it when the microskill returns structured/consumable data (a list, a verdict, extracted fields), or a minimal `{ <path-field>: { type: string } }` when the artifact is a file the caller chains on. Omit it for purely human-facing prose with no composable handle. For an interactive skill, the directive governs only the **final** result, after any Q&A.

## Conflict resolution — config vs invocation prompt

When the invocation prompt disagrees with the active (merged) config:

- **Config wins** for: file paths, `vars`, gate severity, tool restrictions, step optionality, allowed-values.
- **Prompt wins** for: substantive content, runtime answers to clarification questions.
- Explicit per-invocation override: a prompt clause `override microskill-config: <field>=<value>` supersedes the named field for that invocation only. Examples:
  - `override microskill-config: template.depth = lean`
  - `override microskill-config: gates.phase4_human_approval.severity = warn`
  - `override microskill-config: context.extend = null`

Overrides apply only to the current invocation; profile files are not modified.

## Validation rules

Two layers run before the skill starts work:

1. **Grammar validation** — the JSON Schema (`config-schema.json`) is applied to the merged config. Failures are syntactic and *block*.
2. **Semantic validation** — identifiers referenced in the config are cross-checked against `MICROSKILL.md`. Failures here are usually *warnings* (forward-compatible drift) unless they introduce ambiguity (id collisions or layer violations).

| Condition | Layer | Outcome |
|---|---|---|
| `version` ≠ `1` | grammar | block |
| Unknown field at any level (typo, stale key) | grammar | block |
| `inputs.<name>.inject_from` declares 0 or >1 source keys | grammar | block |
| `inputs.<name>.allowed_values` empty | grammar | block |
| `steps` key is neither `^[0-9]+$` nor `add`/`patch`/`remove` | grammar | block |
| `gates.<id>` id is the literal `add` or fails regex | grammar | block |
| `gates.add[].type == human_approval` without `prompt` | grammar | block |
| `context.snippets[]` missing `name` or `text` | grammar | block |
| `profiles/base.yaml` not found | load | resolves with an **empty overlay** (no error) — `base` alone applies |
| Top-level `profile` block in a non-base overlay | semantic | block (exit 1) |
| Unknown `inputs.<name>` (no such input declared in `MICROSKILL.md`) | semantic | warn |
| `inputs.<name>.required: true` for `<name>` not in `MICROSKILL.md` Inputs table | semantic | warn — input NOT added to `required_inputs` ledger |
| Unknown step number in `steps` annotate form (skill has no such step) | semantic | warn |
| `steps.remove`/`steps.patch`/`steps.add[].after` names a non-existent step | resolve | block (clean JSON error; `validate-microskill` surfaces it earlier as a warn) |
| A `steps` `add`/`patch` `text` contains branching language (`if`/`for each`/…) | resolve | block (clean JSON error) |
| >10 merged steps after `steps` add/remove | resolve | warn (advisory) |
| Unknown gate id in `gates` (non-`add`; skill has no such gate) | semantic | warn |
| `gates.add[].id` collides with an existing gate id | semantic | block |
| `inputs.<name>.inject_from` source unresolvable at runtime | runtime | block — surface gap |
| `context.refs[*]` path missing at runtime | runtime | block |
| `context.extend` path missing at runtime | runtime | block |
| `runtime.allowed_tools[*]` / `allowed_mcps[*]` not non-empty strings | grammar | block |
| Caller-named profile file missing | load | warn, fall back to base |

## Full example

```yaml
# profiles/base.yaml
version: 1

profile:
  default: strict

vars:
  project_name: "Atlas Checkout"
  owner: "Sarah Chen"

inputs:
  owner:
    inject_from:
      git_config: user.name
  audience:
    allowed_values: [eng, pm, exec, regulator]
  output_path:
    default: ./out.md

context:
  refs:
    - ./docs/style-guide.md
  snippets:
    - name: regulatory_constraint
      text: "Section 5.2 of compliance doc applies."

runtime:
  allowed_tools: [Read, Write, WebSearch]
  allowed_mcps:  [linear]
```

```yaml
# profiles/strict.yaml — overlay, deep-merged over base
version: 1

inputs:
  approver:
    required: true

gates:
  phase4_human_approval:
    severity: hard
  add:
    - id: review_gap_list
      after: phase_2
      type: human_approval
      prompt: "Approve gap list before proceeding?"
```
