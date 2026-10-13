from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evotrias.db import paths
from evotrias.logger import logger
from evotrias.utils.filelock import blocking_filelock

_handlers: dict[tuple[str, str], list[tuple[int, Callable[..., Any], Any]]] = {}


class HookManager:
    @staticmethod
    def register(agent_type: str, event: str, extra_data: Any = None, priority: int = 0):
        def _wrap(handler: Callable[..., Any]) -> Callable[..., Any]:
            _handlers.setdefault((agent_type, event), []).append((priority, handler, extra_data))
            _handlers[(agent_type, event)].sort(key=lambda item: -item[0])
            return handler

        return _wrap

    @staticmethod
    def dispatch(agent_type: str, event: str, payload: dict) -> dict | None:
        logger.debug(f"Hook broker dispatch. {agent_type=} {event=} {payload=}")
        entries = _handlers.get((agent_type, event))
        if not entries:
            return None
        result = dict()
        # Global cross-process queue lock: dispatches from concurrent processes
        # run one at a time, in lock-acquisition order.  Handlers must never
        # re-enter dispatch (self-deadlock on the queue lock).
        with blocking_filelock(paths.HOOK_QUEUE_LOCK) as held:
            if not held:
                logger.critical(f"dispatch lock unavailable after timeout ({agent_type=}, {event=})")
                return None
            for priority, handler, extra_data in entries:
                try:
                    new_result = handler(payload, result, extra_data)
                    if new_result != result:
                        logger.info(f"Hook handler {handler.__name__}({priority}) returned: {new_result}")
                    result = new_result
                except Exception:
                    logger.error(
                        f"Error in hook handler {handler.__name__} for agent_type={agent_type}, event={event}, priority={priority}",
                        exc_info=True,
                    )
                    continue
        if not result:
            return None
        return result

    @staticmethod
    def harnesses() -> set[str]:
        """Harness names that have at least one registered handler."""
        return {harness for harness, _event in _handlers}

    @staticmethod
    def _clear() -> None:
        _handlers.clear()


hook_manager = HookManager()
