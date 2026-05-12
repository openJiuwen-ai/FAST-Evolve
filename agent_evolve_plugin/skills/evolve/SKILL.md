---
name: evolve
description: Triggered when the user asks to evolve, optimize, search, or iteratively improve something. Starts the self-evolution loop.
argument-hint: [task_description]
allowed-tools: [Read, Grep, Glob, Bash, Write, Skill]
---

# 自进化主 Skill

你现在正在运行 **自进化主 skill**，也就是调度器。你的职责是：初始化 run、维护 task 队列、检测收敛、停止补位、等待已启动任务结束，并调用 Evolution Controller MCP 生成确定性的 run 总结报告。

## 架构角色分工

| 组件 | 职责 |
|---|---|
| 本 skill（调度器） | 初始化 run，维护 task 队列，检测收敛，调用 finalize |
| task-executor agent | 执行单个 task 的完整生命周期 |
| Evolution Controller MCP | 选择 parent/inspirations，记录 attempt，判断收敛，生成 run summary |

---

## 步骤 1：运行初始化

1. 生成 `run_id`：`run_YYYYMMDDHHMMSS`。
   - 使用当前本地启动时间，精确到秒。
   - 不要使用 `run_YYYYMMDD_NNN` 这种需要读取历史目录并累计编号的格式。
2. 和用户确认后开始运行。

---

## 步骤 2：Task 队列调度

维护 task 队列，最多同时运行 **4 个 task**。每个 task 完成后立刻调用 `evolve_check_convergence(run_id)`。达到任务上限、时间上限、或收敛早停，都由 MCP 判断并通过 `should_stop` 返回。

```text
MAX_CONCURRENT_TASKS = 4
task_counter = 0
running_tasks = []
stop_requested = false

// 初始填满队列
FOR i = 1..MAX_CONCURRENT_TASKS:
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  派发 {run_id, task_id, task_description} 给 task-executor agent
  running_tasks.append(task_id)

// 事件循环
WHILE running_tasks is not empty:
  等待任意一个 task-executor 返回 "Task <task_id> completed"
  从 running_tasks 中移除已完成的 task_id

  should_stop = evolve_check_convergence(run_id).should_stop

  IF should_stop:
    stop_requested = true
    // 停止提交新 task，但不要立刻结束 run。
    // 必须等待已经启动的 running_tasks 全部完成并完成入库。
    CONTINUE

  IF NOT stop_requested:
    task_counter += 1
    task_id = f"task_{task_counter:03d}"
    派发 {run_id, task_id, task_description} 给 task-executor agent
    running_tasks.append(task_id)

END WHILE

// 所有已启动 task 都结束后，再做一次最终收敛检测，让 MCP 的 run_state
// 覆盖最终 attempt / success_db 状态。
evolve_check_convergence(run_id)
finalize_result = evolve_finalize_run(run_id)
```

重要约束：

1. 一旦 `should_stop == true`，必须停止提交新的 task。
2. 如果此时还有正在运行的 task，必须等待它们全部完成，不能直接结束 run。
3. 所有已启动 task 都完成后，必须调用一次 `evolve_finalize_run(run_id)`。
4. `evolve_finalize_run` 返回的是报告 ref，不要把完整报告内容粘贴到上下文里。
5. 运行过程中，每当 task 完成、补位 task 启动、或等待剩余 running tasks 时，在终端维护一个简洁的 `Progress Update`。使用适合终端阅读的 Unicode 框线表格，并保留状态 emoji。表格必须包含 `Task`、`Status`、`Fitness`、`Artifact` 四列，不要使用 `Note` 列。`Artifact` 必须填写确定性的 artifact 路径：如果某个 task 的任意 attempt 已经通过 `evolve_record_attempt` 进入 SuccessDB，则该 task 必须显示为 `✅ Completed`，并使用成功入库那次 attempt 的 fitness 和 `artifact_ref`，例如 `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`；不要用同一 task 的早期失败 attempt 覆盖最终成功状态。正在运行 task 使用 `🔄 Running`，`Fitness` 和 `Artifact` 填 `—`；只有当 task 已经结束且没有任何 attempt 进入 SuccessDB 时，才使用 `⚠️ Issue` 或 `⚠️ Failed`，并尽量显示失败 attempt 的 artifact 路径。表格后用一行写当前 best fitness 和当前 running task 数量。

---

## 步骤 3：运行结束

正常停止时，只向用户报告：

```text
Run finalized.
report_ref: <finalize_result.report_ref>
summary_ref: <finalize_result.summary_ref>
```

不要自己手写长篇 run summary。确定性的总结文档由 MCP 写入：

```text
runs/<run_id>/run_summary.md
runs/<run_id>/run_summary.json
```

---

## 错误处理

| 情况 | 处理 |
|---|---|
| MCP 工具返回错误 | 重试一次；如果持续失败，停止提交新 task，等待已启动 task 完成，然后调用 `evolve_finalize_run(run_id, stop_reason="mcp_error")` |
| task-executor 执行失败 | 仍然调用 `evolve_check_convergence`，由 MCP 基于已记录 attempt 判断是否停止或继续 |
| 用户中断 | 停止提交新 task；如果可以，等待已启动 task 完成；然后调用 `evolve_finalize_run(run_id, stop_reason="user_interrupted")` |

---
---

# Self-Evolution Master Skill

You are now running the **self-evolution master skill**, the orchestrator. Your responsibilities are: initialize a run, maintain the task queue, detect convergence, stop replenishment, wait for already launched tasks to finish, and call Evolution Controller MCP to generate a deterministic run summary report.

## Architecture Roles

| Component | Responsibility |
|---|---|
| This skill (orchestrator) | Initialize run, maintain task queue, convergence detection, finalization |
| task-executor agent | Execute the full lifecycle of individual tasks |
| Evolution Controller MCP | Parent/inspiration selection, attempt recording, convergence detection, run summary generation |

---

## Step 1: Run Initialization

1. Generate `run_id`: `run_YYYYMMDDHHMMSS`.
   - Use the current local start time with second-level precision.
   - Do not use `run_YYYYMMDD_NNN`, because that format requires reading historical run directories and incrementing a counter.
2. Confirm with the user and start.

---

## Step 2: Task Queue Scheduling

Maintain the task queue with at most **4 concurrent tasks**. After each task completes, immediately call `evolve_check_convergence(run_id)`. Task-limit stops, time-budget stops, and convergence early stops are all decided by MCP and returned through `should_stop`.

```text
MAX_CONCURRENT_TASKS = 4
task_counter = 0
running_tasks = []
stop_requested = false

// Initial fill
FOR i = 1..MAX_CONCURRENT_TASKS:
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  Dispatch {run_id, task_id, task_description} to task-executor agent
  running_tasks.append(task_id)

// Event loop
WHILE running_tasks is not empty:
  Wait for any task-executor to return "Task <task_id> completed"
  Remove completed task_id from running_tasks

  should_stop = evolve_check_convergence(run_id).should_stop

  IF should_stop:
    stop_requested = true
    // Stop launching replacement tasks, but do not end the run immediately.
    // Wait until all already launched running_tasks are completed and recorded.
    CONTINUE

  IF NOT stop_requested:
    task_counter += 1
    task_id = f"task_{task_counter:03d}"
    Dispatch {run_id, task_id, task_description} to task-executor agent
    running_tasks.append(task_id)

END WHILE

// After every launched task has finished, call convergence once more so MCP
// run_state reflects the final attempt / success_db state.
evolve_check_convergence(run_id)
finalize_result = evolve_finalize_run(run_id)
```

Critical constraints:

1. Once `should_stop == true`, stop launching new tasks.
2. If tasks are still running, wait for all of them to complete before ending the run.
3. After all launched tasks have completed, you must call `evolve_finalize_run(run_id)` exactly as the finalization step.
4. `evolve_finalize_run` returns report refs. Do not paste the full report into the conversation context.
5. During the run, maintain a concise terminal `Progress Update` whenever a task completes, a replacement task is launched, or the workflow is waiting for remaining running tasks. Use a Unicode box-drawing table optimized for terminal readability, and keep status emoji. The table must include exactly these semantic columns: `Task`, `Status`, `Fitness`, and `Artifact`; do not use a `Note` column. `Artifact` must contain the deterministic artifact path. The table is task-level, not attempt-level: if any attempt for a task has been accepted into SuccessDB through `evolve_record_attempt`, that task must be shown as `✅ Completed` using the successful recorded attempt's fitness and `artifact_ref`, for example `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`; do not let an earlier failed attempt for the same task override the final successful task status. Running tasks should use `🔄 Running` with `—` for both `Fitness` and `Artifact`. Use `⚠️ Issue` or `⚠️ Failed` only when the task has finished and no attempt for that task entered SuccessDB, and show the failed attempt artifact path when available. After the table, print one short line with the current best fitness and number of running tasks.

---

## Step 3: Run Finalization

On normal stop, report only:

```text
Run finalized.
report_ref: <finalize_result.report_ref>
summary_ref: <finalize_result.summary_ref>
```

Do not write a long run summary yourself. The deterministic report is written by MCP to:

```text
runs/<run_id>/run_summary.md
runs/<run_id>/run_summary.json
```

---

## Error Handling

| Situation | Action |
|---|---|
| MCP tool returns error | Retry once; if persistent, stop launching new tasks, wait for already launched tasks to complete, then call `evolve_finalize_run(run_id, stop_reason="mcp_error")` |
| task-executor fails | Still call `evolve_check_convergence`; MCP decides whether to stop or continue from recorded attempts |
| User interrupts | Stop launching new tasks; if possible, wait for launched tasks to complete; then call `evolve_finalize_run(run_id, stop_reason="user_interrupted")` |
