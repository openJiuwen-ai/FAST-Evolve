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

from evotrias.db.config import Config
from evotrias.mcp import controller as controller_module
from evotrias.mcp.controller import EvolutionController
from evotrias.mcp.dashboard import start_dashboard
from evotrias.mcp.mcp_server import mcp

_controllers: Dict[str, EvolutionController] = {}


def _controller(run_id: str) -> EvolutionController:
    """Return the cached controller for a run id.

    Controllers are lightweight facades over disk-backed state.  They still
    rebuild the success pool on each operation, so this cache is a convenience rather
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
    lineage_root_number: int = 4,
    max_concurrent_tasks: int = 4,
    task_description: str = "",
) -> Dict[str, Any]:
    """Configure evolution run parameters before starting tasks.

    Must be called BEFORE any other controller tool for the same run_id.
    Subsequent calls for the same run_id are ignored (the configuration
    is locked after the first call). ``lineage_root_number`` caps the root
    phase: at most that many tasks are minted (one root per exploration
    direction) and all must finish before evolution tasks start.
    ``max_concurrent_tasks`` is the in-flight task target in every phase.
    """
    first_time = not Config(run_id).created_at
    if first_time:
        controller_module.configure_run(
            run_id,
            max_total_tasks=max_total_tasks,
            lineage_root_number=lineage_root_number,
            max_concurrent_tasks=max_concurrent_tasks,
        )
        # Replace any controller cached before config.json existed (a tool
        # called ahead of configure_run) so it picks up the written config.
        controller = _controllers[run_id] = EvolutionController(run_id)
        controller.configure_run(task_description=task_description)
    else:
        controller = _controller(run_id)
    config = controller.config
    response = {
        "configured": first_time,
        "max_total_tasks": config.budget.max_total_tasks,
        "lineage_root_number": config.scheduler.lineage_root_number,
        "max_concurrent_tasks": config.scheduler.max_concurrent_tasks,
        "meta_evolve": _meta_evolve_config_ref(config),
        "dashboard": _dashboard_ref(run_id),
    }
    if not first_time:
        response["reason"] = "Run already configured for this run_id; configuration locked."
    return response


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
    numeric fitness to the success pool, and returns any Notes requests.
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
def evolve_check_convergence(
    run_id: str,
    concurrency: int,
) -> Dict[str, Any]:
    """Return the next scheduler action from convergence/meta-evolve state.

    ``concurrency`` is required and must be a positive integer — pass the
    ``max_concurrent_tasks`` value confirmed at ``configure_run`` time. The
    cap holds in every phase. The recorded ``lineage_root_number`` caps how
    many tasks the root phase may mint in total; once minted, all of them
    must finish before evolution tasks are created (meanwhile ``continue`` is
    returned with ``tasks_to_create`` 0).
    """
    return _controller(run_id).check_convergence(concurrency=concurrency)


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


def _dashboard_ref(run_id: str) -> Dict[str, Any]:
    """Start the optional UI without making observability run-critical."""
    try:
        server = start_dashboard(run_id)
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
