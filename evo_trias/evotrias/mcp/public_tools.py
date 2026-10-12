"""MCP tool registration for evotrias.

This is the only layer that exposes FastMCP tools, and it stays a thin shell:
each tool declares the agent-facing contract (argument names, defaults,
docstring) and forwards to ``evotrias.evolve_ctrl.controller`` with every
argument filled in.  ``configure_run`` is the exception — it assembles the
two flat dicts the controller expects and starts the optional dashboard.

Python wrapper names keep an ``evolve_*`` prefix for direct-import
compatibility (tests call ``public_tools_module.evolve_*``); the registered
name is the prefix-free one passed to ``register_tool``.

Every tool goes through ``register_tool``: it logs inputs/outputs at DEBUG,
logs failures at ERROR, and re-raises so the exception travels FastMCP's
normal error channel.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, Dict

from evotrias.dashboard.server import start_dashboard
from evotrias.evolve_ctrl import controller as ctrl
from evotrias.logger import logger
from evotrias.mcp.mcp_server import mcp


def register_tool(name: str) -> Callable:
    """Register an ``evolve_*`` function as an MCP tool with logging.

    The tool docstring is the agent-facing description; the function body is
    forwarded untouched.  Inputs, output and duration are logged at DEBUG, and
    an exception is logged at ERROR with the traceback, then re-raised so
    FastMCP surfaces it through the protocol error channel.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            logger.debug(f"tool {name} <- args={args!r} kwargs={kwargs!r}")
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                logger.error(f"tool {name} failed: {type(exc).__name__}: {exc}", exc_info=True)
                raise
            elapsed = time.monotonic() - started
            logger.debug(f"tool {name} -> {result!r} took {elapsed:.3f}s")
            return result

        return mcp.tool(name=name, run_in_thread=False)(guarded)

    return decorator


@register_tool("configure_run")
def evolve_configure_run(
    run_id: str,
    max_total_tasks: int = 50,
    lineage_root_number: int = 4,
    max_concurrent_tasks: int = 4,
    task_description: str = "",
) -> Dict[str, Any]:
    """Configure evolution run parameters before starting tasks.

    Must be called BEFORE any other controller tool for the same run_id;
    the first call writes ``config.json`` and later calls are ignored (the
    configuration is locked). Returns ``configured``, a ``message``, and the
    recorded knobs.
    """
    configured, config, message = ctrl.configure_run(
        run_id,
        task_info={"task_description": task_description},
        configs={
            "max_total_tasks": max_total_tasks,
            "lineage_root_number": lineage_root_number,
            "max_concurrent_tasks": max_concurrent_tasks,
        },
    )
    return {
        "configured": configured,
        "message": message,
        "max_total_tasks": config.budget.max_total_tasks,
        "lineage_root_number": config.scheduler.lineage_root_number,
        "max_concurrent_tasks": config.scheduler.max_concurrent_tasks,
        "meta_evolve": _meta_evolve_config_ref(config),
        "dashboard": _dashboard_ref(run_id),
    }


@register_tool("select_parent")
def evolve_select_parent(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select a parent and return its id/ref, fitness, and evaluator metrics.

    The controller stores the parent choice in local task context so later tool
    calls do not need the agent to repeat ``parent_id``.
    """
    return ctrl.select_parent(run_id, task_id)


@register_tool("select_inspirations")
def evolve_select_inspirations(run_id: str, task_id: str) -> Dict[str, Any]:
    """Select extra inspiration refs with fitness and evaluator metrics.

    The parent is not repeated in this response.  The controller reads the
    task's cached parent choice from local task context.
    """
    return ctrl.select_inspirations(run_id, task_id)


@register_tool("record_attempt")
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
    return ctrl.record_attempt(
        run_id,
        task_id,
        verification_result_ref,
        attempt_id=attempt_id or None,
        artifact_ref=artifact_ref or None,
        max_attempts=max_attempts,
    )


@register_tool("get_notes_prompt")
def evolve_get_notes_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return run-local Notes as a ready-to-inject prompt section."""
    return ctrl.get_notes_prompt(run_id, task_id)


@register_tool("commit_generated_note")
def evolve_commit_generated_note(
    run_id: str,
    note_request_id: str,
    generated_note_json: Any,
) -> Dict[str, Any]:
    """Validate and commit one LLM-generated run-local note."""
    return ctrl.commit_generated_note(run_id, note_request_id, generated_note_json)


@register_tool("get_meta_guidance_request")
def evolve_get_meta_guidance_request(
    run_id: str,
    request_id: str = "",
) -> Dict[str, Any]:
    """Return a pending meta-guidance request for the dedicated worker."""
    return ctrl.get_meta_guidance_request(run_id, request_id)


@register_tool("commit_meta_guidance")
def evolve_commit_meta_guidance(
    run_id: str,
    request_id: str,
    guidance_json: Any,
) -> Dict[str, Any]:
    """Validate and activate structured meta-guidance."""
    return ctrl.commit_meta_guidance(run_id, request_id, guidance_json)


@register_tool("get_meta_guidance_prompt")
def evolve_get_meta_guidance_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return a ready-to-inject meta-guidance prompt section for a task."""
    return ctrl.get_meta_guidance_prompt(run_id, task_id)


@register_tool("check_convergence")
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
    return ctrl.check_convergence(run_id, concurrency)


@register_tool("finalize_run")
def evolve_finalize_run(run_id: str, stop_reason: str = "") -> Dict[str, Any]:
    """Write deterministic run summary artifacts and return only refs.

    The optional stop reason is for external stops such as user interruption.
    Normal convergence and budget stops should leave it empty so the controller
    uses the reason already stored in run state.
    """
    return ctrl.finalize_run(run_id, stop_reason or None)


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
            "reason": "EVOTRIAS_UI_ENABLED disables automatic startup.",
        }
    return {
        "enabled": True,
        "started": True,
        "url": server.url,
        "port": server.port,
    }
