#!/usr/bin/env python3
"""
harness-drift-check — PreToolUse hook (matcher: Skill).

Fires lazily: on the FIRST invocation, within a session, of one of the harness skills
(microskill / workflow / sync-harness). Runs `initialize-harness --plan` (read-only — writes
NOTHING) against the project + plugin catalog and, if there is drift (newly-released base
components available, or pending engine/component updates), injects a non-blocking advisory
that OFFERS to run the `initialize-harness` journey for the user — the model presents a plan,
confirms, applies, then tells them how to activate it (`/reload-skills`, or a restart only if
the engine/agents changed). The user just says the word; the hook never applies.

Design constraints:
  * In-band, not a session-start nag: only when the user actually touches the harness.
  * Once per session: a /tmp sentinel keyed by session_id. Non-target skills never consume it.
  * Non-destructive: --plan only; the advisory rides `systemMessage` (user console) +
    `additionalContext` (model context). Both are non-blocking — the call always proceeds.
  * Robust: ANY failure (no plugin root, no catalog, bad JSON, timeout) → silent exit 0. A drift
    nudge must never break a skill invocation.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TARGET_SKILLS = {"microskill", "workflow", "sync-harness"}
SENTINEL_DIR = Path("/tmp/microskills-harness-drift")
TIMEOUT_S = 8

# User-console offer (systemMessage) and the model-facing directive (additionalContext).
# Both are appended to the factual drift summary built by build_message().
USER_OFFER = " — I can run /initialize-harness to review and adopt; just say the word."
MODEL_DIRECTIVE = (
    ". Proactively offer to run this for the user now by invoking the `initialize-harness` "
    "skill — it presents a plan, asks the user to confirm, applies, then tells them how to "
    "activate it (usually `/reload-skills`; a restart only if the engine/agents changed). "
    "Ask first; do NOT block or replace the current tool call.")


def emit_advisory(summary):
    """Non-blocking PreToolUse advisory; the current tool call always proceeds.

    Two tailored channels, neither blocking:
      * `systemMessage` — the only documented field that renders on the USER's console;
        carries the drift summary plus a plain-language offer to run the journey.
      * `hookSpecificOutput.additionalContext` — model context; carries the summary plus
        an actionable directive to proactively offer to run the `initialize-harness` skill.
    """
    print(json.dumps({
        "systemMessage": summary + USER_OFFER,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": summary + MODEL_DIRECTIVE,
        },
    }))


def build_message(plan):
    bits = []
    avail = plan.get("available_base") or []
    if avail:
        names = ", ".join(b["name"] for b in avail)
        bits.append(f"{len(avail)} new base component(s) available ({names})")
    s = plan.get("summary") or {}
    pending = (s.get("add", 0) + s.get("update", 0) + s.get("remove", 0))
    if (plan.get("engine") or {}).get("action") not in (None, "noop"):
        pending_eng = " + engine update"
    else:
        pending_eng = ""
    if pending or pending_eng:
        bits.append(f"{pending} plugin component update(s){pending_eng} pending")
    if not bits:
        return None
    return "microskills harness drift: " + "; ".join(bits)


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return  # malformed input — do nothing
    skill = (payload.get("tool_input") or {}).get("skill")
    if skill not in TARGET_SKILLS:
        return  # not a harness skill — do not fire, do not consume the session slot
    session_id = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()

    # Once per session.
    try:
        SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
        sentinel = SENTINEL_DIR / session_id
        if sentinel.exists():
            return
        sentinel.touch()
    except OSError:
        return  # can't dedup safely — stay silent rather than risk repeat-nagging

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not plugin_root:
        return  # not running as an installed plugin (e.g. dogfood) — nothing to resolve
    catalog = Path(plugin_root) / "catalog"
    init = catalog / "scripts" / "initialize-harness"
    if not init.is_file():
        return

    try:
        proc = subprocess.run(
            [sys.executable, str(init), "--plan",
             "--project-root", cwd, "--catalog", str(catalog)],
            capture_output=True, text=True, timeout=TIMEOUT_S)
        plan = json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError, OSError):
        return  # any failure → silent

    msg = build_message(plan)
    if msg:
        emit_advisory(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never let a drift check break a skill call
    sys.exit(0)
