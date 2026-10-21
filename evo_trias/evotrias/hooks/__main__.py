# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""``python -m evotrias.hooks <harness> <event>``: one hook entry for every harness.

stdin:  payload JSON from the harness (UTF-8), empty means ``{}``
stdout: dispatch result JSON, nothing when there is no result

Exit codes: 0 handled; 1 misconfigured entry or unreadable payload.  Never 2:
Claude Code treats exit code 2 as "block this tool call".
"""

from __future__ import annotations

import json
import sys

from evotrias.hooks import hook_manager
from evotrias.logger import logger

_USAGE = "usage: python -m evotrias.hooks <harness> <event>"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        logger.critical(f"hook entry called with bad arguments {args!r}")
        print(_USAGE, file=sys.stderr)
        return 1
    harness, event = args
    known = hook_manager.harnesses()
    if harness not in known:
        logger.critical(f"hook entry called for unknown harness {harness!r} (known: {sorted(known)})")
        print(f"unknown harness {harness!r}; expected one of {sorted(known)}", file=sys.stderr)
        return 1

    try:
        # Read bytes: the platform default stdin encoding (e.g. GBK on Chinese
        # Windows) would corrupt the UTF-8 payload the harness sends.
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw) if raw.strip() else {}
        result = hook_manager.dispatch(harness, event, payload)
    except Exception:
        logger.critical(f"hook entry failed for {harness}/{event}", exc_info=True)
        raise
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
