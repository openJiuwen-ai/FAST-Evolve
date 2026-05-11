# Evolution Controller MCP 最小上下文接口设计

本文是新版 `agent_evolve_mcp` 的目标接口契约。核心目标是减少 agent 上下文占用，并避免让大模型记忆和转述容易出错的 id。

## 1. 第一性原则

1. 非必要、非关键的信息不进入 MCP 输入输出。
2. 长字段用 ref 指代，不传原文。
3. 能由 MCP 从本地状态、SuccessDB、attempt log、verifier result file 检索到的信息，不要求 agent 传入。
4. WATCHING、strategy signal、diagnostics 属于 MCP 内部控制逻辑，不暴露给主 agent 做精细控制。

## 2. 公开 Tools

### `evolve_select_parent`

输入：

```python
evolve_select_parent(run_id: str, task_id: str)
```

输出：

```json
{
  "parent_id": "task_003",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_003/final.patch"
}
```

SuccessDB 为空时：

```json
{
  "parent_id": null,
  "artifact_ref": null
}
```

副作用：

```text
MCP 写入 task_contexts.json:
(run_id, task_id) -> parent_id
```

`progress` 由 MCP 内部根据 attempt 数和 budget 计算，不再由 agent 传入。

### `evolve_select_inspirations`

输入：

```python
evolve_select_inspirations(run_id: str, task_id: str)
```

输出：

```json
{
  "inspirations": [
    {
      "inspiration_id": "task_002",
      "artifact_ref": "memory_hub/runs/run_001/tasks/task_002/final.patch"
    }
  ]
}
```

注意：

- 返回值不重复包含 parent。
- 不返回 fitness、metrics、profile、sketch、generation。
- MCP 从 `task_contexts.json` 找回本 task 的 parent。
- 选中的 inspiration ids 会写回 `task_contexts.json`。

### `evolve_record_attempt`

输入：

```python
evolve_record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
)
```

输出：

```json
{
  "entered_success_db": true,
  "success_record_id": "task_007"
}
```

失败或未入 SuccessDB：

```json
{
  "entered_success_db": false,
  "success_record_id": null
}
```

MCP 内部步骤：

1. 用 `(run_id, task_id)` 从 `task_contexts.json` 读取 `parent_id` 和 `inspiration_ids`。
2. 读取 `verification_result_ref` 指向的 JSON result。
3. 从 result 中读取 `status`、`artifact_ref`、`fitness`、`metrics`、`profile`、`log_ref` 等字段。
4. 追加写 `attempts.jsonl`。
5. 如果 `status == "success"` 且 `fitness` 合法，则写入 `success_db.json`。
6. 将 task context 标记为 recorded。

`verification_result_ref` 指向的 JSON 至少需要：

```json
{
  "status": "success",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_007/final.patch",
  "fitness": 0.87
}
```

可选字段：

```text
metrics
profile
log_ref
evaluator_version
subagent_id
summary_ref
report_ref
```

完整日志、长反馈、代码内容不进入 MCP 调用参数。

### `evolve_check_convergence`

输入：

```python
evolve_check_convergence(run_id: str)
```

输出：

```json
{
  "should_stop": false
}
```

MCP 内部从本地文件计算：

```text
total_submitted = attempts.jsonl 行数
total_success = success_db.json records 数量
progress = min(total_submitted / max_total_tasks, 1.0)
elapsed_seconds = now - run_state.started_at
```

内部状态：

```text
EXPLORING
WATCHING
STOPPED
```

对外映射：

```text
EXPLORING -> should_stop=false
WATCHING  -> should_stop=false
STOPPED   -> should_stop=true
```

WATCHING 的策略信号不返回给 agent。MCP 将 WATCHING 写入 `run_state.json`，后续 `evolve_select_parent` 和 `evolve_select_inspirations` 读取该状态并内部应用：

```text
tau_multiplier
p_migrate_multiplier
T_insp_multiplier
```

### `evolve_finalize_run`

输入：

```python
evolve_finalize_run(run_id: str, stop_reason: str = "")
```

输出：

```json
{
  "report_ref": "runs/run_20260509_001/run_summary.md",
  "summary_ref": "runs/run_20260509_001/run_summary.json"
}
```

说明：

- `evolve_check_convergence` 仍然只负责判断收敛和预算停止，只返回 `should_stop`。
- `evolve_finalize_run` 负责确定性写入 run 总结文档，不返回完整报告内容。
- 正常收敛、达到最大 task 数、达到时间上限时，`stop_reason` 留空，MCP 使用 `run_state.json` 中已有的停止原因。
- 用户中断或主 agent 外部错误停止时，可以传入 `user_interrupted`、`mcp_error` 等短字符串。
- 报告固定写入 `runs/<run_id>/run_summary.md` 和 `runs/<run_id>/run_summary.json`。
- 该接口幂等，重复调用会根据当前本地状态重新生成报告，不修改 SuccessDB 或 attempt log。

## 3. 本地状态文件

默认根目录：

```text
.agent_evolve/controller/{run_id}/
```

关键文件：

```text
success_db.json       # 成功记录，供 parent/inspiration selection 使用
attempts.jsonl        # 所有最终 attempt，成功/失败都记录
task_contexts.json    # task -> parent/inspirations 临时映射
run_state.json        # convergence state、started_at、内部策略状态
```

`task_contexts.json` 示例：

```json
{
  "task_007": {
    "task_id": "task_007",
    "parent_id": "task_003",
    "inspiration_ids": ["task_002", "task_005"],
    "created_at": "2026-05-07T10:00:00Z",
    "updated_at": "2026-05-07T10:01:00Z",
    "recorded": false
  }
}
```

## 4. 幂等与重试语义

MCP tool 需要尽量按 `(run_id, task_id)` 幂等，避免 agent 或 MCP client 重试时污染控制器状态。

### Parent Selection

如果 `task_contexts.json` 中已经存在该 `task_id` 的 `parent_id`，`evolve_select_parent` 应直接返回缓存的 parent ref，不重新采样，也不再次增加 `selection_count`。

只有第一次为该 task 选择 parent 时，才执行 parent selector 并写入 cache。

### Inspiration Selection

如果该 `task_id` 已经有 `inspiration_ids`，`evolve_select_inspirations` 应直接返回这些 id 对应的 artifact refs，不重新采样。

如果还没有 parent context，说明调用顺序不符合协议。MVP 可返回空 inspirations 并在本地 context 中标记 `missing_parent_context=true`，也可以直接抛出结构化错误。更保守的实现是抛错，提示调用方先执行 `evolve_select_parent`。

### Attempt Recording

如果同一个 `task_id` 已经 recorded，`evolve_record_attempt` 应返回已记录的 `entered_success_db` / `success_record_id`，不重复 append attempt，也不重复写 SuccessDB。

如果同一个 `task_id` 已 recorded，但新的 `verification_result_ref` 不同，应记录本地 warning 或 error，避免一个 task 被两个最终结果覆盖。

### Convergence Check

`evolve_check_convergence` 可以被主 agent 多次调用。MCP 应根据 attempts 数量判断是否有新数据；没有新 attempt 时，不应让 plateau 计数重复增长。

## 5. Prompt 组装边界

MCP 只返回 `artifact_ref`，不返回 artifact 内容。

下一步 prompt 组装由 skill / plugin / subagent 完成：

1. 调 `evolve_select_parent` 得到 parent artifact ref。
2. 调 `evolve_select_inspirations` 得到额外 inspiration artifact refs。
3. subagent 或 prompt builder 读取这些本地文件。
4. 根据任务类型做截断、摘要、diff 提取或结构化拼接。
5. 把整理后的关键内容放进 generator prompt。

MVP 可以先在 self-evolution skill 中用自然语言规则要求 subagent 读取这些 refs。后续更稳的做法是在 `agent_evolve_plugin` 中实现确定性的 prompt builder。

## 6. 代码实现清单

1. `public_tools.py` 使用五个最小签名。
2. `controller.py` 让 `select_parent/select_inspirations/check_convergence` 自己计算 progress。
3. `state_store.py` 提供 task context 持久化层。
4. `state_store.py` 提供 run state 持久化层，用于保存 WATCHING/STOPPED 等内部状态。
5. `record_attempt` 从 `verification_result_ref` 读取 JSON，不再接收完整 `verification_result`。
6. `select_inspirations` 公开返回时过滤 parent，只返回额外 inspirations 的 id/ref。
7. `check_convergence` 公开返回只保留 `should_stop`。
8. `finalize_run` 公开返回只保留 `report_ref` 和 `summary_ref`。
9. 所有选择与入库接口按 `(run_id, task_id)` 做幂等处理，finalize 接口按 `run_id` 做幂等处理。
