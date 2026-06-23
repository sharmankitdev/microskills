---
name: <kebab-case-name>
description: <When to trigger + what this does. Keep name + description together ≤ 100 words. Copied verbatim into the auto-generated slash-shim `.claude/commands/<name>.md` so tool-discovery surfaces the same trigger text.>
---

# <Microskill Title>

## Purpose

<One sentence: given <input>, do <action>, produce <output>.>

## Inputs

| Name | Required | Type | Description | Default |
|---|---|---|---|---|
| <input_1> | yes | string | <what it represents> | — |
| <input_2> | no  | string | <what it represents> | — |

## Steps

1. **<verb-led step name>** — <atomic action>. Uses <input_1>.
2. **<verb-led step name>** — <atomic action>. Uses <input_2>.
3. **<verb-led step name>** — <atomic action>.

<!-- Internals are a black box: branches, loops, tool use, even a human gate (declare
     AskUserQuestion in runtime.allowed_tools) are all fine. A microskill graduates to a
     workflow when it must compose OTHER components — call another microskill/agent/workflow,
     or orchestrate a multi-node graph. -->

## Output

<Describe the artifact: format, where it lands, what fields it contains.>

## Failure modes

- **Missing required input** — stop, name the input, do not proceed.
- **<input> malformed** — stop, quote the bad value, do not proceed.
