"""Public MCP tool registration.

This module is the only layer that should expose FastMCP tools.  Each function
keeps the public schema small and delegates immediately to the internal
controller.  The separation is intentional: callers see only the compact
``evolve_*`` operations, while algorithms and persistence remain ordinary
Python modules.
"""

from __future__ import annotations

from typing import Any, Dict

from agent_evolve_mcp.config import load_evolution_controller_config
from agent_evolve_mcp.controller import EvolutionController
from agent_evolve_mcp.mcp_server import mcp


_controllers: Dict[str, EvolutionController] = {}


def _controller(run_id: str) -> EvolutionController:
    """Return the cached controller for a run id.

    Controllers are lightweight facades over disk-backed state.  They still
    reload SuccessDB on each operation, so this cache is a convenience rather
    than the source of truth.
    """
    controller = _controllers.get(run_id)
    if controller is None:
        controller = EvolutionController(run_id)
        _controllers[run_id] = controller
    return controller


@mcp.tool(run_in_thread=False)
def evolve_configure_run(
    run_id: str,
    max_total_tasks: int = 50,
) -> Dict[str, Any]:
    """Configure evolution run parameters before starting tasks.

    Must be called BEFORE any other evolve_* tool for the same run_id.
    Subsequent calls for the same run_id are ignored (the configuration
    is locked after the first call).
    """
    if run_id in _controllers:
        return {
            "configured": False,
            "reason": "Controller already exists for this run_id; configuration locked.",
            "max_total_tasks": _controllers[run_id].config.convergence.max_total_tasks,
        }
    config = load_evolution_controller_config(max_total_tasks=max_total_tasks)
    controller = EvolutionController(run_id, config=config)
    _controllers[run_id] = controller
    return {
        "configured": True,
        "max_total_tasks": config.convergence.max_total_tasks,
    }


@mcp.tool(run_in_thread=False)
def evolve_select_parent(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select a parent for a task and return only its id/ref.

    The controller stores the parent choice in local task context so later tool
    calls do not need the agent to repeat ``parent_id``.
    """
    return _controller(run_id).select_parent(task_id=task_id)


@mcp.tool(run_in_thread=False)
def evolve_select_inspirations(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select extra inspiration refs for a task.

    The parent is not repeated in this response.  The controller reads the
    task's cached parent choice from local task context.
    """
    return _controller(run_id).select_inspirations(task_id=task_id)


@mcp.tool(run_in_thread=False)
def evolve_record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
) -> Dict[str, Any]:
    """Record a completed task from a verifier-result file reference.

    The controller reads the JSON result, restores parent/inspiration ids from
    task context, records the attempt, and adds successful attempts with
    top-level numeric fitness to SuccessDB.
    """
    return _controller(run_id).record_attempt(
        task_id=task_id,
        verification_result_ref=verification_result_ref,
    )


@mcp.tool(run_in_thread=False)
def evolve_check_convergence(run_id: str) -> Dict[str, Any]:
    """Return only whether the host should stop submitting new tasks."""
    return _controller(run_id).check_convergence()


@mcp.tool(run_in_thread=False)
def evolve_finalize_run(run_id: str, stop_reason: str = "") -> Dict[str, Any]:
    """Write deterministic run summary artifacts and return only refs.

    The optional stop reason is for external stops such as user interruption.
    Normal convergence and budget stops should leave it empty so the controller
    uses the reason already stored in run state.
    """
    return _controller(run_id).finalize_run(stop_reason=stop_reason or None)
