# Evolution Controller MCP 最小上下文接口设计

本文描述 `agent_evolve_mcp` 当前的公开接口边界。核心目标仍然是减少 agent 上下文占用：能由 MCP 从本地状态恢复的信息，不让主 agent 或 task-executor 反复转述。

## 1. 第一性原则

1. 非必要、非关键的信息不进入 MCP 输入输出。
2. 长字段用 ref 指代，不传原文。
3. Parent、inspirations、attempt、notes、meta-guidance 的状态都由 MCP 本地持久化。
4. 主 agent 只处理调度和紧凑 MCP 控制调用；meta-guidance 等内容生成由专职 subagent 在独立上下文中完成。
5. Plateau 不会触发提前停止；只有 `action = "budget_exhausted"` 才表示停止补充新任务。

## 2. 公开 Tools

### `configure_run`

输入：

```python
configure_run(run_id: str, max_total_tasks: int = 50, task_description: str = "")
```

说明：

- 必须在同一 run 的其他 controller MCP tool 之前调用。
- `task_description` 保存原始用户任务描述，供后续 meta-guidance request 使用。
- 同一 `run_id` 重复配置会被忽略，并返回已锁定的配置。

返回中包含当前 meta-evolve 默认配置：

```json
{
  "configured": true,
  "max_total_tasks": 50,
  "meta_evolve": {
    "enabled": true,
    "guidance_num_to_generate": 3,
    "guidance_max_uses": 2,
    "max_tried_guidance": 10
  }
}
```

### `select_parent`

输入：

```python
select_parent(run_id: str, task_id: str)
```

输出：

```json
{
  "parent_id": "task_003",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_003/final.patch",
  "fitness": 0.9415,
  "metrics": {"loss": 0.0585}
}
```

语义：

- 第一次调用时 MCP 选择并缓存 parent。
- 重复调用同一个 `task_id` 时直接返回缓存结果，不重复采样。
- 如果存在 active meta-guidance，MCP 会优先把该 guidance 绑定到本次调用时的 canonical best；否则走普通 parent selector。
- SuccessDB 为空时返回 `parent_id: null`。

### `select_inspirations`

输入：

```python
select_inspirations(run_id: str, task_id: str)
```

输出：

```json
{
  "inspirations": [
    {
      "inspiration_id": "task_002",
      "artifact_ref": "memory_hub/runs/run_001/tasks/task_002/final.patch",
      "fitness": 0.9352,
      "metrics": {"loss": 0.0648}
    }
  ]
}
```

语义：

- 必须先调用 `select_parent`。
- 返回值不重复包含 parent。
- 返回 `id/ref/fitness/metrics`，不返回 artifact 内容、profile、sketch。
- 选中的 inspiration ids 写入 `task_contexts.json`，同一 task 重试时保持不变。

### `get_notes_prompt`

输入：

```python
get_notes_prompt(run_id: str, task_id: str)
```

语义：

- 必须在 parent 和 inspirations 都固定之后调用。
- MCP 返回一个 ready-to-inject prompt section。
- 关闭 Notes 时返回 `available: false` 和关闭原因。

### `get_meta_guidance_prompt`

输入：

```python
get_meta_guidance_prompt(run_id: str, task_id: str)
```

语义：

- 必须在 `select_parent` 之后调用。
- 如果该 task 被分配了 active guidance，MCP 返回 ready-to-inject meta-guidance prompt。
- 如果没有 active guidance，返回 `available: false`。
- task-executor 应把该 prompt 作为 required strategy 使用；如果它和 parent/inspiration 的常规方向冲突，以 meta-guidance 为更高优先级，但不能违反任务和 verifier 输出契约。

### `record_attempt`

输入：

```python
record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
    attempt_id: str = "",
    artifact_ref: str = "",
    max_attempts: int = 3,
)
```

MCP 内部步骤：

1. 从 `task_contexts.json` 恢复 parent、inspirations、meta-guidance assignment。
2. 读取 `verification_result_ref` 指向的 JSON result。
3. 追加写 `attempts.jsonl`。
4. 成功且有合法 fitness 时写入 `success_db.json`。
5. 末次 attempt 时记录 meta-guidance outcome，支持后续归因。
6. 返回 Notes request，供 task-executor 生成局部经验。

`verification_result_ref` 指向的 JSON 至少需要：

```json
{
  "status": "success",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_007/final.patch",
  "fitness": 0.87
}
```

### `check_convergence`

输入：

```python
check_convergence(run_id: str)
```

输出示例：

```json
{
  "action": "continue",
  "search_state": "exploring",
  "plateau": false
}
```

`action` 是主语义：

```text
continue              # 继续调度 task
request_meta_guidance # plateau 持续足够久，需要调度专职 worker 生成 meta-guidance
budget_exhausted      # 达到硬预算，停止提交新 task
```

约束：

- Plateau / WATCHING 不会停止任务。
- 收敛检测进入 WATCHING 后，MCP 直接返回 `request_meta_guidance`。
- WATCHING 不会改变 parent / inspiration 选择参数。
- 关闭 meta-guidance 后仍会保留 plateau diagnostics，但不会创建 guidance request。

### `get_meta_guidance_request`

输入：

```python
get_meta_guidance_request(run_id: str, request_id: str = "")
```

返回 pending request，供无状态的 `meta-guidance-generator` 生成结构化 guidance。主 agent 只传递 `run_id` 和 `request_id`，不读取该 payload。请求中包含：

- 原始 `task_description`
- canonical best 作为生成 guidance 的创建时 anchor parent
- plateau/progress diagnostics
- lineage summaries
- failed guidance 摘要
- safety constraints

### `commit_meta_guidance`

输入：

```python
commit_meta_guidance(run_id: str, request_id: str, guidance_json: Any)
```

`guidance_json` 格式：

```json
{
  "guidance": [
    {
      "idea": "One concise idea",
      "description": "How this changes the search direction",
      "target_metric": "fitness",
      "novelty_claim": "Why this is different from recent attempts",
      "implementation_plan": ["Step 1", "Step 2"],
      "cautions": ["Keep output contract unchanged"],
      "approach_type": "representation",
      "avoid_repeating": ["Known failed route"],
      "max_uses": 2
    }
  ]
}
```

专职 worker 自己调用该工具；MCP 校验并激活 guidance。主 agent 在补位前通过紧凑的 convergence recheck 确认提交结果，不接收 guidance JSON。后续 `select_parent` 会把 active guidance 分配给 task，绑定当时最新的 canonical best，并消耗 use count。

### `finalize_run`

输入：

```python
finalize_run(run_id: str, stop_reason: str = "")
```

返回：

```json
{
  "report_ref": "runs/run_20260509_001/run_summary.md",
  "summary_ref": "runs/run_20260509_001/run_summary.json"
}
```

## 3. 本地状态文件

默认根目录：

```text
.agent_evolve/controller/{run_id}/
```

关键文件：

```text
success_db.json
attempts.jsonl
task_contexts.json
run_state.json
notes.json
meta_guidance/meta_guidance_state.json
meta_guidance/meta_guidance_uses.jsonl
```

`meta_guidance_state.json` 保存 pending/active/tried guidance、watching marker 和 task assignments。

`meta_guidance_uses.jsonl` 记录 guidance 分配和结果，用于归因与报告。

## 4. Anchor Parent 选择规则

Meta-guidance 激活时，会记录创建时的 canonical best；每次分配给 task 时，则重新绑定当时最新的 canonical best：

1. fitness 最高；
2. fitness 相同则 `created_at` 最早；
3. 仍相同则 `record_id` 字典序最小。

这样既能处理 current best 有多个实现的情况，避免在多个并列 best 之间漂移，也能在某条 guidance 产生 new best 后，让同一批剩余 guidance 继续基于最新 best 执行。

## 5. Prompt 组装边界

MCP 只返回 refs 和 ready-to-inject prompt section，不读取或拼接 artifact 内容。

task-executor 的顺序：

1. 调 `select_parent`。
2. 调 `select_inspirations`。
3. 调 `get_notes_prompt`。
4. 调 `get_meta_guidance_prompt`。
5. 读取 refs 指向的本地 artifact。
6. 生成候选并调用 verifier。
7. 调 `record_attempt`。

Meta-guidance 与 parent/inspirations 的关系：

- parent 是 anchor、baseline、实现参考或 fallback；
- inspiration 是普通参考；
- meta-guidance 可以要求走明显不同的新方向；
- 不要求 task-executor 生硬融合冲突的 inspiration；
- 任何 guidance 都不能覆盖任务要求和 verifier 输出契约。
