from evotrias.hooks.broker import hook_manager

# isort: split
# @register side effects
import evotrias.hooks.claude.permission_hooks  # noqa: F401
import evotrias.hooks.claude.scheduler_hooks  # noqa: F401
import evotrias.hooks.opencode.scheduler_hooks  # noqa: F401

__all__ = ["hook_manager"]
