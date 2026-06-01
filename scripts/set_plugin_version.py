#!/usr/bin/env python3
"""Stamp a semver into .claude-plugin/plugin.json (the canonical plugin version).

Invoked by semantic-release (@semantic-release/exec prepareCmd) as:
    python scripts/set_plugin_version.py <version>
Fails non-zero on a bad version or unreadable file rather than writing garbage.
"""
import json
import re
import sys
from pathlib import Path

PLUGIN_JSON = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
SEMVER = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def set_version(version: str, path: Path = PLUGIN_JSON) -> None:
    if not SEMVER.match(version):
        raise ValueError(f"not a valid semver: {version!r}")
    data = json.loads(path.read_text())
    data["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv: list) -> int:
    if len(argv) != 2:
        print("usage: set_plugin_version.py <semver>", file=sys.stderr)
        return 2
    try:
        set_version(argv[1])
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
