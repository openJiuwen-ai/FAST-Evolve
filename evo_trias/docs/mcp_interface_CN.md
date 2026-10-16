# Evolution Controller MCP 最小上下文接口

[English](mcp_interface.md) | **中文**

本文是 `evotrias.mcp`（Evolution Controller MCP）的公开工具契约。核心目标是减少 agent 上下文占用：能由 MCP 从本地状态恢复的信息，不让主 agent 或 evotrias-task-executor 反复转述。

`evotrias.mcp` 是薄入口。`evotrias/mcp/public_tools.py` 声明面向 agent 的契约（工具名、参数、默认值、docstring），把每次调用转发给 `evotrias.evolve_ctrl.controller`；算法在那里运行，run 状态经 `evotrias.db` 读写。本文描述契约，`controller.py` 是实现。

## 1. 第一性原则

1. 非必要、非关键的信息不进入 MCP 输入输出。
2. 长字段用 ref 指代，不传原文。
3. Parent、inspirations、attempt、notes、meta-guidance 的状态都由 MCP 本地持久化。
4. 主 agent 只处理调度和紧凑的 MCP 控制调用；meta-guidance、notes 等内容生成由专职 subagent 在独立上下文中完成。
5. Plateau 不会触发提前停止；只有 `action = "budget_exhausted"` 才表示停止创建新任务。

## 2. 公开 Tools

### `configure_run`

输入：

```python
configure_run(
    run_id: str,
    max_total_tasks: int = 50,
    lineage_root_number: int = 4,
    max_concurrent_tasks: int = 4,
    task_description: str = "",
)
```

说明：

- 必须在同一 run 的其他 controller MCP tool 之前调用。
- `lineage_root_number` 与 `max_concurrent_tasks` 是两个独立的旋钮：前者决定初始探索方向数量，是根任务总数的上限（根任务全部结束前不会创建演化任务）；后者决定所有阶段的并行 task 上限。二者互不推导，默认都是 4，最小钳到 1。
- 三个数值参数连同其余全部 run 设置写入 `config.json`。环境变量覆盖（`EVOTRIAS_*`，清单见顶层 README 的 "Defaults and Runtime Switches" 一节）在此时一次性解析并固化进文件，之后整个 run 都以该文件为完整配置。
- `task_description` 保存在 `run_state.json`，供后续 meta-guidance request 使用。
- 同一 `run_id` 重复调用会被忽略，返回已锁定的配置且 `configured: false`（包括其他进程已配置、落盘的情况）。
- 可选的 dashboard 在此启动，状态在 `dashboard` 字段返回，启动失败不影响 run。

返回：

```json
{
  "configured": true,
  "message": "Run configured.",
  "max_total_tasks": 50,
  "lineage_root_number": 4,
  "max_concurrent_tasks": 4,
  "meta_evolve": {
    "enabled": true,
    "guidance_num_to_generate": 3,
    "guidance_max_uses": 2
  },
  "dashboard": {"enabled": true, "started": true, "url": "http://127.0.0.1:4000", "port": 4000}
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
  "artifact_ref": "runs/run_001/tasks/task_003/attempts/attempt_001/solution.py",
  "fitness": 0.9415,
  "metrics": {"loss": 0.0585}
}
```

语义：

- 第一次调用时 MCP 选择 parent 并缓存到 task context。
- 重复调用同一个 `task_id` 时直接返回缓存结果，不重复采样。
- **根阶段**（谱系根任务尚未全部结束）：一律返回 `parent_id: null`，且不绑定 meta-guidance——根任务只从自己的 direction 出发，即使 success pool 已有先完成的根任务。随后 `select_inspirations` 也返回空列表。
- 否则，如果存在 active meta-guidance，MCP 把该 guidance 绑定到本次调用时的 canonical best 并以之为 parent；没有则走普通 parent selector。
- success pool 为空时返回 `parent_id: null`。

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
      "artifact_ref": "runs/run_001/tasks/task_002/attempts/attempt_001/solution.py",
      "fitness": 0.9352,
      "metrics": {"loss": 0.0648}
    }
  ]
}
```

语义：

- 必须先调用 `select_parent`。
- 返回值不重复包含 parent。
- 只返回 id/ref/fitness/metrics，不返回 artifact 内容、profile、sketch。
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
- evotrias-task-executor 应把该 prompt 作为 required strategy 使用；如果它和 parent/inspiration 的常规方向冲突，以 meta-guidance 为更高优先级，但不能违反任务要求和 verifier 输出契约。

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
2. 读取 `verification_result_ref` 指向的 JSON result（绝对路径或相对 cwd）。
3. 追加写 `attempts.jsonl`。记录粒度是 attempt 而非 task，因为 Notes 需要失败 attempt 作证据；同一 task/attempt 重复调用幂等。
4. `status == "success"` 且顶层 `fitness` 为数值时，标记该 attempt `in_success_pool`。一个 task 只入池一次：同一 task 再次成功只记 warning，不再入池。
5. task 的末次 attempt（成功，或 attempt 序号达到 `max_attempts`）时记录 meta-guidance outcome，支持后续归因。
6. 返回 Notes request，供 evotrias-task-executor 生成局部经验。

`attempt_id` 的解析顺序：入参 → result JSON 的 `attempt_id` 字段 → `verification_result_ref` 的父目录名（形如 `attempt_*` 时）→ `task_id`。

`verification_result_ref` 指向的 JSON 至少需要：

```json
{
  "status": "success",
  "artifact_ref": "runs/run_001/tasks/task_007/attempts/attempt_001/solution.py",
  "fitness": 0.87
}
```

`artifact_ref` 也可以作为工具入参传入，二者必有其一。

返回：

```json
{
  "recorded": true,
  "attempt_id": "attempt_001",
  "in_success_pool": true,
  "success_record_id": "task_007",
  "fitness": 0.87,
  "relative_delta_to_parent": 0.034,
  "note_requests": []
}
```

`success_record_id` 等于 task id，未入池时为 `null`。`relative_delta_to_parent` 是 `(fitness - parent.fitness) / |parent.fitness|`，无 parent 时为 `null`。

### `check_convergence`

输入：

```python
check_convergence(run_id: str, concurrency: int)
```

输出示例：

```json
{
  "action": "continue",
  "tasks_to_create": 2,
  "search_state": "exploring",
  "plateau": false,
  "stop_reason": "",
  "meta_guidance": {"enabled": true, "has_pending_request": false, "active_count": 0, "...": "..."},
  "diagnostics": {"...": "..."}
}
```

`action` 为 `request_meta_guidance` 时额外返回 `request_id`。`search_state` 取值 `exploring`、`watching`、`stopped`。`meta_guidance` 和 `diagnostics` 是诊断块，主 agent 不需要读。

`action` 是主语义：

```text
continue              # 继续调度 task，补 tasks_to_create 个
request_meta_guidance # plateau 持续足够久，需要调度专职 worker 生成 meta-guidance
budget_exhausted      # 达到硬预算，停止提交新 task
```

本调用也是 run 唯一的任务水龙头：`continue` 时把新 task id 铸入 task pool，并以 `tasks_to_create` 报告数量。

补位目标（`action == "continue"` 时）。并发上限（`concurrency` 入参，必填且必须为正整数，非正数直接报错；调用方应传 `configure_run` 确认的 `max_concurrent_tasks`）在所有阶段都生效，按三个阶段决定补位数量：

- **根阶段**（已创建任务数 < `lineage_root_number`）：在飞任务补到并发上限，但累计创建数不得超过 `lineage_root_number`（每个初始方向恰好一个根任务）。
- **根屏障**（已创建任务数 == `lineage_root_number` 且仍有未结束任务）：返回 `action == "continue"` 且 `tasks_to_create == 0`，直到所有根任务结束。这是正常等待状态，不是停止信号。
- **演化阶段**（根任务全部结束后）：每次补位到并发上限。
- 所有阶段都受 `max_total_tasks` 硬预算截断。

例：`max_concurrent_tasks=2`、`lineage_root_number=3` 时，首次创建 2 个，完成 1 个后补 1 个（根数到 3），随后等 3 个全部完成才补 2 个，此后每完成 1 个补到 2。`max_concurrent_tasks=3`、`lineage_root_number=2` 时，首次创建 2 个，全部完成后补 3 个，此后每完成 1 个补到 3。

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

返回 pending request，供无状态的 `evotrias-meta-guidance-generator` 生成结构化 guidance。主 agent 只传递 `run_id` 和 `request_id`，不读取该 payload。请求中包含：

- 原始 `task_description`
- `current_best`：请求创建时的 canonical best，作为生成 guidance 的 anchor parent（record id、fitness、artifact ref、summary、metrics、profile）。`artifact_excerpt` 和 `artifact_excerpt_truncated` 在取用时从 artifact 现读，不落盘。
- `progress`：plateau / progress diagnostics
- `lineages`：各谱系摘要
- `notes_summary`：为生成 guidance 提炼的 run-local Notes
- `guidance_history` 和 `failed_guidance`：此前 guidance 试过什么、结果如何
- `generation_instructions` 和 `constraints`

没有 pending request，或 `request_id` 不匹配时，返回 `available: false` 和原因。

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

把 run 标记为 `stopped`（未传 `stop_reason` 时沿用 `check_convergence` 已记录的原因），写出两份 summary。finalize 幂等：重复调用只是从当前状态重新生成同样的文件。

## 3. 本地状态文件

全部 Python 侧状态位于 `.evotrias/db/runs/<run_id>/`，相对当前工作目录（没有路径类环境变量）：

```text
config.json                  全部 run 设置，configure_run 写一次后锁定
run_state.json               task_description、search state、stop reason、diagnostics
task_contexts.json           每个 task 的 parent / inspirations / guidance 分配 / 结果
attempts.jsonl               每条已记录 attempt 一行；success pool 由此派生
notes.jsonl                  已提交的 run-local notes
note_requests.jsonl          发给 evotrias-task-executor 的 note request
notes_events.jsonl           Notes 完整时间线（request 创建/跳过、校验失败、note 提交）
meta_guidance_state.json     pending / active / tried guidance、watching 标记、task 分配
meta_guidance_events.jsonl   meta-guidance 全生命周期（watching、request、commit、分配、结果、归档）
task_pool.json               调度桶（created / pending / queued / running / completed / failed），由 scheduler 写
agent_index.json             调度 agent 登记，由 scheduler 写
```

success pool 不是文件：每次调用时从 `attempts.jsonl`（`in_success_pool` 的记录）加 `task_contexts.json`（selection 计数）在内存中重建。

MCP 拥有 `.evotrias/` 下的一切。`runs/<run_id>/` 下的 artifact、verifier 结果和运行工作区归调用方（agent）所有，MCP 只以路径 ref 引用；唯一例外是 finalize 报告，由 MCP 写到 `runs/<run_id>/run_summary.md` 和 `runs/<run_id>/run_summary.json`。

用尽的 meta-guidance 在 run 生命周期内完整保留，不会从持久化历史中截断。

## 4. Anchor Parent 选择规则

Meta-guidance 激活时，会记录创建时的 canonical best；每次分配给 task 时，则重新绑定当时最新的 canonical best：

1. fitness 最高；
2. fitness 相同则 `created_at` 最早；
3. 仍相同则 `record_id` 字典序最小。

这样既能处理 current best 有多个实现的情况，避免在多个并列 best 之间漂移，也能在某条 guidance 产生 new best 后，让同一批剩余 guidance 继续基于最新 best 执行。

## 5. Prompt 组装边界

MCP 只返回 refs 和 ready-to-inject prompt section，不为 evotrias-task-executor 读取或拼接 artifact 内容。唯一例外是 `get_meta_guidance_request` 里交给 meta-guidance worker 的 `artifact_excerpt`。

evotrias-task-executor 的顺序：

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
- 不要求 evotrias-task-executor 生硬融合冲突的 inspiration；
- 任何 guidance 都不能覆盖任务要求和 verifier 输出契约。
