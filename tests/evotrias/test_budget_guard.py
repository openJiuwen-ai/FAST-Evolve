"""Tests for evotrias.evolve_ctrl.budget_guard."""

from __future__ import annotations

from evotrias.db.config import RunBudgetConfig
from evotrias.evolve_ctrl.budget_guard import RunBudgetGuard


def _guard(**overrides):
    return RunBudgetGuard(RunBudgetConfig(**overrides))


def test_within_budget_returns_empty():
    guard = _guard(max_total_tasks=10, max_time_seconds=60.0)
    assert guard.exhaustion_reason(total_tasks=9, elapsed_seconds=1.0) == ""


def test_task_budget_reached():
    reason = _guard(max_total_tasks=10).exhaustion_reason(total_tasks=10)
    assert "max_total_tasks (10)" in reason


def test_time_budget_needs_both_limit_and_elapsed():
    assert _guard(max_total_tasks=10, max_time_seconds=None).exhaustion_reason(total_tasks=1, elapsed_seconds=999.0) == ""
    assert _guard(max_total_tasks=10, max_time_seconds=60.0).exhaustion_reason(total_tasks=1, elapsed_seconds=None) == ""
    reason = _guard(max_total_tasks=10, max_time_seconds=60.0).exhaustion_reason(total_tasks=1, elapsed_seconds=60.0)
    assert "60" in reason and "Time budget" in reason


def test_task_reason_takes_precedence_over_time():
    guard = _guard(max_total_tasks=10, max_time_seconds=60.0)
    reason = guard.exhaustion_reason(total_tasks=10, elapsed_seconds=999.0)
    assert "max_total_tasks" in reason
    assert "Time budget" not in reason
