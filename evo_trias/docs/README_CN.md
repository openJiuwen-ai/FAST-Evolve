<div align="center">

<img src="../assets/evotrias-banner.png" alt="EvoTrias" width="500">

[English](../README.md) | **中文**

</div>

EvoTrias 将编码智能体、任务评估器和持久化搜索状态组织在同一个进化循环中。
只需提供优化任务与验证器，它就能并行探索多种方向、评估候选方案、保留有效发现，
并基于当前最优结果持续搜索。

## 核心能力

- **并行探索**：从彼此不同的初始方向出发，以滚动任务队列持续生成候选方案。
- **验证器驱动**：候选方案的适应度由各任务自带的评估器决定，任务评分逻辑与控制器解耦。
- **持久化搜索经验**：记录成功候选、谱系、尝试历史和本轮 Notes，供后续任务复用。
- **策略自适应**：检测搜索停滞，并可生成有使用上限的元指导，以改进或替换当前策略。
- **实时可观测**：只读仪表盘集中展示适应度、谱系、活跃任务、近期活动、Notes 和元指导。

## 工作原理

```text
宿主编码智能体
  └─ 初始化运行、调度任务，并仅按 ID 委派元指导请求
       ↓
Evolution Controller MCP
  ├─ 管理选择、谱系、Notes、收敛状态和元指导状态
  └─ 向无状态的 evotrias-meta-guidance-generator 智能体提供 pending request
       ↓
evotrias-task-executor 智能体
  └─ 生成候选方案并调用任务评估器
       ↓
任务评估器
  └─ 向控制器返回归一化适应度与产物
```

控制器将详细状态持久化到磁盘，只向智能体传递紧凑的任务上下文。这样，长时间运行的
搜索可以复用既有发现，而不会不断占用宿主智能体的上下文窗口。

## 快速开始

### 环境要求

- Python 3.11+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) 或
  [opencode](https://opencode.ai)

### 安装

在仓库根目录运行：

```bash
pip install ./evo_trias
```

### 配置工作区

```bash
mkdir my-workspace
cd my-workspace
evotrias setup
```

选择一个 harness，setup 会把 EvoTrias 的智能体、技能和 harness 配置装进工作区。
再次运行 `evotrias setup` 可以为同一工作区再配置另一个 harness。

### 添加待进化的任务

把待进化的任务写成一个技能：在它的 `SKILL.md` 中描述任务本身、候选解如何生成、
候选解如何评测。然后把这个技能目录拷进工作区的技能目录（setup 会提示位置）。

### 运行

```bash
evotrias
```

它会启动最后一次 setup 的 harness。然后提交进化任务，点名要进化的任务技能：

```text
/evolve evolve <task skill name>
```

EvoTrias 会在 `runs/` 下创建具名运行，启动仪表盘，探索多个初始方向，并持续调度
候选方案，直至达到配置的任务预算或时间预算。

## 仓库结构

```text
evotrias/               核心包：db（状态）、evolve_ctrl（进化控制）、scheduler、permissions、mcp（服务入口）、hooks、dashboard、setup、cli
evotrias/templates/     由 "evotrias setup" 安装的智能体、技能与 harness 配置
docs/                   MCP 接口文档
```

控制器 MCP 的工具契约见 [docs/mcp_interface_CN.md](mcp_interface_CN.md)。

运行产物保存在 Git 忽略的 `runs/` 和 `.evotrias/` 目录中。每次完成的运行都会
生成机器可读结果和简洁的 Markdown 总结。

## 默认设置与运行时开关

| 设置 | 默认值 | 作用 |
| --- | ---: | --- |
| `LINEAGE_ROOT_NUMBER` | 4 | 初始探索方向数，即根探索分支数量 |
| `MAX_CONCURRENT_TASKS` | 4 | 滚动调度队列上限 |
| `MAX_TOTAL_TASKS` | 50 | 任务硬预算 |
| 停滞检测窗口 | 5 个成功候选 | 用于判断搜索是否停滞 |
| 停滞耐心值 | 4 | 连续达到停滞条件后触发策略调整 |
| 适应度提升阈值 | 0.0 | 最优适应度只要正向提升即视为进展 |
| 每次元指导数量 | 3 | 单次生成的不同元指导上限 |
| 每条元指导使用次数 | 2 | 每条指导最多分配给多少个任务 |
| 已尝试元指导保留数 | 10 | 后续生成指导时可参考的结果历史 |

上表前三个调度参数可以直接写在进化请求里（例如"6 initial directions, 8 in parallel, 200 tasks"）；
它们会在创建运行前得到确认，并在运行初始化后锁定。

下列环境变量需要在 MCP 进程启动前设置；它们由 `configure_run` 一次性解析并固化到该 run 的
`config.json`：

```powershell
# 关闭元指导，但保留停滞诊断和正常选择逻辑。
$env:EVOTRIAS_META_EVOLVE_ENABLED = "0"

# 关闭本轮 Notes。
$env:EVOTRIAS_NOTES_ENABLED = "0"

# 关闭仪表盘自动启动。
$env:EVOTRIAS_UI_ENABLED = "0"

# 将低于 1% 的相对最优适应度提升视为停滞。
$env:EVOTRIAS_PERF_IMPROVEMENT_THRESHOLD = "0.01"
```

无论是否检测到停滞，任务预算和时间预算始终有效。

如需远程访问仪表盘，在启动 EvoTrias 前设置：

```bash
export EVOTRIAS_UI_HOST=0.0.0.0
export EVOTRIAS_UI_PUBLIC_HOST="<HOST_OR_IP>"
```

使用 EvoTrias 打印的 URL 访问。仅本机访问时不要设置这两个变量。

## 运行产物

EvoTrias 将 agent 工作区与控制器内部状态分开保存，两者都相对当前工作目录：

```text
runs/<run_id>/                                        agent 工作区
  tasks/<task_id>/direction.txt                       由 evotrias-init-explorer 写入
  tasks/<task_id>/attempts/<attempt_id>/solution.py   由 evotrias-task-executor 写入（或 solution.cpp）
  tasks/<task_id>/attempts/<attempt_id>/eval_result.json
  run_summary.md                                      由 finalize_run 写入
  run_summary.json
```

```text
.evotrias/
  db/runs/<run_id>/   控制器状态（config、run state、attempts、notes、meta-guidance、task pool）
  hooks/              hook 私有状态
  locks/              跨进程锁
  logs/               MCP 进程日志
```

每个 run 的状态文件清单见 [docs/mcp_interface_CN.md](mcp_interface_CN.md) 第 3 节。
这两个位置都属于本地运行状态，并已被 Git 忽略。需要分享结果时，应使用最终总结或
经过整理的报告，而不是提交完整运行目录。
