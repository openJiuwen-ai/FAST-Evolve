"""Public MCP tool registration.

This module is the only layer that should expose FastMCP tools.  Each function
keeps the public schema small and delegates immediately to the internal
controller. The separation is intentional: MCP callers see compact operations,
while algorithms and persistence remain ordinary Python modules. Python wrapper
names retain their ``evolve_*`` prefix for direct-import compatibility; explicit
MCP registration names omit that redundant prefix.
"""

from __future__ import annotations

from typing import Any, Dict

from agent_evolve_mcp.config import load_evolution_controller_config
from agent_evolve_mcp.controller import EvolutionController
from agent_evolve_mcp.dashboard import start_dashboard
from agent_evolve_mcp.mcp_server import mcp
from agent_evolve_mcp.notes import notes_enabled
from agent_evolve_mcp.scheduler_state import (
    get_scheduler_state,
    initialize_scheduler_state,
    update_task_status,
)

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


@mcp.tool(name="configure_run", run_in_thread=False)
def evolve_configure_run(
    run_id: str,
    max_total_tasks: int = 50,
    task_description: str = "",
) -> Dict[str, Any]:
    """Configure evolution run parameters before starting tasks.

    Must be called BEFORE any other controller tool for the same run_id.
    Subsequent calls for the same run_id are ignored (the configuration
    is locked after the first call).
    """
    initialize_scheduler_state(run_id)
    if run_id in _controllers:
        config = _controllers[run_id].config
        return {
            "configured": False,
            "reason": "Controller already exists for this run_id; configuration locked.",
            "max_total_tasks": config.budget.max_total_tasks,
            "meta_evolve": _meta_evolve_config_ref(config),
            "dashboard": _dashboard_ref(
                run_id,
                max_total_tasks=config.budget.max_total_tasks,
                task_description=task_description,
                meta_guidance_enabled=config.meta_evolve.enabled,
                notes_feature_enabled=notes_enabled(),
            ),
        }
    config = load_evolution_controller_config(max_total_tasks=max_total_tasks)
    controller = EvolutionController(run_id, config=config)
    controller.configure_run(task_description=task_description)
    _controllers[run_id] = controller
    return {
        "configured": True,
        "max_total_tasks": config.budget.max_total_tasks,
        "meta_evolve": _meta_evolve_config_ref(config),
        "dashboard": _dashboard_ref(
            run_id,
            max_total_tasks=config.budget.max_total_tasks,
            task_description=task_description,
            meta_guidance_enabled=config.meta_evolve.enabled,
            notes_feature_enabled=notes_enabled(),
        ),
    }


@mcp.tool(name="update_task_status", run_in_thread=False)
def evolve_update_task_status(
    run_id: str,
    task_id: str,
    status: str,
    agent_id: str = "",
) -> Dict[str, Any]:
    """Persist one scheduler task transition without exposing the state-file diff."""
    return update_task_status(
        run_id=run_id,
        task_id=task_id,
        status=status,
        agent_id=agent_id,
    )


@mcp.tool(name="get_scheduler_state", run_in_thread=False)
def evolve_get_scheduler_state(run_id: str) -> Dict[str, Any]:
    """Return only active tasks and agents for scheduling recovery."""
    return get_scheduler_state(run_id)


@mcp.tool(name="select_parent", run_in_thread=False)
def evolve_select_parent(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select a parent and return its id/ref, fitness, and evaluator metrics.

    The controller stores the parent choice in local task context so later tool
    calls do not need the agent to repeat ``parent_id``.
    """
    return _controller(run_id).select_parent(task_id=task_id)


@mcp.tool(name="select_inspirations", run_in_thread=False)
def evolve_select_inspirations(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select extra inspiration refs with fitness and evaluator metrics.

    The parent is not repeated in this response.  The controller reads the
    task's cached parent choice from local task context.
    """
    return _controller(run_id).select_inspirations(task_id=task_id)


@mcp.tool(name="record_attempt", run_in_thread=False)
def evolve_record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
    attempt_id: str = "",
    artifact_ref: str = "",
    max_attempts: int = 3,
) -> Dict[str, Any]:
    """Record one completed attempt from a verifier-result file reference.

    The controller reads the JSON result, restores parent/inspiration ids from
    task context, records the attempt, adds successful attempts with top-level
    numeric fitness to SuccessDB, and returns any Notes requests.
    """
    return _controller(run_id).record_attempt(
        task_id=task_id,
        verification_result_ref=verification_result_ref,
        attempt_id=attempt_id or None,
        artifact_ref=artifact_ref or None,
        max_attempts=max_attempts,
    )


@mcp.tool(name="get_notes_prompt", run_in_thread=False)
def evolve_get_notes_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return run-local Notes as a ready-to-inject prompt section."""
    return _controller(run_id).get_notes_prompt(task_id=task_id)


@mcp.tool(name="commit_generated_note", run_in_thread=False)
def evolve_commit_generated_note(
    run_id: str,
    note_request_id: str,
    generated_note_json: Any,
) -> Dict[str, Any]:
    """Validate and commit one LLM-generated run-local note."""
    return _controller(run_id).commit_generated_note(
        note_request_id=note_request_id,
        generated_note_json=generated_note_json,
    )


@mcp.tool(name="get_meta_guidance_request", run_in_thread=False)
def evolve_get_meta_guidance_request(
    run_id: str,
    request_id: str = "",
) -> Dict[str, Any]:
    """Return a pending meta-guidance request for the dedicated worker."""
    return _controller(run_id).get_meta_guidance_request(request_id=request_id)


@mcp.tool(name="commit_meta_guidance", run_in_thread=False)
def evolve_commit_meta_guidance(
    run_id: str,
    request_id: str,
    guidance_json: Any,
) -> Dict[str, Any]:
    """Validate and activate structured meta-guidance."""
    return _controller(run_id).commit_meta_guidance(
        request_id=request_id,
        guidance_json=guidance_json,
    )


@mcp.tool(name="get_meta_guidance_prompt", run_in_thread=False)
def evolve_get_meta_guidance_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return a ready-to-inject meta-guidance prompt section for a task."""
    return _controller(run_id).get_meta_guidance_prompt(task_id=task_id)


@mcp.tool(name="check_convergence", run_in_thread=False)
def evolve_check_convergence(run_id: str) -> Dict[str, Any]:
    """Return the next scheduler action from convergence/meta-evolve state."""
    return _controller(run_id).check_convergence()


@mcp.tool(name="finalize_run", run_in_thread=False)
def evolve_finalize_run(run_id: str, stop_reason: str = "") -> Dict[str, Any]:
    """Write deterministic run summary artifacts and return only refs.

    The optional stop reason is for external stops such as user interruption.
    Normal convergence and budget stops should leave it empty so the controller
    uses the reason already stored in run state.
    """
    return _controller(run_id).finalize_run(stop_reason=stop_reason or None)


def _meta_evolve_config_ref(config: Any) -> Dict[str, Any]:
    return {
        "enabled": config.meta_evolve.enabled,
        "guidance_num_to_generate": config.meta_evolve.guidance_num_to_generate,
        "guidance_max_uses": config.meta_evolve.guidance_max_uses,
    }


def _dashboard_ref(
    run_id: str,
    *,
    max_total_tasks: int,
    task_description: str,
    meta_guidance_enabled: bool,
    notes_feature_enabled: bool,
) -> Dict[str, Any]:
    """Start the optional UI without making observability run-critical."""
    try:
        server = start_dashboard(
            run_id,
            max_total_tasks=max_total_tasks,
            task_description=task_description,
            feature_flags={
                "meta_guidance": meta_guidance_enabled,
                "notes": notes_feature_enabled,
            },
        )
    except (OSError, RuntimeError) as exc:
        return {
            "enabled": True,
            "started": False,
            "error": str(exc),
        }
    if server is None:
        return {
            "enabled": False,
            "started": False,
            "reason": "AGENT_EVOLVE_UI_ENABLED disables automatic startup.",
        }
    return {
        "enabled": True,
        "started": True,
        "url": server.url,
        "port": server.port,
    }
