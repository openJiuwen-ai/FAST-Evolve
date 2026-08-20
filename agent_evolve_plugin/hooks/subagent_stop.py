#!/usr/bin/env python3
import json
import sys

from evotrias.hooks import hook_manager


def main() -> int:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    result = hook_manager.dispatch("claude", "subagent_stop", payload)
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
