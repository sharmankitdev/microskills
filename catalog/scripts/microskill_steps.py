"""
microskill_steps — the single source of truth for a microskill's "## Steps"
numbered-step vocabulary and advisory atomicity cap.

Both `validate-microskill` (author-time lint) and `resolve-microskill` (runtime
profile resolution, which may add/patch step text) import this module via
importlib SourceFileLoader so the numbered-step pattern and the atomicity cap
cannot drift between the two tools.

Internal control flow is no longer linted — a microskill body's internals are
its own concern; the compiler owns orchestration placement (a microskill exposing AskUserQuestion
runs at an orchestrator checkpoint; the compile dies if mis-placed in a
background segment). This module therefore carries only the numbered-step
pattern and the advisory atomicity cap, with no control-flow opinion.

Exports:
  STEP_RE     — matches the start of a markdown numbered step line ("1.").
  STEP_CAP    — advisory atomicity cap; >STEP_CAP numbered steps is a WARN (never
                a block) in both tools.
  count_steps(steps_body)        — number of numbered steps in a Steps block body.
"""

import re

# A markdown numbered-step line start: "1." / "12." at the start of a line.
STEP_RE = re.compile(r"^\d+\.", re.MULTILINE)

# Advisory atomicity cap. More than this many numbered steps WARNS (does not
# block) in both validate-microskill and resolve-microskill.
STEP_CAP = 10


def count_steps(steps_body):
    """Return the number of markdown numbered steps in a Steps block body."""
    if not steps_body:
        return 0
    return len(STEP_RE.findall(steps_body))
