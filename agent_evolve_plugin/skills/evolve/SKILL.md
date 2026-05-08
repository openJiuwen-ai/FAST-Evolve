---
name: evolve
description: Triggered when the user asks to evolve, optimize, search, or iteratively improve something. Starts the self-evolution loop.
argument-hint: [task_description]
allowed-tools: [Read, Grep, Glob, Bash, Write, Skill]
---

# 自进化主 Skill

你现在正在运行 **自进化主 skill** —— 流程调度器。你的职责是：初始化运行、维护 task 队列、检测收敛、输出总结。

## 架构角色分工

| 组件 | 职责 |
|------|------|
| **本 skill（调度器）** | 初始化运行、维护 task 队列、收敛检测、输出总结 |
| **task-executor agent** | 执行单个 task 的完整生命周期 |
| Evolution Controller MCP | 策略状态服务 |

---

## 步骤 1：运行初始化

1. 生成 `run_id`：`run_YYYYMMDD_NNN`
2. 与用户确认后开始

---

## 步骤 2：Task 队列调度

维护 task 队列，最多同时运行 **4 个 task**。每个 task 完成后立即检测收敛，不收敛就拉起新 task 补位。

```
MAX_CONCURRENT_TASKS = 4
task_counter = 0
running_tasks = []

// 初始填满队列
FOR i = 1..MAX_CONCURRENT_TASKS:
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  派发 {run_id, task_id, task_description} 给 task-executor agent
  running_tasks.append(task_id)

// 事件循环
WHILE true:
  等待任一 task-executor 返回 "Task <task_id> completed"
  从 running_tasks 中移除已完成的 task_id

  // 检测收敛
  should_stop = evolve_check_convergence(run_id).should_stop
  IF should_stop: BREAK

  // 不收敛，立即拉起新 task 补位
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  派发 {run_id, task_id, task_description} 给 task-executor agent
  running_tasks.append(task_id)

END WHILE
```

---

## 步骤 3：运行总结

当 `should_stop == true` 或用户中断时，向用户呈现：
- **最佳结果**：最高 fitness、制品路径、代数
- **收敛原因**：预算耗尽、fitness 停滞、时间限制
- **统计数据**：总 task 数、成功率

---

## 错误处理

| 情况 | 处理 |
|------|------|
| MCP 工具返回错误 | 重试一次；若持续，停止运行 |
| task-executor 执行失败 | 收敛检测仍会执行，继续下一个 task 或停止 |
| 用户中断 | 执行步骤 3（运行总结），使用当前状态 |

---
---

# Self-Evolution Master Skill

You are now running the **self-evolution master skill** — the orchestrator. Your responsibilities are: initialize a run, maintain the task queue, detect convergence, and produce the run summary.

## Architecture Roles

| Component | Responsibility |
|-----------|---------------|
| **This skill (orchestrator)** | Initialize run, maintain task queue, convergence detection, run summary |
| **task-executor agent** | Execute the full lifecycle of individual tasks |
| Evolution Controller MCP | Strategy state service |

---

## Step 1: Run Initialization

1. Generate `run_id`: `run_YYYYMMDD_NNN`
2. Confirm with user and start

---

## Step 2: Task Queue Scheduling

Maintain the task queue with at most **4 concurrent tasks**. After each task completes, check convergence immediately. If not converged, launch a new task to fill the slot.

```
MAX_CONCURRENT_TASKS = 4
task_counter = 0
running_tasks = []

// Initial fill
FOR i = 1..MAX_CONCURRENT_TASKS:
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  Dispatch {run_id, task_id, task_description} to task-executor agent
  running_tasks.append(task_id)

// Event loop
WHILE true:
  Wait for any task-executor to return "Task <task_id> completed"
  Remove completed task_id from running_tasks

  // Check convergence
  should_stop = evolve_check_convergence(run_id).should_stop
  IF should_stop: BREAK

  // Not converged — launch new task to fill the slot
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  Dispatch {run_id, task_id, task_description} to task-executor agent
  running_tasks.append(task_id)

END WHILE
```

---

## Step 3: Run Summary

When `should_stop == true` or user interrupts, present:
- **Best result**: highest fitness, artifact path, generation
- **Convergence reason**: budget exhausted, fitness plateau, time limit
- **Statistics**: total tasks, success rate

---

## Error Handling

| Situation | Action |
|-----------|--------|
| MCP tool returns error | Retry once; if persistent, stop run |
| task-executor fails | Convergence check still runs; continue next task or stop |
| User interrupts | Run Step 3 (Run Summary) with current state |
