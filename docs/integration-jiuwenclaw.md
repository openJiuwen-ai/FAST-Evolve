# Agent Evolve 集成到 JiuwenClaw 指南

将 [Agent Evolve](https://github.com/algorithmicsuperintelligence/evolve_agent) 的自进化能力层集成到 JiuwenClaw 中，使 agent 具备 evaluator-guided adaptive search 能力。

## 架构概览

```
JiuwenClaw Agent
  + evolve skill            （进化编排，调度 task 队列）
  + task-executor agent     （执行单个 task 的完整生命周期）
  + Evolution Controller MCP（策略状态服务，父代/灵感选择、收敛检测）
  + Benchmark Skills        （提供具体问题的 SKILL.md 和 evaluator 脚本）
  = 具备自进化能力的 Agent
```

## 集成步骤

### Step 1：复制 MCP 服务器代码

将 `agent_evolve_mcp/` 包复制到 jiuwenclaw 项目根目录：

```bash
cp -r evolve_agent/agent_evolve_mcp/ jiuwenclaw/agent_evolve_mcp/
```

MCP 服务器是独立进程，通过 `python -m agent_evolve_mcp` 启动。包含 5 个工具：

| MCP Tool | 功能 |
|----------|------|
| `evolve_select_parent` | 为 task 选择父代（返回 id + artifact_ref） |
| `evolve_select_inspirations` | 选择灵感参考（Boltzmann 采样 + 跨谱系迁移） |
| `evolve_record_attempt` | 记录 task 的验证结果，成功的加入 SuccessDB |
| `evolve_check_convergence` | 多维收敛检测（性能平台 + 多样性 + 谱系活跃度） |
| `evolve_finalize_run` | 生成确定性的 run summary 报告 |

### Step 2：更新 pyproject.toml

在 `dependencies` 中添加缺失的依赖：

```toml
"pyyaml>=6.0",
"tqdm>=4.64.0",
```

> `fastmcp` 和 `numpy` 已在 jiuwenclaw 中存在，无需额外添加。

在 `[tool.setuptools.packages.find]` 中添加包发现：

```toml
include = ["openjiuwen*", "jiuwenclaw*", "agent_evolve_mcp*"]
```

### Step 3：配置 MCP 服务器

在 `~/.jiuwenclaw/config/config.yaml` 中添加 MCP 配置（如已存在则跳过）：

```yaml
mcp:
  servers:
    - name: agent_evolve_mcp
      enabled: true
      transport: stdio
      command: python
      args: ["-m", "agent_evolve_mcp"]
```

### Step 4：安装 Skills

将 skills 复制到 jiuwenclaw 运行时目录：

```bash
SKILLS_DIR=~/.jiuwenclaw/agent/jiuwenclaw_workspace/skills

# 核心 skill：进化编排（含 task-executor 子 agent）
cp -r evolve_agent/agent_evolve_plugin/skills/evolve "$SKILLS_DIR/evolve"
mkdir -p "$SKILLS_DIR/evolve/agents"
cp evolve_agent/agent_evolve_plugin/agents/task-executor.md "$SKILLS_DIR/evolve/agents/"

# 核心 skill：架构文档生成
mkdir -p "$SKILLS_DIR/design_doc"
cp evolve_agent/agent_evolve_plugin/skills/design_doc/SKILL.md "$SKILLS_DIR/design_doc/"

# Benchmark skills（17 个数学优化问题）
for skill in circle_packing circle_packing_rect erdos_min_overlap \
  first_autocorr_ineq heilbronn_convex_13 heilbronn_convex_14 \
  heilbronn_triangle hexagon_packing_11 hexagon_packing_12 \
  kernelbench_layernorm matmul minimizing_max_min_dist_2 \
  minimizing_max_min_dist_3 second_autocorr_ineq signal_processing \
  sums_diffs_finite_sets uncertainty_ineq; do
  cp -r "evolve_agent/benchmarks/skills/$skill" "$SKILLS_DIR/$skill"
done
```

### Step 5：验证

```bash
cd jiuwenclaw

# 1. 验证 MCP 服务器导入
.venv/Scripts/python -c "from agent_evolve_mcp import mcp; print(mcp.name)"
# 期望输出: agent_evolve

# 2. 验证工具注册
.venv/Scripts/python -c "
from agent_evolve_mcp import mcp
for name in mcp._tool_manager._tools:
    print(f'  - {name}')
"
# 期望输出 5 个 evolve_* 工具

# 3. 重启 jiuwenclaw 并在 Web UI 中确认 skill 列表
```

## Skills 清单

### 核心 Skills

| Skill | 描述 |
|-------|------|
| `evolve` | 自进化主调度器，维护 task 队列，检测收敛，生成 run summary |
| `design_doc` | 读取代码库生成完整架构文档 |

### Benchmark Skills

| Skill | 问题描述 |
|-------|---------|
| `circle_packing` | 在单位正方形内排列 26 个圆，最大化半径之和 |
| `circle_packing_rect` | 矩形内的圆排列优化 |
| `matmul` | 矩阵乘法优化 |
| `kernelbench_layernorm` | LayerNorm kernel 优化 |
| `heilbronn_triangle` | Heilbronn 三角形问题 |
| `heilbronn_convex_13` | Heilbronn 凸包问题 (n=13) |
| `heilbronn_convex_14` | Heilbronn 凸包问题 (n=14) |
| `hexagon_packing_11` | 六边形排列 (n=11) |
| `hexagon_packing_12` | 六边形排列 (n=12) |
| `minimizing_max_min_dist_2` | 最小化最大最小距离 (2D) |
| `minimizing_max_min_dist_3` | 最小化最大最小距离 (3D) |
| `first_autocorr_ineq` | 第一自相关不等式 |
| `second_autocorr_ineq` | 第二自相关不等式 |
| `signal_processing` | 信号处理优化 |
| `sums_diffs_finite_sets` | 有限集合的和与差 |
| `uncertainty_ineq` | 不确定性不等式 |
| `erdos_min_overlap` | Erdős 最小重叠问题 |

## 使用方法

在 jiuwenclaw 的聊天界面中直接说：

```
用 evolve 优化 circle_packing 问题
```

或：

```
/evolve circle_packing
```

### 运行流程

```
1. evolve skill 初始化 run → 生成 run_id（如 run_20260527163000）
2. 调度器启动最多 4 个并发 task
3. 每个 task 由 task-executor agent 执行：
   a. select_parent     → 从 SuccessDB 选择父代
   b. select_inspirations → 选择灵感参考
   c. 生成 solution.py  → 基于父代 + 灵感改进
   d. 运行 evaluator    → 确定性验证，输出 fitness
   e. record_attempt    → 成功的记录到 SuccessDB
4. 收敛检测通过后 → finalize_run 生成 summary
```

### 运行结果

状态文件保存在 `.agent_evolve/controller/{run_id}/` 下：

| 文件 | 内容 |
|------|------|
| `success_db.json` | 成功的可复用记录池 |
| `attempts.jsonl` | 全部尝试的审计日志 |
| `task_contexts.json` | task 到 parent/inspiration 的选择缓存 |
| `run_state.json` | 收敛状态和诊断信息 |

运行报告保存在 `runs/{run_id}/` 下：

| 文件 | 内容 |
|------|------|
| `run_summary.md` | 可读的运行总结 |
| `run_summary.json` | 结构化的运行数据 |

## 注意事项

- **Python 环境**：task-executor 执行 evaluator 时需要确保 Python 环境有 `numpy` 等依赖。如果使用 venv，需要在 SKILL.md 中指定完整 Python 路径。
- **MCP 配置**：`config.yaml` 中的 `command: python` 使用系统 PATH 中的 Python。如果需要指定 venv，改为 venv 的 Python 完整路径。
- **状态持久化**：MCP 服务器每次操作前从磁盘重载状态，重启不影响已记录的数据。
