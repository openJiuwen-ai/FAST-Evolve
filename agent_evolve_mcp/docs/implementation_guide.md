# Agent Evolve MCP 实现讲解

> 版本提示：这是一份较早的实现讲解，仍保留不少历史流程细节。当前开发请以仓库根目录 `agent_evolve_mcp/README.md` 和本目录 `minimal_context_interface.md` 为准；尤其是 meta-evolve 之后，`evolve_check_convergence` 的主语义已经从单一 `should_stop` 改为 `action`。

> 本文解释当前 `agent_evolve_mcp` 目录下这版 Evolution Controller MCP 的代码结构、运行流程、配置方式和接入边界。它面向后续要实现 `agent_evolve_plugin` 主循环、或要把 MCP 接到 Claude Code / Codex / opencode 等第三方 agent 的同事。

> 设计更新说明：精简版接口契约见：[minimal_context_interface.md](minimal_context_interface.md)。

---

## 1. 这部分代码到底负责什么

当前 `agent_evolve_mcp` 实现的是 **进化控制器 MCP**，也就是整个自进化底座里的“策略状态服务”。

它只负责四件事：

1. **选择 parent**：从 SuccessDB 中选择一个历史成功记录，作为下一次生成的父代。
2. **选择 inspirations**：围绕 parent 选择若干参考记录，给 generator subagent 作为灵感。
3. **记录 attempt**：subagent 把最终 `verification_result_ref` 交给 MCP；成功记录进入 SuccessDB，失败记录只进入 attempt log。
4. **检查收敛和 meta-evolve 触发**：根据本地 attempt log、SuccessDB、fitness 停滞、多样性等信号返回下一步 `action`；plateau 不提前停止，必要时请求 meta-guidance。

它明确不负责：

- 不实现进化主循环；
- 不拉起 subagent；
- 不执行 verifier；
- 不修改代码；
- 不管理 artifact / log 文件；
- 不实现 heartbeat；
- 不依赖原 AKG / `akg_agents` 代码。

后续主循环应该放在 `agent_evolve_plugin` 中。主循环通过 MCP tools 调用这里的五个接口。

---

## 2. 关键设计原则

### 2.1 MCP IO 最小上下文原则

这版设计需要优先保护 agent 上下文。MCP 的输入输出只保留下一步必须使用的信息：

```text
非必要、非关键字段不传。
能由 MCP 从本地文件查到的信息，不让大模型记忆和转述。
长内容统一用 ref 指代。
```

这条原则直接影响五个公开 tool：

| Tool | 最小输入 | 最小输出 |
|------|----------|----------|
| `evolve_select_parent` | `run_id`, `task_id` | `parent_id`, `artifact_ref`, `fitness` |
| `evolve_select_inspirations` | `run_id`, `task_id` | 额外 inspirations 的 id/ref/fitness，不重复返回 parent |
| `evolve_record_attempt` | `run_id`, `task_id`, `verification_result_ref` | `entered_success_db`, `success_record_id` |
| `evolve_check_convergence` | `run_id` | `should_stop` |
| `evolve_finalize_run` | `run_id`, 可选 `stop_reason` | `report_ref`, `summary_ref` |

`progress`、`parent_id`、`inspiration_ids`、`artifact_ref`、完整 `verification_result`、`strategy_signal` 等字段不再由 agent 在公开接口之间传来传去。

MCP 会自己维护本地 task context cache：

```text
.agent_evolve/controller/{run_id}/task_contexts.json
```

其中记录 `(run_id, task_id)` 对应的 `parent_id` 和 `inspiration_ids`。正式入库时，MCP 根据 `task_id` 找回这些关系，再读取 `verification_result_ref` 指向的 JSON result。

同一个 `(run_id, task_id)` 的 tool 调用应尽量幂等：重复领取 parent / inspirations 时返回缓存结果，不重新采样；重复 record attempt 时返回已有结果，不重复 append。

### 2.2 通用 fitness，越高越好

旧 AKG 代码里有 `speedup`、`gen_time`：

- `speedup` 越大越好；
- `gen_time` 越小越好。

这版 MCP 去掉了这类场景特定字段，统一使用：

```text
fitness: float
```

规则只有一个：

```text
fitness 越高越好
```

不管任务是代码生成、数学搜索、prompt 策略、研究方案，控制器都只看 `fitness`。

其他指标全部放进：

```text
profile
metrics
```

例如：

```json
{
  "status": "success",
  "fitness": 0.87,
  "metrics": {
    "accuracy": 0.91,
    "latency_ms": 128,
    "cost": 0.12
  }
}
```

控制器不会解释 `accuracy`、`latency_ms`、`cost`，它只持久化这些字段，供主 agent 总结或未来 evidence 使用。

### 2.3 SuccessRecord 和 SuccessDB 的区别

`SuccessRecord` 是一条成功经验。

它代表某个通过 verifier 的候选结果，未来可以被选为 parent 或 inspiration。

`SuccessDB` 是成功经验的集合和检索层。

它负责：

- 保存多条 `SuccessRecord`；
- 按 id 查找；
- 返回全部成功记录；
- 返回当前最佳记录；
- 增加 `selection_count`；
- 持久化到磁盘。

简单说：

```text
SuccessRecord = 一条成功记录
SuccessDB     = 多条成功记录组成的数据库/仓库
```

### 2.4 Attempt 和 SuccessRecord 的区别

`AttemptRecord` 记录的是一次 task 的最终尝试结果。

它可以成功，也可以失败。

所有 task 完成后都应该记录 attempt，因为失败同样有复盘价值。

但只有满足以下条件的 attempt 才会进入 SuccessDB：

```text
verification_result.status == "success"
并且
verification_result.fitness 是合法数字
```

因此：

```text
AttemptRecord = 所有最终尝试，成功/失败都记录
SuccessRecord = 成功且有 fitness 的 attempt，能被未来复用
```

---

## 3. 目录结构总览

当前 `agent_evolve_mcp` 目录如下：

```text
agent_evolve_mcp/
  __init__.py
  mcp_server.py
  public_tools.py

  models.py
  config.py
  storage.py
  success_db.py

  controller.py
  lineage_tree.py
  parent_selector.py
  inspiration_selector.py
  convergence_detector.py
```

可以分成四层：

```text
FastMCP server 层
  mcp_server.py
  __init__.py

公开工具层
  public_tools.py

内部控制器层
  controller.py

算法与数据层
  models.py
  config.py
  storage.py
  success_db.py
  lineage_tree.py
  parent_selector.py
  inspiration_selector.py
  convergence_detector.py
```

分层关系：

```text
外部 agent
  -> MCP tool: public_tools.py
    -> EvolutionController: controller.py
      -> SuccessDB / LineageTree / Selectors / ConvergenceDetector
        -> 本地 JSON / JSONL 状态文件
```

---

## 4. 每个文件是干什么的

### 4.1 `mcp_server.py`

职责：

```text
创建 FastMCP server 实例，并注册公开 tools。
```

核心代码形态：

```python
mcp = FastMCP("agent_evolve")

import agent_evolve_mcp.public_tools
```

这里的 `mcp` 是全局 MCP server 对象。

`public_tools.py` 里所有 `@mcp.tool()` 都会注册到这个 server 上。

正式运行环境要求安装 `fastmcp`。当前代码不再提供开发期 fallback；如果环境缺少 `fastmcp`，导入 `agent_evolve_mcp.mcp_server` 会直接失败，这样可以更早暴露部署依赖问题。

可以用下面命令确认当前环境走的是真实 `FastMCP`：

```powershell
python -c "import agent_evolve_mcp.mcp_server as s; print(type(s.mcp).__name__)"
```

预期输出：

```text
FastMCP
```

### 4.2 `__init__.py`

职责：

```text
提供包级导出，并触发 public_tools 注册。
```

当前导出：

```python
from agent_evolve_mcp.mcp_server import mcp
from . import public_tools as public_tools

__all__ = ["mcp", "public_tools"]
```

注意这里导出的是 `mcp`，不是 `mcp_server`。

这是为了避免下面这种导入被包级变量遮蔽：

```python
import agent_evolve_mcp.mcp_server
```

### 4.3 `public_tools.py`

职责：

```text
注册对外暴露的 MCP tools。
```

它是“公开门面”，只应该做很薄的工作：

1. 接收 MCP tool 参数；
2. 找到对应 `run_id` 的内部 controller；
3. 调用 controller 方法；
4. 返回结构化 dict。

它不应该塞入复杂算法。

当前公开五个 MCP tools：

```python
evolve_select_parent(...)
evolve_select_inspirations(...)
evolve_record_attempt(...)
evolve_check_convergence(...)
evolve_finalize_run(...)
```

内部有一个简单 cache：

```python
_controllers: Dict[str, EvolutionController] = {}
```

作用是同一个 `run_id` 重复调用时复用 controller 对象。

不过要注意：

```text
controller 对象不是状态真源。
磁盘上的 SuccessDB 才是状态真源。
```

每次关键操作前，controller 都会重新 `load()` SuccessDB。

### 4.4 `models.py`

职责：

```text
定义所有共享数据结构。
```

主要模型：

#### `SearchState`

搜索状态枚举：

```python
EXPLORING
WATCHING
STOPPED
```

这是 MCP 内部状态。下一版公开 `evolve_check_convergence` 不再返回这些字符串，只返回 `should_stop`：

```text
EXPLORING / WATCHING / STOPPED
```

#### `StrategySignal`

收敛检测器给选择器的策略信号。

例如进入 `WATCHING` 后：

```python
tau_multiplier = 1.5
p_migrate_multiplier = 2.0
T_insp_multiplier = 1.3
```

这不会触发 heartbeat，只会让后续 parent / inspiration 选择更偏探索。

#### `ParentSelectionConfig`

父代选择器参数。

字段：

```python
tau_start
tau_end
c_local_start
c_local_end
p_exploit
quality_bonus_strength
elite_rank_fraction
elite_selection_shield
```

#### `InspirationSelectionConfig`

灵感选择器参数。

字段：

```python
inspiration_sample_num
p_migrate
foreign_elite_num
T_start
T_end
```

#### `ConvergenceConfig`

收敛检测参数。

字段：

```python
enabled
convergence_window
perf_improvement_threshold
diversity_change_threshold
activity_threshold
patience
max_total_tasks
max_time_seconds
```

#### `SuccessRecord`

成功记录。

核心字段：

```python
id
run_id
fitness
parent_id
generation
selection_count
artifact_ref
sketch
profile
metrics
task_id
subagent_id
verification_result_ref
created_at
metadata
```

重要方法：

```python
to_dict()
from_dict()
to_inspiration()
to_summary()
```

下一版最小接口中，公开 MCP tool 不再直接返回完整 summary / inspiration 对象。可以保留这些方法作为内部调试或未来 diagnostics 使用，但公开返回值应只抽取：

```text
record id
artifact_ref
```

#### `LineageStats`

某条谱系的聚合统计：

```python
root_id
size
best_fitness
best_record_id
max_depth
share
total_selections
recent_improvement
```

#### `AttemptRecord`

一次 task 的最终结果记录。

字段包括：

```python
id
run_id
task_id
subagent_id
artifact_ref
parent_id
inspiration_ids
generation
verification_result
verification_result_ref
sketch
entered_success_db
success_record_id
created_at
```

它会写入 `attempts.jsonl`。公开接口只接收 `verification_result_ref`；`verification_result` 是 MCP 读取该 ref 后落盘的内部字段。

### 4.5 `config.py`

职责：

```text
加载 controller 配置。
```

入口函数：

```python
load_evolution_controller_config(config_path=None, max_total_tasks=100)
```

当前 MVP 默认不要求配置文件。

如果不传 `config_path`，就使用代码内置默认值。

如果传 YAML 路径，会读取：

```yaml
parent_selection:
  tau_start: 1.5
  tau_end: 0.5
  c_local_start: 1.5
  c_local_end: 0.5
  p_exploit: 0.7
  quality_bonus_strength: 0.3
  elite_rank_fraction: 0.15
  elite_selection_shield: 0.6

inspiration:
  sample_num: 3
  p_migrate: 0.2
  foreign_elite_num: 2
  T_start: 1.5
  T_end: 0.5

convergence:
  enabled: true
  convergence_window: 5
  perf_improvement_threshold: 0.0
  diversity_change_threshold: 0.05
  activity_threshold: 0.9
  patience: 6
  max_time_seconds: 1800
```

#### 当前注意点

目前公开 MCP tools 没有暴露：

```text
config_path
max_total_tasks
```

所以从 MCP tool 路径调用时，使用的是默认配置：

```text
max_total_tasks = 100
```

后续如果主循环需要每个 run 自定义预算，可以扩展：

1. 增加 `evolve_configure_run`；
2. 或让 `EvolutionController(run_id, config=...)` 从某个 run config 文件加载。

当前代码已经预留了内部加载能力，但公开 MCP schema 还没有开放这部分。

### 4.6 `storage.py`

职责：

```text
管理 controller 自己拥有的本地状态文件路径。
```

默认根目录：

```text
.agent_evolve/controller
```

可以通过环境变量覆盖：

```text
AGENT_EVOLVE_CONTROLLER_DIR
```

例如 PowerShell：

```powershell
$env:AGENT_EVOLVE_CONTROLLER_DIR = "C:\path\to\controller_state"
```

例如 bash：

```bash
export AGENT_EVOLVE_CONTROLLER_DIR=/path/to/controller_state
```

每个 run 的状态目录：

```text
{controller_root}/{run_id}/
```

其中 `run_id` 会通过 `_safe_name()` 转成安全路径片段。

文件：

```text
success_db.json
attempts.jsonl
```

注意：

```text
MCP 不管理 artifact 文件
MCP 不管理 verifier log 文件
MCP 只保存它们的字符串引用
```

例如：

```json
{
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_07/final.patch",
  "log_ref": "memory_hub/runs/run_001/tasks/task_07/logs/final.log"
}
```

这些路径对应的文件由主 agent / plugin / verifier 管。

### 4.7 `success_db.py`

职责：

```text
实现 JSON-backed SuccessDB 和 JSONL-backed AttemptLog。
```

#### `SuccessDB`

初始化：

```python
db = SuccessDB(run_id)
```

会定位到：

```text
.agent_evolve/controller/{run_id}/success_db.json
```

主要方法：

```python
load()
save()
is_empty()
get_all()
get(record_id)
add(record)
increment_selection(record_id)
get_best_record()
infer_next_generation(parent_id)
```

其中：

- `get_best_record()` 按最高 `fitness` 返回最佳记录；
- `increment_selection()` 在 parent 被选中时增加选择次数；
- `infer_next_generation()` 如果调用方没传 generation，就用 parent.generation + 1。

#### `AttemptLog`

初始化：

```python
attempt_log = AttemptLog(run_id)
```

写入：

```python
attempt_log.append(attempt)
```

对应文件：

```text
.agent_evolve/controller/{run_id}/attempts.jsonl
```

每行是一个完整 JSON object。

### 4.8 `lineage_tree.py`

职责：

```text
从 SuccessDB 的 parent_id 链实时重建谱系森林。
```

它不单独持久化图结构。

每次需要做决策时：

```python
lineage_tree.rebuild(db)
```

然后得到：

- 哪些记录是 root；
- 每条记录属于哪个 root；
- 每条记录的 depth；
- 每条谱系的统计信息；
- 多样性指数。

#### root 如何定义

某条记录满足以下任一条件，就是 root：

```text
parent_id is None
或
parent_id 不在当前 SuccessDB 中
```

这对跨 run 或历史缺失情况更宽容。

#### 多样性指数

使用 Shannon entropy：

```text
D = H / ln(K)
```

其中：

- `K` 是谱系数量；
- `share` 是每条谱系记录数占比。

解释：

```text
D 接近 0：成功记录集中在少数谱系，可能坍缩
D 接近 1：成功记录在谱系间比较均匀
```

当前它是监控和收敛信号，不是直接优化目标。

### 4.9 `parent_selector.py`

职责：

```text
实现 Solver 1：谱系感知父代选择。
```

选择分两步。

#### Step 1：选择谱系

先计算每条谱系的潜力：

```python
potential = best_fitness
```

再对各谱系 potential 做 softmax：

```text
deserved_share = softmax(best_fitness / tau)
```

`tau` 是温度。

- tau 大：更平均，更探索；
- tau 小：更偏高 fitness 谱系，更利用。

然后计算探索赤字：

```text
deficit = deserved_share / (actual_share + epsilon)
```

含义：

```text
deficit > 1：这条谱系探索不足
deficit < 1：这条谱系探索过度
```

选择模式：

```python
if random() < p_exploit:
    选 deficit * quality_bonus 最大的谱系
else:
    按 deficit 轮盘赌采样
```

`quality_bonus` 用来在赤字接近时偏向更高质量谱系。

#### Step 2：在谱系内选择具体 parent

在选中的谱系内部，用带 elite shield 的局部 UCB：

```text
elite_cutoff = max(1, ceil(total_count * elite_rank_fraction))
effective_selection_count = selection_count

if global_rank <= elite_cutoff:
    effective_selection_count = selection_count * (1 - elite_selection_shield)

score = rank_quality + c_local * sqrt(log(S_lineage + 1) / (effective_selection_count + 1))
```

其中：

- `rank_quality` 来自全局 fitness 排名；
- `S_lineage` 是该谱系内所有记录被选次数总和；
- `selection_count` 是该记录自身被选次数。
- `elite_rank_fraction` 定义全局 fitness 排名前多少比例属于 elite。
- `elite_selection_shield` 减弱 elite record 的 selection_count 惩罚，让顶尖实现被选中过多次后概率下降更慢。

选中后会立即：

```python
db.increment_selection(record.id)
```

这是乐观更新，表示这个 parent 已经被领取过。

#### 退火参数

```python
tau = anneal(tau_start, tau_end, progress)
c_local = anneal(c_local_start, c_local_end, progress)
```

`progress` 是 MCP 内部参数，范围 `[0, 1]`。

公开 tool 不要求 agent 传它。MCP 可以用本地 attempt 数和预算计算：

```text
submitted_count / max_total_tasks
```

### 4.10 `inspiration_selector.py`

职责：

```text
实现 Solver 2：谱系内 Boltzmann 采样 + 跨谱系迁移。
```

内部 selector 会把 parent 作为第一项方便算法实现：

```text
[parent, inspiration_1, inspiration_2, ...]
```

但公开 MCP tool 会过滤掉 parent，只把额外 inspiration 的 id/ref/fitness 返回给 agent，避免 `evolve_select_parent` 和 `evolve_select_inspirations` 重复占用上下文。

然后选若干 inspiration。

#### 候选池

分成两类：

```text
local_pool：parent 所在谱系的其他记录
foreign_elites：其他谱系的 top-m 精英记录
```

#### 每个 inspiration 槽位如何选择

对每个槽位：

```python
if random() < p_migrate:
    从 foreign_elites 选
else:
    从 local_pool 做 Boltzmann 采样
```

如果 local_pool 空，会 fallback 到全局候选。

#### Boltzmann 采样

概率和 fitness 有关：

```text
P(record) ∝ exp(fitness / T)
```

`T` 是温度：

- T 大：更随机；
- T 小：更偏高 fitness。

同样按 progress 退火：

```python
T = anneal(T_start, T_end, progress)
```

如果收敛检测器处于 `WATCHING`，会通过 `StrategySignal` 增加：

```python
p_migrate
T
```

这样更容易跳出局部路径。

### 4.11 `convergence_detector.py`

职责：

```text
实现 Solver 3：多维收敛检测。
```

当前检测顺序：

1. 先检查硬预算；
2. 成功记录太少时不做软收敛；
3. 计算 fitness 停滞；
4. 计算多样性趋势；
5. 计算当前谱系活跃度；
6. 输出 `EXPLORING` / `WATCHING` / `STOPPED`。

#### 硬预算

如果：

```python
total_submitted >= max_total_tasks
```

立刻 STOPPED。

默认：

```python
max_total_tasks = 100
```

如果配置了：

```python
max_time_seconds
```

且 `elapsed_seconds >= max_time_seconds`，也会 STOPPED。

#### 最少成功数

如果成功数少于：

```python
min_success = max(convergence_window, 5)
```

则保持 `EXPLORING`。

这是防止过早收敛。

#### S1：fitness 停滞

构造 running best fitness history：

```text
best_history[i] = 截止第 i 条成功记录时的最高 fitness
```

窗口内改善率：

```text
improvement = (new_best - old_best) / abs(old_best)
```

如果：

```text
improvement < perf_improvement_threshold
```

则认为 plateau。

默认：

```text
perf_improvement_threshold = 0.0
```

即默认不启用模糊边界：任意正的 best fitness 改善都算进展，等值 running best 不累计 S1 plateau。对算子性能这类 verifier 有波动的任务，可以显式设置为 `0.01`，表示窗口内小于 1% 改善算停滞。

#### S2：多样性趋势

计算每个 prefix 的 lineage diversity。

然后看窗口内变化：

```text
diversity_change = current_diversity - old_diversity
```

如果下降超过阈值：

```text
s2_trend = declining
```

当前 MVP 中，S2 主要用于 diagnostics，不直接决定停止。

#### S3：谱系活跃度

当前用多样性指数近似：

```text
s3_activity = current_diversity
```

如果：

```text
s3_activity >= activity_threshold
```

说明谱系探索相对充分。

默认：

```text
activity_threshold = 0.8
```

#### 状态转移

当前逻辑：

```text
如果达到 max_total_tasks 或时间预算：
  STOPPED

如果成功数不足：
  EXPLORING

如果 plateau 且 plateau_count >= patience 且 activity >= activity_threshold：
  STOPPED

如果 plateau：
  WATCHING

否则：
  EXPLORING
```

默认：

```text
patience = 3
```

#### WATCHING 的作用

当前 MVP 不做 heartbeat。

`WATCHING` 只会让选择器收到：

```python
StrategySignal(
  tau_multiplier=1.5,
  p_migrate_multiplier=2.0,
  T_insp_multiplier=1.3,
  reason="watching_boost_exploration"
)
```

也就是：

- parent selection 更探索；
- inspiration migration 更频繁；
- Boltzmann sampling 更随机。

### 4.12 `controller.py`

职责：

```text
内部总门面，串起所有模块。
```

它不是 MCP tool，但 `public_tools.py` 的五个公开函数都会调用它。

初始化：

```python
controller = EvolutionController(run_id)
```

内部创建：

```python
SuccessDB(run_id)
AttemptLog(run_id)
LineageTree()
LineageAwareParentSelector(...)
LineageBoltzmannInspirationSelector(...)
MultiDimensionalConvergenceDetector(...)
```

公开方法：

```python
select_parent(...)
select_inspirations(...)
record_attempt(...)
check_convergence(...)
```

每个方法开始都会：

```python
self.db.load()
self.lineage_tree.rebuild(self.db)
```

这样可以确保磁盘上的 SuccessDB 是状态真源。

---

## 5. 五个 MCP tools 的详细说明

### 5.1 `evolve_select_parent`

签名：

```python
evolve_select_parent(
  run_id: str,
  task_id: str
) -> Dict[str, Any]
```

输入：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前搜索 run 的 ID |
| `task_id` | 当前 generator task 的 ID |

SuccessDB 为空时返回：

```json
{
  "parent_id": null,
  "artifact_ref": null,
  "fitness": null
}
```

SuccessDB 非空时返回：

```json
{
  "parent_id": "task_003",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_003/final.patch",
  "fitness": 0.9415
}
```

调用后的副作用：

```text
被选中的 parent.selection_count 会 +1，并写回 success_db.json
(run_id, task_id) -> parent_id 会写入 task_contexts.json
```

### 5.2 `evolve_select_inspirations`

签名：

```python
evolve_select_inspirations(
  run_id: str,
  task_id: str
) -> Dict[str, Any]
```

输入：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前搜索 run ID |
| `task_id` | 当前 task ID |

如果 parent 不存在：

```json
{
  "inspirations": []
}
```

如果 parent 存在：

```json
{
  "inspirations": [
    {
      "inspiration_id": "task_002",
      "artifact_ref": "memory_hub/runs/run_001/tasks/task_002/final.patch",
      "fitness": 0.9352
    },
    {
      "inspiration_id": "task_005",
      "artifact_ref": "memory_hub/runs/run_001/tasks/task_005/final.patch",
      "fitness": 0.9288
    }
  ]
}
```

注意：

```text
返回值只包含额外 inspiration，不重复返回 parent。
fitness 越高越好，用于帮助 task-executor 判断参考优先级。
sample_num 使用控制器配置，不需要 agent 传入。
```

如果候选不够，实际数量可能更少。调用后，MCP 会把 inspiration_ids 写入 task_contexts.json，供后续 record_attempt 使用。

### 5.3 `evolve_record_attempt`

签名：

```python
evolve_record_attempt(
  run_id: str,
  task_id: str,
  verification_result_ref: str
) -> Dict[str, Any]
```

输入：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前搜索 run ID |
| `task_id` | 当前 task ID |
| `verification_result_ref` | verifier JSON 文件引用 |

`verification_result_ref` 指向的 JSON 推荐格式：

```json
{
  "status": "success",
  "artifact_ref": "memory_hub/runs/run_001/tasks/task_007/final.patch",
  "fitness": 0.87,
  "metrics": {
    "accuracy": 0.91,
    "latency_ms": 128
  },
  "log_ref": "logs/task_007.log",
  "evaluator_version": "v1",
  "subagent_id": "subagent_007"
}
```

长反馈、完整日志、详细报告不放进这个 MCP 调用参数里；如果需要保留，用 `log_ref`、`summary_ref`、`report_ref` 等 ref 字段指向本地文件。

成功入库条件：

```text
result.status == "success"
并且
result.fitness 可以转成 float
```

成功返回：

```json
{
  "entered_success_db": true,
  "success_record_id": "task_007"
}
```

失败但记录 attempt：

```json
{
  "entered_success_db": false,
  "success_record_id": null
}
```

成功但缺 fitness：

```json
{
  "entered_success_db": false,
  "success_record_id": null
}
```

重复写入：

```json
{
  "entered_success_db": true,
  "success_record_id": "task_007"
}
```

当前 attempt id 规则：

```python
attempt_id = result.get("attempt_id") or task_id or uuid4()
```

所以一般情况下，`task_id` 就是 success record id。

MCP 会从 `task_contexts.json` 读取本 task 的 `parent_id` 和 `inspiration_ids`，并从 result JSON 读取 `artifact_ref`、`fitness`、`metrics`、`profile`、`log_ref` 等字段。agent 不需要在入库接口里复述这些内容。

### 5.4 `evolve_check_convergence`

签名：

```python
evolve_check_convergence(
  run_id: str
) -> Dict[str, Any]
```

输入：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前搜索 run ID |

返回示例：

```json
{
  "should_stop": false
}
```

WATCHING 示例：

```json
{
  "should_stop": false
}
```

`WATCHING` 不返回给主 agent。MCP 会把内部状态写入 run state，后续 `evolve_select_parent` / `evolve_select_inspirations` 会自动读取该状态并增强探索。

STOPPED 示例：

```json
{
  "should_stop": true
}
```

### 5.5 `evolve_finalize_run`

签名：

```python
evolve_finalize_run(
  run_id: str,
  stop_reason: str = "",
) -> Dict[str, Any]
```

输入：

| 字段 | 含义 |
|------|------|
| `run_id` | 当前搜索 run ID |
| `stop_reason` | 可选。仅用于用户中断、主 agent 错误等 MCP 外部停止原因 |

返回示例：

```json
{
  "report_ref": "runs/run_20260509_001/run_summary.md",
  "summary_ref": "runs/run_20260509_001/run_summary.json"
}
```

职责：

1. 从 `success_db.json`、`attempts.jsonl`、`task_contexts.json`、`run_state.json` 重建 run 总结；
2. 在 `runs/<run_id>/run_summary.md` 写入人工可读报告；
3. 在 `runs/<run_id>/run_summary.json` 写入机器可读总结；
4. 只返回 ref，不返回完整报告正文；
5. 保持幂等，重复调用不会重复写 SuccessDB 或 attempt log。

报告内容由 `agent_evolve_mcp/run_report.py` 确定性生成，包括：

- Overview / Best Result；
- 停止原因和收敛诊断指标；
- 按 generation 分组的 Mermaid 谱系图；
- 初始/root 节点的 lineage size；
- Top Results；
- Fitness Progression；
- Lineage Statistics；
- Attempts 明细；
- 基于 SuccessDB 和 LineageTree 计算出的确定性观察。

报告不生成 LLM 自由总结的 Key Insights。若 benchmark 需要语义化策略说明，应由 verifier 或 subagent 写入结构化 `summary` 字段，报告器只展示已有字段，不编造内容。

---

## 6. 一次完整运行流程

下面按未来 `agent_evolve_plugin` 主循环视角解释。

### Step 1：主 agent 创建 run_id

主 agent / plugin 决定一个 run：

```text
run_id = "run_20260506143310"
```

它也会创建自己的 workspace，例如：

```text
runs/run_20260506143310/
```

注意这个 workspace 不归 MCP 管。

MCP 只会在默认位置创建自己的 controller 状态：

```text
.agent_evolve/controller/run_20260506143310/
```

### Step 2：generator subagent 领取 parent

subagent 调用：

```python
evolve_select_parent(
  run_id="run_20260506_001",
  task_id="task_001"
)
```

如果 SuccessDB 空，返回：

```json
{
  "parent_id": null,
  "artifact_ref": null,
  "fitness": null
}
```

subagent 生成初始候选。

### Step 3：generator subagent 领取 inspirations

如果有 parent，则调用：

```python
evolve_select_inspirations(
  run_id="run_20260506_001",
  task_id="task_001"
)
```

如果没有 parent，可以跳过，或者调用后得到空列表。

### Step 4：subagent 生成 artifact 并调用 verifier CLI

这一步 MCP 不参与。

例如 subagent 运行：

```bash
python tools/verify.py \
  --run-id run_20260506_001 \
  --task-id task_001 \
  --subagent-id subagent_001 \
  --artifact-ref memory_hub/runs/run_20260506_001/tasks/task_001/final.patch \
  --mode full \
  --out memory_hub/runs/run_20260506_001/tasks/task_001/verification/final.json
```

verifier 输出：

```json
{
  "status": "success",
  "artifact_ref": "memory_hub/runs/run_20260506_001/tasks/task_001/final.patch",
  "fitness": 0.87,
  "metrics": {
    "accuracy": 0.91
  },
  "log_ref": "...",
  "evaluator_version": "v1"
}
```

### Step 5：subagent 记录 attempt

task 完成后，subagent 调用：

```python
evolve_record_attempt(
  run_id="run_20260506_001",
  task_id="task_001",
  verification_result_ref="memory_hub/runs/run_20260506_001/tasks/task_001/verification/final.json"
)
```

MCP 做四件事：

1. 从 `task_contexts.json` 读取 parent / inspirations；
2. 从 `verification_result_ref` 读取 verifier result；
3. 追加写 `attempts.jsonl`；
4. 如果成功且有 fitness，则写入 `success_db.json`。

### Step 6：主 agent 检查收敛

```python
evolve_check_convergence(
  run_id="run_20260506_001"
)
```

```text
should_stop = false
```

主 agent 继续补位。MCP 内部可能已经进入 WATCHING，但不会把该状态返回给主 agent；它只影响后续 selection 参数。

如果：

```text
should_stop = true
```

主 agent 停止提交新 task，但不立刻结束 run。它需要等待已经启动的 running tasks 全部完成并完成 `evolve_record_attempt`，然后再调用一次 `evolve_check_convergence(run_id)` 刷新最终 `run_state`。

最后调用：

```python
evolve_finalize_run(
  run_id="run_20260506143310"
)
```

返回：

```json
{
  "report_ref": "runs/run_20260506143310/run_summary.md",
  "summary_ref": "runs/run_20260506143310/run_summary.json"
}
```

主 agent 只需要把这两个 ref 告诉用户，不应把完整报告粘贴进上下文。

---

## 7. 数据如何落盘

假设：

```text
run_id = run_001
```

默认目录：

```text
.agent_evolve/controller/run_001/
```

### 7.1 `task_contexts.json`

记录正式入库前的 task 选择关系。这个文件是为了让 agent 不必记忆和转述 `parent_id` / `inspiration_ids`。

示例：

```json
{
  "task_007": {
    "task_id": "task_007",
    "parent_id": "task_003",
    "inspiration_ids": ["task_002", "task_005"],
    "created_at": "...",
    "updated_at": "...",
    "recorded": false
  }
}
```

`inspiration_ids` 不包含 parent。`evolve_record_attempt` 会根据 `(run_id, task_id)` 读取这个文件，恢复完整谱系关系。

### 7.2 `run_state.json`

记录 MCP 内部运行状态，例如 convergence detector 当前处于 `EXPLORING`、`WATCHING` 还是 `STOPPED`，以及最近一次收敛检测需要的统计信息。

这个文件不会直接暴露给 agent。`WATCHING` 只会影响后续 selection 参数；agent 只看到 `should_stop`。

### 7.3 `success_db.json`

只保存成功记录。

示例：

```json
{
  "run_id": "run_001",
  "records": [
    {
      "id": "task_001",
      "run_id": "run_001",
      "fitness": 0.87,
      "parent_id": null,
      "generation": 0,
      "selection_count": 2,
      "artifact_ref": "...",
      "sketch": "...",
      "profile": {
        "accuracy": 0.91
      },
      "metrics": {
        "accuracy": 0.91
      },
      "task_id": "task_001",
      "subagent_id": "subagent_001",
      "verification_result_ref": "...",
      "created_at": "...",
      "metadata": {
        "inspiration_ids": [],
        "evaluator_version": "v1",
        "log_ref": "...",
        "result_ref": "..."
      }
    }
  ]
}
```

### 7.4 `attempts.jsonl`

保存所有 attempt。

每行一个 JSON。

成功、失败、invalid 都可以出现。

它是 append-only log，方便后续做 policy evidence。

---

## 8. 运行前有哪些配置

### 8.1 必须配置

严格来说，当前 MVP 没有必须配置。

默认即可运行：

```text
controller 状态目录 = .agent_evolve/controller
max_total_tasks = 100
fitness 越高越好
```

### 8.2 建议配置：状态目录

建议主循环在启动 run 前设置：

```text
AGENT_EVOLVE_CONTROLLER_DIR
```

这样可以把 MCP 状态放到你希望的位置。

PowerShell：

```powershell
$env:AGENT_EVOLVE_CONTROLLER_DIR = "C:\evolve\evolve_agent\.agent_evolve\controller"
```

bash：

```bash
export AGENT_EVOLVE_CONTROLLER_DIR=/path/to/.agent_evolve/controller
```

如果不设置，就用：

```text
.agent_evolve/controller
```

### 8.3 建议配置：收敛检测开关

默认启用收敛检测。如果希望临时关闭 MCP stop 判断，可以在启动 Claude Code/MCP 前设置：

PowerShell：

```powershell
$env:AGENT_EVOLVE_CONVERGENCE_ENABLED = "false"
```

bash：

```bash
export AGENT_EVOLVE_CONVERGENCE_ENABLED=false
```

关闭后，`evolve_check_convergence` 会跳过 plateau 软收敛早停。只要没有触发硬预算，它会返回：

```json
{"should_stop": false}
```

这个开关不会覆盖硬预算。即使关闭收敛检测，只要达到 `max_total_tasks` 或 `max_time_seconds`，MCP 仍然会返回 `should_stop=true`。也就是说，关闭后主循环的自动退出条件只剩硬预算，而不会因为 plateau 收敛早停。

### 8.4 内部支持但 MCP 暂未公开的配置

`config.py` 支持 YAML：

```yaml
parent_selection:
  tau_start: 1.5
  tau_end: 0.5
  c_local_start: 1.5
  c_local_end: 0.5
  p_exploit: 0.7
  quality_bonus_strength: 0.3
  elite_rank_fraction: 0.15
  elite_selection_shield: 0.6

inspiration:
  sample_num: 3
  p_migrate: 0.2
  foreign_elite_num: 2
  T_start: 1.5
  T_end: 0.5

convergence:
  enabled: true
  convergence_window: 5
  perf_improvement_threshold: 0.0
  diversity_change_threshold: 0.05
  activity_threshold: 0.9
  patience: 6
  max_time_seconds: 1800
```

但当前 `public_tools.py` 没有传 `config_path`。

所以如果通过 MCP tools 使用，暂时就是默认参数。

如果直接在 Python 内部用：

```python
from agent_evolve_mcp.config import load_evolution_controller_config
from agent_evolve_mcp.controller import EvolutionController

config = load_evolution_controller_config(
    config_path="path/to/config.yaml",
    max_total_tasks=200,
)
controller = EvolutionController("run_001", config=config)
```

可以自定义。

未来如果要让第三方 agent 也能配置，建议加一个新 MCP tool：

```text
evolve_configure_run
```

---

## 9. 参数解释

### 9.1 父代选择参数

#### `tau_start`

默认：

```text
1.5
```

谱系选择的初始温度。

越大越平均，越探索。

#### `tau_end`

默认：

```text
0.5
```

谱系选择的后期温度。

越小越偏向高 fitness 谱系。

#### `c_local_start`

默认：

```text
1.5
```

谱系内 UCB 初始探索系数。

#### `c_local_end`

默认：

```text
0.5
```

谱系内 UCB 后期探索系数。

#### `p_exploit`

默认：

```text
0.7
```

选择谱系时，有 70% 概率走 exploit：

```text
选 deficit * quality_bonus 最大的谱系
```

剩下 30% 走轮盘赌探索。

#### `quality_bonus_strength`

默认：

```text
0.3
```

当多个谱系 deficit 接近时，给更高 fitness 谱系一点奖励。

### 9.2 灵感选择参数

#### `inspiration_sample_num`

默认：

```text
3
```

额外 inspiration 数量。

最终返回通常是：

```text
1 个 parent + 3 个 inspiration
```

#### `p_migrate`

默认：

```text
0.2
```

每个 inspiration 槽位有 20% 概率从其他谱系精英中选。

#### `foreign_elite_num`

默认：

```text
2
```

每条外部谱系取 top-2 作为迁移候选。

#### `T_start`

默认：

```text
1.5
```

Boltzmann 采样初始温度。

#### `T_end`

默认：

```text
0.5
```

Boltzmann 采样后期温度。

### 9.3 收敛检测参数

#### `convergence_window`

默认：

```text
5
```

计算 fitness 改善和 diversity 变化的窗口大小。

#### `perf_improvement_threshold`

默认：

```text
0.0
```

默认不启用模糊边界：任意正的 best fitness 改善都算进展，等值 running best 不累计 S1 plateau。对算子性能这类 verifier 有波动的任务，可以显式设置为 `0.01`，表示窗口内小于 1% 改善算停滞。

#### `diversity_change_threshold`

默认：

```text
0.05
```

多样性变化超过 0.05 才认为明显上升或下降。

当前只用于 diagnostics。

#### `activity_threshold`

默认：

```text
0.8
```

谱系多样性达到 0.8，认为探索相对充分。

#### `patience`

默认：

```text
3
```

连续多少个 plateau window 后才允许 STOPPED。

#### `max_total_tasks`

默认：

```text
100
```

硬安全阀。

当前 MCP tool 暂未暴露配置入口。

#### `max_time_seconds`

默认：

```text
None
```

如果配置了，MCP 可以根据 run state 中的开始时间计算 elapsed_seconds，并按时间预算停止。公开 `evolve_check_convergence` 不要求 agent 传时间。

---

## 10. 未来 plugin 应该如何接

未来 `agent_evolve_plugin` 主循环大概应该是：

```text
1. 生成 run_id
2. 设置/确定 controller state dir
3. 启动 rolling queue
4. 每个 generator subagent:
   4.1 evolve_select_parent
   4.2 evolve_select_inspirations
   4.3 生成 artifact
   4.4 调 verifier CLI
   4.5 保存 verification_result_ref
   4.6 evolve_record_attempt
5. 主 agent:
   5.1 evolve_check_convergence
   5.2 should_stop=false 则补位
   5.3 should_stop=true 则停止提交新 task
6. 汇总 success_db.json、attempts.jsonl、verifier logs、artifacts
```

### 10.1 progress 如何计算

公开 MCP tool 不再要求 agent 传 `progress`。

MCP 内部建议按本地 attempt 数计算：

```python
progress = min(total_submitted / max_total_tasks, 1.0)
```

其中 `total_submitted` 来自 `max(task_contexts 中已注册 task 数, attempts.jsonl 行数)`，`max_total_tasks` 来自控制器配置，默认 100。后续如果 plugin 有自己的 budget，应保持 plugin 和 MCP 的 max_total_tasks 一致。

### 10.2 total_success 如何计算

公开 MCP tool 不再要求 agent 传 `total_success`。

MCP 内部直接读取 `success_db.json` 数量。

### 10.3 task_id 和 attempt_id 的关系

当前默认：

```text
attempt_id = task_id
success_record_id = task_id
```

除非 `verification_result` 里显式传了：

```json
{
  "attempt_id": "custom_id"
}
```

建议主循环保证：

```text
task_id 在同一个 run 内唯一
```

否则可能出现 duplicate。

---

## 11. 当前实现的限制和后续建议

### 11.1 配置尚未通过 MCP 暴露

当前内部支持 YAML config，但 MCP tools 没有配置入口。

建议后续加：

```text
evolve_configure_run
```

字段可包括：

```yaml
run_id: string
max_total_tasks: int
max_time_seconds: float | null
parent_selection: object
inspiration: object
convergence: object
```

### 11.2 没有 run-level 元数据

当前没有 `create_run`。

MCP 不知道任务名称、目标、evaluator 信息。

这些目前由主 agent / plugin 管。

后续如要更完整审计，可加：

```text
evolve_create_run
evolve_get_run
evolve_checkpoint_run
```

### 11.3 没有 diagnostics tool

当前 diagnostics 不通过公开 MCP tool 返回，避免污染 agent 上下文。

如果想单独查看谱系树，可以加：

```text
evolve_get_diagnostics
```

### 11.4 没有 heartbeat

当前 WATCHING 只影响选择参数。

heartbeat / reflect / consolidate / pivot 应该由主循环或 skill 实现。

### 11.5 并发写入还很轻量

当前 JSON 写入是 atomic replace，attempt 是 append JSONL。

如果未来多个 MCP server 进程并发写同一个 run，可能需要文件锁或 SQLite。

当前假设：

```text
同一个 MCP server 进程处理同一个 run 的 tool 调用
```

### 11.6 收敛检测的 diversity history 是简化实现

当前 `_diversity_history()` 会对每个 prefix 重建 LineageTree。

这对 MVP 没问题，但复杂度偏高。

如果 SuccessDB 很大，可以后续把 diversity history 持久化或增量维护。

---

## 12. 最小本地验证方式

在安装 `fastmcp` 后，可以确认 server 使用真实 FastMCP：

```powershell
python -c "import agent_evolve_mcp.mcp_server as s; print(type(s.mcp).__name__)"
```

预期：

```text
FastMCP
```

编译检查：

```powershell
python -m compileall agent_evolve_mcp
```

也可以直接调用 tool 函数做最小本地验证：

```python
from agent_evolve_mcp.public_tools import (
    evolve_select_parent,
    evolve_record_attempt,
    evolve_select_inspirations,
    evolve_check_convergence,
    evolve_finalize_run,
)

run_id = "demo_run"

print(evolve_select_parent(run_id, "task0"))

# In the real flow, verifier writes this JSON file.
verification_result_ref = "artifacts/task0.verification.json"

print(evolve_record_attempt(
    run_id=run_id,
    task_id="task0",
    verification_result_ref=verification_result_ref,
))

parent = evolve_select_parent(run_id, "task1")
print(parent)

print(evolve_select_inspirations(
    run_id,
    "task1"
))

print(evolve_check_convergence(
    run_id
))

print(evolve_finalize_run(
    run_id,
    stop_reason="manual_test",
))
```

---

## 13. 一句话总结

当前 `agent_evolve_mcp` 是一个通用 fitness 最大化的 Evolution Controller MCP：

```text
public_tools.py 暴露五个 evolve_* MCP tools
controller.py 串起 SuccessDB、LineageTree 和三求解器
SuccessDB 保存成功经验
AttemptLog 保存全部尝试
ParentSelector 选父代
InspirationSelector 选灵感
ConvergenceDetector 判断继续/观察/停止
```

它已经从 AKG 场景迁移成了通用自进化底座组件，后续主循环只需要按协议调用这五个 MCP tools 即可。
