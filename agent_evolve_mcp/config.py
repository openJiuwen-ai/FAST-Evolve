"""Configuration loading for the Evolution Controller.

The MCP MVP works with built-in defaults, but this loader allows experiments to
override solver parameters from a YAML file without changing tool schemas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from agent_evolve_mcp.models import (
    ConvergenceConfig,
    EvolutionControllerConfig,
    InspirationSelectionConfig,
    ParentSelectionConfig,
)


def load_evolution_controller_config(
    config_path: Optional[str] = None,
    max_total_tasks: int = 50,
) -> EvolutionControllerConfig:
    """Load controller config from YAML plus caller-provided budget values."""
    data: Dict[str, Any] = {}
    if config_path:
        data = _load_yaml(Path(config_path))

    ps = data.get("parent_selection", {})
    insp = data.get("inspiration", {})
    conv = data.get("convergence", {})

    return EvolutionControllerConfig(
        parent_selection=ParentSelectionConfig(
            tau_start=float(ps.get("tau_start", 1.5)),
            tau_end=float(ps.get("tau_end", 0.5)),
            c_local_start=float(ps.get("c_local_start", 1.5)),
            c_local_end=float(ps.get("c_local_end", 0.5)),
            p_exploit=float(ps.get("p_exploit", 0.7)),
            quality_bonus_strength=float(ps.get("quality_bonus_strength", 0.3)),
        ),
        inspiration=InspirationSelectionConfig(
            inspiration_sample_num=int(insp.get("sample_num", insp.get("inspiration_sample_num", 3))),
            p_migrate=float(insp.get("p_migrate", 0.2)),
            foreign_elite_num=int(insp.get("foreign_elite_num", 2)),
            T_start=float(insp.get("T_start", 1.5)),
            T_end=float(insp.get("T_end", 0.5)),
        ),
        convergence=ConvergenceConfig(
            convergence_window=int(conv.get("convergence_window", 5)),
            perf_improvement_threshold=float(conv.get("perf_improvement_threshold", 0.02)),
            diversity_change_threshold=float(conv.get("diversity_change_threshold", 0.05)),
            activity_threshold=float(conv.get("activity_threshold", 0.8)),
            patience=int(conv.get("patience", 3)),
            max_total_tasks=max_total_tasks,
            max_time_seconds=conv.get("max_time_seconds"),
        ),
    )


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file if it is available."""
    import yaml

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
