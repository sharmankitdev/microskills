"""
microskill_steps — the single source of truth for a microskill's linear-path
"## Steps" vocabulary and atomicity cap.

Both `validate-microskill` (author-time lint) and `resolve-microskill` (runtime
profile resolution, which may now add/patch step text) import this module via
importlib SourceFileLoader so the branching-language vocabulary and the step
cap cannot drift between the two tools. If a profile adds or patches a step that
introduces branching language, the resolver must reject it with exactly the same
regex the validator uses — otherwise a profile could smuggle a conditional past
the author-time lint.

Exports:
  STEP_RE     — matches the start of a markdown numbered step line ("1.").
  BRANCH_RE   — matches branching / control-flow language that breaks the single
                linear path (if / else / for each / repeat / retry / when…then …).
  STEP_CAP    — advisory atomicity cap; >STEP_CAP numbered steps is a WARN (never
                a block) in both tools.
  count_steps(steps_body)        — number of numbered steps in a Steps block body.
  find_branch_language(text)     — list of (snippet, span) for each branch match,
                                   snippet trimmed for human-readable messages.
"""

import re

# A markdown numbered-step line start: "1." / "12." at the start of a line.
STEP_RE = re.compile(r"^\d+\.", re.MULTILINE)

# Branching / control-flow vocabulary. A microskill's Steps must be a single
# linear path — no branches, conditionals, loops, or parallel tracks.
BRANCH_RE = re.compile(
    r"\b("
    r"if\b|else\b|for each\b|repeat\b|retry\b|when\b.*then\b|based on\b|"
    r"depending on|either\b|go to step|otherwise|or if|unless"
    r")\b",
    re.IGNORECASE,
)

# Advisory atomicity cap. More than this many numbered steps WARNS (does not
# block) in both validate-microskill and resolve-microskill.
STEP_CAP = 10


def count_steps(steps_body):
    """Return the number of markdown numbered steps in a Steps block body."""
    if not steps_body:
        return 0
    return len(STEP_RE.findall(steps_body))


def find_branch_language(text):
    """Return a list of (snippet, (start, end)) for each branching-language match
    in `text`. The snippet is a short, newline-collapsed window around the match,
    suitable for an error message."""
    out = []
    for m in BRANCH_RE.finditer(text or ""):
        snippet = (
            m.string[max(0, m.start() - 20): m.end() + 20].replace("\n", " ").strip()
        )
        out.append((snippet, (m.start(), m.end())))
    return out
