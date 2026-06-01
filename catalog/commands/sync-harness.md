---
description: >
  Reconcile the vendored harness/ into the runtime .claude/ — preview add/update/remove,
  confirm, then apply, touching only harness-managed components and never other .claude/ contents.
---

Invoke the `sync-harness` Skill, passing these caller arguments: $ARGUMENTS.

The dispatcher will run `.claude/scripts/harness-sync --plan`, present the reconcile
actions and any conflicts, confirm, then run `--apply` and report what changed.
