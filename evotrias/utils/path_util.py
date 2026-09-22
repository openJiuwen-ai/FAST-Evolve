from __future__ import annotations

import os


def norm_path(p: str | None) -> str:
    if not p:
        return ""
    return os.path.normpath(p).replace("\\", "/")


def path_within(child: str, parent: str) -> bool:
    if not child or not parent:
        return False
    child = norm_path(child)
    parent = norm_path(parent)
    if child == parent:
        return True
    return child.startswith(parent.rstrip("/") + "/")


def task_workspace(run_id: str | None, task_id: str | None) -> str:
    if not run_id or not task_id:
        return ""
    return norm_path(os.path.join("runs", str(run_id), "tasks", str(task_id)))
