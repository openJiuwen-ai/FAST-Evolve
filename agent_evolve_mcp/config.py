"""Configuration loading for the Evolution Controller.

The MCP MVP works with built-in defaults, but this loader allows experiments to
override solver parameters from a YAML file without changing tool schemas.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from agent_evolve_mcp.models import (
    ConvergenceConfig,
    EvolutionControllerConfig,
    InspirationSelectionConfig,
    MetaEvolveConfig,
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
    meta = data.get("meta_evolve", {})
    parent_defaults = ParentSelectionConfig()
    inspiration_defaults = InspirationSelectionConfig()
    convergence_defaults = ConvergenceConfig(max_total_tasks=max_total_tasks)
    meta_defaults = MetaEvolveConfig()

    return EvolutionControllerConfig(
        parent_selection=ParentSelectionConfig(
            tau_start=float(ps.get("tau_start", parent_defaults.tau_start)),
            tau_end=float(ps.get("tau_end", parent_defaults.tau_end)),
            c_local_start=float(ps.get("c_local_start", parent_defaults.c_local_start)),
            c_local_end=float(ps.get("c_local_end", parent_defaults.c_local_end)),
            p_exploit=float(ps.get("p_exploit", parent_defaults.p_exploit)),
            quality_bonus_strength=float(
                ps.get("quality_bonus_strength", parent_defaults.quality_bonus_strength)
            ),
            elite_rank_fraction=float(
                ps.get("elite_rank_fraction", parent_defaults.elite_rank_fraction)
            ),
            elite_selection_shield=float(
                ps.get("elite_selection_shield", parent_defaults.elite_selection_shield)
            ),
        ),
        inspiration=InspirationSelectionConfig(
            inspiration_sample_num=int(
                insp.get(
                    "sample_num",
                    insp.get("inspiration_sample_num", inspiration_defaults.inspiration_sample_num),
                )
            ),
            p_migrate=float(insp.get("p_migrate", inspiration_defaults.p_migrate)),
            foreign_elite_num=int(insp.get("foreign_elite_num", inspiration_defaults.foreign_elite_num)),
            T_start=float(insp.get("T_start", inspiration_defaults.T_start)),
            T_end=float(insp.get("T_end", inspiration_defaults.T_end)),
        ),
        convergence=ConvergenceConfig(
            enabled=_bool_from_env_or_config(
                "AGENT_EVOLVE_CONVERGENCE_ENABLED",
                conv.get("enabled", conv.get("enable_convergence", convergence_defaults.enabled)),
            ),
            convergence_window=int(
                conv.get("convergence_window", convergence_defaults.convergence_window)
            ),
            perf_improvement_threshold=_float_from_env_or_config(
                "AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD",
                conv.get("perf_improvement_threshold", convergence_defaults.perf_improvement_threshold),
            ),
            diversity_change_threshold=float(
                conv.get("diversity_change_threshold", convergence_defaults.diversity_change_threshold)
            ),
            activity_threshold=float(
                conv.get("activity_threshold", convergence_defaults.activity_threshold)
            ),
            patience=int(conv.get("patience", convergence_defaults.patience)),
            max_total_tasks=max_total_tasks,
            max_time_seconds=conv.get("max_time_seconds", convergence_defaults.max_time_seconds),
        ),
        meta_evolve=MetaEvolveConfig(
            enabled=_bool_from_env_or_config(
                "AGENT_EVOLVE_META_EVOLVE_ENABLED",
                meta.get("enabled", meta_defaults.enabled),
            ),
            watch_tasks_before_guidance=int(
                meta.get(
                    "watch_tasks_before_guidance",
                    meta_defaults.watch_tasks_before_guidance,
                )
            ),
            guidance_num_to_generate=int(
                meta.get("guidance_num_to_generate", meta_defaults.guidance_num_to_generate)
            ),
            guidance_max_uses=int(meta.get("guidance_max_uses", meta_defaults.guidance_max_uses)),
            max_tried_guidance=int(meta.get("max_tried_guidance", meta_defaults.max_tried_guidance)),
        ),
    )


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file if it is available."""
    import yaml

    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _bool_from_env_or_config(env_name: str, config_value: Any) -> bool:
    """Parse a boolean from an environment override or config value."""
    value = os.getenv(env_name)
    if value is None:
        value = config_value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(config_value)


def _float_from_env_or_config(env_name: str, config_value: Any) -> float:
    """Parse a float from an environment override or config value."""
    value = os.getenv(env_name)
    if value is None:
        value = config_value
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(config_value)
