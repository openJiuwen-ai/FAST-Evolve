#!/usr/bin/env python3
"""Thin hook entry for the opencode harness.

Spawned by .opencode/plugins/evotrias-bridge.ts once per forwarded event:

    uv run python .opencode/plugins/hooks_entry.py <event>
      stdin:  payload JSON (native opencode structure, verbatim)
      stdout: dispatch result JSON (native shape, empty when no result)
"""

import json
import sys

from evotrias.hooks import hook_manager


def main() -> int:
    event = sys.argv[1]
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    result = hook_manager.dispatch("opencode", event, payload)
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
