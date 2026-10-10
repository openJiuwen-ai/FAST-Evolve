"""Task scheduling for one run: minting, allocation and the task state machine.

Scheduling is not evolution control -- it does not choose parents or judge
convergence.  It only tracks which task ids exist, which agent holds each
one, and how they finished.  All state lives in ``evotrias.db``
(``TaskPool``, ``AgentIndex``, ``SessionMap``); this module owns the
transitions ``created -> pending -> queued -> running -> completed | failed``.
"""

from __future__ import annotations

from typing import Any, Optional

from evotrias.db.agent_index import AgentIndex, AgentInfo
from evotrias.db.session_map import SessionMap
from evotrias.db.task_pool import TaskAssigned, TaskCompleted, TaskFailed, TaskPool
from evotrias.logger import logger


def remember_session_run(session_id: str, run_id: str) -> None:
    session_id = str(session_id).strip()
    run_id = str(run_id).strip()
    if not session_id or not run_id:
        logger.error(f'remember_session_run skipped: empty key. {session_id=!r}, {run_id=!r}')
        return
    with SessionMap() as sessions:
        sessions.sessions[session_id] = run_id
    logger.info(f'Session {session_id} mapped to run {run_id}')


def lookup_run_by_session(session_id: str) -> str | None:
    result = SessionMap().sessions.get(str(session_id).strip())
    logger.debug(f'lookup_run_by_session: {session_id=!r} -> {result!r}')
    return str(result) if result else None


def remember_agent(run_id: str, agent_id: str, agent_info: AgentInfo) -> None:
    with AgentIndex(run_id) as index:
        index.agents[agent_id] = agent_info
    logger.info(f'Agent {agent_id} indexed for run {run_id}')
    logger.debug(f'{agent_info=!r}')


def lookup_agent(run_id: str, agent_id: str) -> Optional[AgentInfo]:
    info = AgentIndex(run_id).agents.get(agent_id)
    if info is None:
        logger.debug(f'lookup_agent: {agent_id=!r} not found in run {run_id}')
    return info


def top_up_tasks(run_id: str, target: int, max_tasks: int) -> list[str]:
    if target < 0:
        target = 0
    created: list[str] = []
    with TaskPool(run_id) as pool:
        current = len(pool.created) + len(pool.pending) + len(pool.queued) + len(pool.running)
        already = pool.next_seq - 1
        budget_target = current + max(max_tasks - already, 0)
        if budget_target < target:
            logger.info(
                f'top_up_tasks: run {run_id} capped by task budget '
                f'({already}/{max_tasks} created), target {target} -> {budget_target}'
            )
            target = budget_target
        while current < target:
            task_id = pool.next_task_id()
            pool.created.append(task_id)
            current += 1
            created.append(task_id)
    logger.info(f'top_up_tasks: created {len(created)} tasks for run {run_id}: {created}')
    return created


def allocate_task(run_id: str) -> str | None:
    if not TaskPool(run_id).created:
        logger.critical(f'allocate_task rejected: no created tasks in run {run_id}')
        return None
    with TaskPool(run_id) as pool:
        task_id = pool.created.pop(0)
        pool.pending.append(task_id)
    logger.info(f'allocate_task: task {task_id} allocated in run {run_id}')
    return task_id


def queue_task(run_id: str, task_id: str, agent_id: str) -> None:
    if task_id not in TaskPool(run_id).pending:
        logger.critical(f'queue_task rejected: task {task_id} is not in pending state in run {run_id}')
        return
    with TaskPool(run_id) as pool:
        pool.pending.remove(task_id)
        pool.queued[task_id] = TaskAssigned(agent_id=str(agent_id))
    logger.info(f'queue_task: task {task_id} queued for agent {agent_id} in run {run_id}')


def start_task(run_id: str, agent_id: str) -> str | None:
    agent_id = str(agent_id)
    task_id = next((tid for tid, held in TaskPool(run_id).queued.items() if held.agent_id == agent_id), None)
    if task_id is None:
        # Legitimate when SubagentStart fires before the spawner's PostToolUse
        # (parallel processes): the binding hasn't landed in queued yet.
        logger.info(f'start_task: no queued task yet for agent {agent_id} in run {run_id}')
        return None
    with TaskPool(run_id) as pool:
        pool.queued.pop(task_id)
        pool.running[task_id] = TaskAssigned(agent_id=agent_id)
    logger.info(f'start_task: task {task_id} started by agent {agent_id} in run {run_id}')
    return task_id


def complete_task(run_id: str, task_id: str, fitness: Any) -> None:
    if task_id not in TaskPool(run_id).running:
        logger.error(f'complete_task rejected: task {task_id} is not in running state in run {run_id}')
        return
    with TaskPool(run_id) as pool:
        pool.running.pop(task_id)
        pool.completed[task_id] = TaskCompleted(fitness=fitness)
    logger.info(f'complete_task: task {task_id} completed in run {run_id}, {fitness=!r}')


def fail_task(run_id: str, task_id: str, reason: str) -> None:
    if task_id not in TaskPool(run_id).running:
        logger.error(f'fail_task rejected: task {task_id} is not in running state in run {run_id}')
        return
    with TaskPool(run_id) as pool:
        pool.running.pop(task_id)
        pool.failed[task_id] = TaskFailed(reason=str(reason)[:500])
    logger.info(f'fail_task: task {task_id} failed in run {run_id}, reason={str(reason)[:120]!r}')


def running_count(run_id: str) -> int:
    return len(TaskPool(run_id).running)


def pool_snapshot(run_id: str) -> TaskPool:
    return TaskPool(run_id)
