# MCP 接口参考

EvoTrias 控制器以 MCP 服务的形式对外提供进化控制能力。本文档列出全部公开工具的参数与返回值，供 agent prompt 编写及二次开发参考。

| 项目 | 取值 |
| --- | --- |
| 服务名称 | `evotrias` |
| 传输方式 | stdio |
| 启动命令 | `python -m evotrias.mcp`（由 `evotrias setup` 自动写入编码智能体配置） |
| 状态目录 | agent 工作区 `runs/<run_id>/`；控制器内部状态 `.evotrias/`（均相对当前工作目录） |

## 工具概览

MCP 工具由以下三类 agent 调用：

- **主 agent**：用户会话中的 agent，加载 `evolve` skill 后负责整个运行的编排调度。
- **`evotrias-task-executor`**：主 agent 为每个任务派发的 subagent，负责生成并评测候选程序。
- **`evotrias-meta-guidance-generator`**：主 agent 在搜索停滞时派发的 subagent，负责生成 Meta-guidance。

所有工具均以 `run_id` 标识一次运行，任务级工具额外以 `task_id` 标识任务。

| 工具 | 调用方 | 作用 |
| --- | --- | --- |
| [`configure_run`](#configure_run) | 主 agent | 登记运行参数并启动仪表盘 |
| [`check_convergence`](#check_convergence) | 主 agent | 返回下一步调度动作，并按需创建新任务 |
| [`finalize_run`](#finalize_run) | 主 agent | 生成运行总结 |
| [`select_parent`](#select_parent) | `evotrias-task-executor` | 为任务选择父代 |
| [`select_inspirations`](#select_inspirations) | `evotrias-task-executor` | 为任务选择 inspiration |
| [`get_notes_prompt`](#get_notes_prompt) | `evotrias-task-executor` | 获取本任务适用的 Notes |
| [`get_meta_guidance_prompt`](#get_meta_guidance_prompt) | `evotrias-task-executor` | 获取分配给本任务的 Meta-guidance |
| [`record_attempt`](#record_attempt) | `evotrias-task-executor` | 记录一次尝试的评测结果 |
| [`commit_generated_note`](#commit_generated_note) | `evotrias-task-executor` | 提交一条 Note |
| [`get_meta_guidance_request`](#get_meta_guidance_request) | `evotrias-meta-guidance-generator` | 获取待处理的 Meta-guidance 请求 |
| [`commit_meta_guidance`](#commit_meta_guidance) | `evotrias-meta-guidance-generator` | 提交并激活 Meta-guidance |

## 调用流程

```text
主 agent（evolve skill）
  configure_run ─► check_convergence（循环）─► finalize_run
                     │
                     ├─ action = continue               ─► 派发 tasks_to_create 个 evotrias-task-executor
                     ├─ action = request_meta_guidance  ─► 以 request_id 派发 evotrias-meta-guidance-generator
                     └─ action = budget_exhausted       ─► 等待在途任务结束后收尾

evotrias-task-executor（每个任务一个）
  select_parent ─► select_inspirations ─► get_notes_prompt ─► get_meta_guidance_prompt
    ─► 生成候选并运行评估器 ─► record_attempt ─► commit_generated_note（按 note_requests 逐条提交）

evotrias-meta-guidance-generator
  get_meta_guidance_request ─► commit_meta_guidance
```

## configure_run

登记一次运行的参数。必须先于该 `run_id` 的其他工具调用；首次调用写入配置，此后配置锁定，重复调用返回已锁定的值。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `max_total_tasks` | integer | | `50` | 任务总数上限 |
| `lineage_root_number` | integer | | `4` | 初始探索方向数，即谱系根任务数，最小为 1 |
| `max_concurrent_tasks` | integer | | `4` | 最大并行任务数，最小为 1 |
| `task_description` | string | | `""` | 用户提交的原始任务描述 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `configured` | boolean | 本次调用是否写入了配置；配置已存在时为 `false` |
| `message` | string | 结果说明 |
| `max_total_tasks` | integer | 生效的任务总数上限 |
| `lineage_root_number` | integer | 生效的初始方向数 |
| `max_concurrent_tasks` | integer | 生效的最大并行任务数 |
| `meta_evolve` | object | Meta-guidance 配置：`enabled`、`guidance_num_to_generate`、`guidance_max_uses` |
| `dashboard` | object | 仪表盘状态：`enabled`、`started`；启动成功时含 `url`、`port`，失败时含 `error`，被禁用时含 `reason` |

## check_convergence

根据当前搜索状态返回下一步调度动作。返回 `continue` 时，本工具同时在任务池中创建新任务，是运行中唯一的任务创建入口。

在根阶段，任务总数不超过 `lineage_root_number`；全部根任务结束前不会创建进化任务，此时返回 `continue` 且 `tasks_to_create` 为 `0`，属于正常等待状态。所有阶段的在途任务数均不超过 `concurrency`，任务总数不超过 `max_total_tasks`。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `concurrency` | integer | ✓ | — | 在途任务上限，须为正整数，通常传入 `max_concurrent_tasks` |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `action` | string | `continue`：继续调度；`request_meta_guidance`：需生成 Meta-guidance；`budget_exhausted`：预算耗尽，停止创建任务 |
| `tasks_to_create` | integer | 本次新创建的任务数，仅在 `continue` 时可能大于 0 |
| `request_id` | string | Meta-guidance 请求标识，仅在 `request_meta_guidance` 时返回 |
| `search_state` | string | 搜索阶段：`exploring`、`watching`（停滞观察中）或 `stopped` |
| `plateau` | boolean | 是否处于停滞观察状态 |
| `stop_reason` | string | 停止原因，未停止时为空 |
| `meta_guidance` | object | Meta-guidance 统计：`enabled`、`has_pending_request`、`active_count`、`active_with_remaining`、`tried_count`、`watching` |
| `diagnostics` | object | 停滞检测诊断信息，如 `current_best_fitness`、`plateau_count`、`window`、`patience` 等 |

## finalize_run

根据已持久化的运行状态生成运行总结。可重复调用，每次基于当前状态重新生成，不修改候选与尝试历史。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `stop_reason` | string | | `""` | 外部停止原因（如用户中断）；正常收敛或预算耗尽时留空，沿用已记录的原因 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `report_ref` | string | Markdown 总结路径：`runs/<run_id>/run_summary.md` |
| `summary_ref` | string | JSON 总结路径：`runs/<run_id>/run_summary.json` |

## select_parent

为任务选择父代，并缓存在任务上下文中，后续工具无需重复传入。同一任务重复调用返回相同结果。根任务没有父代；任务被分配 Meta-guidance 时，父代固定为当前最优候选。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `task_id` | string | ✓ | — | 任务标识 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `parent_id` | string \| null | 父代候选标识，根任务为 `null` |
| `artifact_ref` | string \| null | 父代产物路径 |
| `fitness` | number \| null | 父代适应度 |
| `metrics` | object | 父代评测指标，根任务为 `{}` |

## select_inspirations

为任务选择 inspiration，结果不包含父代。须在 `select_parent` 之后调用；同一任务重复调用返回相同结果。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `task_id` | string | ✓ | — | 任务标识 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `inspirations` | array | inspiration 列表，每项含 `inspiration_id`、`artifact_ref`、`fitness`、`metrics`；无父代时为空列表 |

## get_notes_prompt

返回与本任务父代及 inspiration 相关的 Notes，已渲染为可直接注入的提示词片段。须在 `select_parent` 与 `select_inspirations` 之后调用。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `task_id` | string | ✓ | — | 任务标识 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `available` | boolean | 是否有可用 Notes |
| `prompt` | string | 提示词片段，无可用 Notes 时为空 |
| `selected_note_ids` | array | 选中的 Note 标识 |
| `counts` | object | 各类 Note 数量：`error`、`positive`、`negative`、`matched_pos_neg`、`random_fill` |
| `disabled` | boolean | 仅在 Notes 被禁用时返回，值为 `true` |
| `reason` | string | 仅在 Notes 被禁用时返回 |

## get_meta_guidance_prompt

返回分配给本任务的 Meta-guidance，已渲染为可直接注入的提示词片段。须在 `select_parent` 之后调用。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `task_id` | string | ✓ | — | 任务标识 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `available` | boolean | 本任务是否分配了 Meta-guidance |
| `reason` | string | 不可用原因：`no_active_guidance` 或 `assigned_guidance_not_found` |
| `guidance_id` | string | Meta-guidance 标识 |
| `anchor_parent_id` | string | 本任务的锚定父代 |
| `anchor_fitness` | number \| null | 锚定父代的适应度 |
| `creation_anchor_parent_id` | string | Meta-guidance 生成时的锚定父代 |
| `prompt` | string | 提示词片段 |

`available` 为 `false` 时仅返回 `available` 与 `reason`。

## record_attempt

记录一次尝试的评测结果。成功且含数值适应度的尝试进入 success pool；父代与 inspiration 从任务上下文中自动关联。同一 `task_id` 与 `attempt_id` 重复调用为幂等操作。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `task_id` | string | ✓ | — | 任务标识 |
| `verification_result_ref` | string | ✓ | — | 评测结果文件路径，格式见[评测结果文件](#评测结果文件) |
| `attempt_id` | string | | `""` | 尝试标识；留空时依次从结果文件、结果文件所在目录名（如 `attempt_001`）推断 |
| `artifact_ref` | string | | `""` | 候选产物路径；留空时读取结果文件中的 `artifact_ref`，两者均缺失时报错 |
| `max_attempts` | integer | | `3` | 本任务的最大尝试次数，用于判断是否为最后一次尝试 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `recorded` | boolean | 恒为 `true` |
| `attempt_id` | string | 生效的尝试标识 |
| `in_success_pool` | boolean | 是否进入 success pool |
| `success_record_id` | string \| null | success pool 记录标识 |
| `fitness` | number \| null | 本次尝试的适应度 |
| `relative_delta_to_parent` | number \| null | 相对父代的适应度变化率 |
| `note_requests` | array | 待生成的 Note 请求，每项含 `note_request_id`、`note_type`（`error`、`positive` 或 `negative`）、`analysis_prompt`、`expected_schema`；须逐条通过 `commit_generated_note` 提交 |

## commit_generated_note

校验并提交一条 Note，对应 `record_attempt` 返回的某个 Note 请求。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `note_request_id` | string | ✓ | — | Note 请求标识 |
| `generated_note_json` | object \| string | ✓ | — | Note 内容，可为对象或 JSON 字符串 |

`generated_note_json` 的字段如下，内容中不得包含代码块、长日志或推理过程：

| 字段 | 必填 | 约束 |
| --- | :---: | --- |
| `title` | ✓ | 不超过 120 字符 |
| `lesson` | ✓ | 不超过 400 字符 |
| `actionable_guidance` | ✓ | 不超过 400 字符 |
| `evidence_summary` | ✓ | 不超过 500 字符 |
| `confidence` | | `low`、`medium` 或 `high`，默认 `medium` |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `committed` | boolean | 是否提交成功 |
| `note_id` | string | Note 标识，提交成功时返回 |
| `already_committed` | boolean | 该 Note 此前已提交时返回 `true` |
| `retryable` | boolean | 提交失败时返回，表示修正内容后是否可重试 |
| `reason` | string | 失败原因 |
| `disabled` | boolean | 仅在 Notes 被禁用时返回，值为 `true` |

## get_meta_guidance_request

获取当前待处理的 Meta-guidance 请求，作为生成 Meta-guidance 的依据。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `request_id` | string | | `""` | 请求标识；留空时返回当前待处理请求 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `available` | boolean | 是否存在匹配的待处理请求 |
| `reason` | string | 不可用原因：`no_pending_meta_guidance_request` 或 `meta_guidance_request_not_found` |
| `request_id` | string | 请求标识 |
| `task_description` | string | 原始任务描述 |
| `current_best` | object | 当前最优候选：`record_id`、`fitness`、`artifact_ref`、`summary`、`metrics`、`artifact_excerpt`（产物内容节选）等 |
| `progress` | object | 停滞进展：`completed_since_watching`、`best_at_watching_start`、`current_best_fitness`、`diagnostics` |
| `lineages` | array | 各谱系统计：`root_id`、`size`、`best_fitness`、`share`、`recent_improvement` 等 |
| `notes_summary` | object | 已有 Notes 摘要 |
| `guidance_history` | array | 已尝试 Meta-guidance 及其效果 |
| `failed_guidance` | array | 未带来提升的 Meta-guidance |
| `generation_instructions` | array | 生成要求 |
| `constraints` | array | 必须遵守的约束 |

`available` 为 `false` 时仅返回 `available` 与 `reason`。

## commit_meta_guidance

校验并激活一批 Meta-guidance。整批校验通过后才会写入；任一项被拒绝时，待处理请求保持不变，可修正后重试。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | 运行标识 |
| `request_id` | string | ✓ | — | 请求标识，须与当前待处理请求一致 |
| `guidance_json` | object \| array \| string | ✓ | — | Meta-guidance 列表，可为 `{"guidance": [...]}`、数组或对应的 JSON 字符串 |

单条 Meta-guidance 的字段如下。条目数不超过 `guidance_num_to_generate`，且各条 `approach_type` 不得重复：

| 字段 | 必填 | 说明 |
| --- | :---: | --- |
| `idea` | ✓ | 核心思路 |
| `description` | ✓ | 具体描述 |
| `novelty_claim` | ✓ | 与已尝试方向的差异 |
| `implementation_plan` | ✓ | 实施步骤列表 |
| `approach_type` | ✓ | 方法类别 |
| `target_metric` | | 目标指标，默认 `fitness` |
| `current_best_assessment` | | 对当前最优解的评估 |
| `relationship_to_current_best` | | 与当前最优解的关系，默认 `critically_adapt` |
| `cautions` | | 注意事项列表 |
| `avoid_repeating` | | 需避免重复的做法列表 |
| `max_uses` | | 可分配任务数，限定在 1 至 `guidance_max_uses` 之间 |

**返回值**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `committed` | boolean | 是否提交成功 |
| `request_id` | string | 请求标识，提交成功时返回 |
| `guidance_ids` | array | 新激活的 Meta-guidance 标识，提交成功时返回 |
| `anchor_parent_id` | string | 锚定父代，提交成功时返回 |
| `reason` | string | 失败原因，如 `pending_request_not_found`、`empty_guidance`、`too_many_guidance_items`、`duplicate_approach_type`、`missing_required_field:<字段>`、`unsafe_guidance`、`meta_guidance_disabled` |
| `max` | integer | 允许的最大条目数，仅在 `too_many_guidance_items` 时返回 |

## 评测结果文件

`record_attempt` 读取的评测结果为 JSON 对象，路径可为绝对路径或相对当前工作目录的路径。控制器使用以下字段：

| 字段 | 必填 | 说明 |
| --- | :---: | --- |
| `status` | ✓ | `success` 表示候选有效，其他取值均视为失败 |
| `fitness` | ✓ | 顶层数值适应度，越大越好；缺失或非数值时不进入 success pool |
| `metrics` | | 评测指标对象 |
| `artifact_ref` | | 候选产物路径，未通过参数传入时必填 |
| `attempt_id` | | 尝试标识 |
| `summary` | | 候选方案的简要说明 |

```json
{
  "status": "success",
  "artifact_ref": "runs/demo/tasks/task_001/attempts/attempt_001/solution.py",
  "fitness": 2.6158,
  "metrics": { "validity": 1.0, "sum_radii": 2.6158, "target_ratio": 0.9927 }
}
```

## 环境变量

以下环境变量需在 MCP 服务启动前设置。前三项在 `configure_run` 时解析并写入该运行的配置，此后不再变化。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EVOTRIAS_META_EVOLVE_ENABLED` | 开启 | 设为 `0` 关闭 Meta-guidance，停滞检测与常规选择不受影响 |
| `EVOTRIAS_NOTES_ENABLED` | 开启 | 设为 `0` 关闭 Notes |
| `EVOTRIAS_PERF_IMPROVEMENT_THRESHOLD` | `0.0` | 最优适应度的相对提升阈值，低于该值视为停滞 |
| `EVOTRIAS_UI_ENABLED` | 开启 | 设为 `0` 关闭仪表盘自动启动 |
| `EVOTRIAS_UI_HOST` | `127.0.0.1` | 仪表盘服务的绑定地址；默认仅允许本机访问 |
| `EVOTRIAS_UI_PUBLIC_HOST` | 同 `EVOTRIAS_UI_HOST` | 仪表盘 URL（即 `configure_run` 返回的 `dashboard.url`）中使用的主机名，不影响绑定地址 |

远程访问仪表盘时，需同时设置 `EVOTRIAS_UI_HOST=0.0.0.0` 与 `EVOTRIAS_UI_PUBLIC_HOST=<主机名或 IP>`，否则输出的 URL 中主机名为 `0.0.0.0`，无法直接访问。仅本机访问时无需设置。

## 错误约定

- **调用错误**：参数非法或未满足前置条件（如未调用 `select_parent` 即调用 `select_inspirations`、`concurrency` 非正整数、评测结果文件无法读取）时，工具抛出异常，经 MCP 标准错误通道返回。
- **业务拒绝**：提交类工具的内容未通过校验时，正常返回 `committed: false` 及 `reason`，运行状态不变。
