from evotrias.hooks.broker import hook_manager

# @register side effects
import evotrias.hooks.claude.scheduler_hooks
import evotrias.hooks.opencode.scheduler_hooks

__all__ = [
    "hook_manager"
]
