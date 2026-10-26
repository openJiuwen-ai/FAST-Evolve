<div align="center">

<img src="assets/fast-evolve-banner.png" alt="FAST-Evolve" width="600">

[English](README.md) | **中文**

</div>

## 🌱 简介

FAST-Evolve 是面向算法优化的一站式智能体进化工具链。给出优化目标和评测标准，它就能驱动 Coding Agent 自主探索优化方案，依据评测反馈持续迭代，让更优解不断涌现。

当前，FAST-Evolve 通过 **EvoTrias** 为编码智能体提供程序进化能力：将优化任务与评估器接入工作区，驱动智能体持续生成、评测和筛选候选程序。EvoTrias 已接入 Claude Code 和 OpenCode，详细用法见 [EvoTrias 文档](evo_trias/README.zh.md)。

## 🚀 快速开始

以下以 Claude Code 和内置的 Circle Packing 任务为例。该任务要求在单位正方形内放置 26 个互不重叠的圆，使半径之和最大。

准备 Python 3.11 或更高版本，并安装、配置好 Claude Code 及其模型服务。建议在 Python 虚拟环境中安装依赖。

### 1. 获取代码并安装

```bash
git clone https://gitcode.com/openJiuwen/FAST-Evolve.git
pip install ./FAST-Evolve/evo_trias
pip install -r FAST-Evolve/benchmarks/requirements/math.txt
evotrias --version
```

### 2. 创建工作区并导入任务

在仓库的同级目录创建工作区，初始化 EvoTrias 并复制任务 skill：

```bash
mkdir my-workspace && cd my-workspace
evotrias setup claude
cp -r ../FAST-Evolve/benchmarks/skills/circle-packing .claude/skills/
```

### 3. 启动进化

```bash
evotrias
```

在启动的智能体会话中输入：

```text
/evolve evolve circle-packing, 2 initial directions, 2 in parallel, 10 tasks
```

确认运行参数后，EvoTrias 将从两个初始方向开展探索，最多并行执行两个任务，在 10 个任务的预算内持续迭代。运行期间可通过输出的仪表盘地址查看进展，结束后在工作区的 `runs/<run_id>/run_summary.md` 查看总结。

如需接入自定义优化任务或使用其他编码智能体，请参阅 [EvoTrias 文档](evo_trias/README.zh.md)和[基准任务示例](benchmarks/skills/)。

## 🏗️ 架构简介

EvoTrias 将进化过程分为治理、提案和裁决三个部分：

| 部分 | 职责 |
| --- | --- |
| 治理（Structured Governance） | 由控制器管理任务预算、调度与搜索状态，依据评测证据选择后续探索方向。 |
| 提案（Open Proposal） | 由编码智能体探索优化策略，生成和修改候选程序。 |
| 裁决（Protected Judgment） | 通过评估器检查候选程序的有效性并计算适应度，为筛选和下一轮迭代提供反馈。 |

三者通过明确的任务与结果契约协作，形成“提出方案 → 评测反馈 → 筛选与迭代”的闭环。

## ✨ 功能特性

- 🔌 **插件化接入**：通过 skill、MCP 服务和 hooks 将进化能力接入编码智能体工作区。
- 🧭 **多方向并行探索**：支持从多个初始方向出发，在任务预算内并行探索与持续优化。
- 🔄 **评测反馈驱动**：依据候选程序的有效性和适应度进行选择，将有效经验沉淀为 Notes，并在搜索停滞时生成策略引导。
- 🛡️ **评测访问控制**：在 Claude Code 中，通过权限 hooks 限制对评估器源码及其他任务结果的访问。
- 📊 **运行过程可观测**：提供只读仪表盘、运行记录与 Markdown 总结，便于跟踪进展和复盘结果。

## 🤝 参与贡献

欢迎通过 [Issue](https://gitcode.com/openJiuwen/FAST-Evolve/issues) 反馈问题、提出建议，或通过 [Pull Request](https://gitcode.com/openJiuwen/FAST-Evolve/pulls) 贡献代码、文档和基准任务。

提交贡献前，请阅读 [openJiuwen 社区贡献指南](https://gitcode.com/openJiuwen/community/blob/main/CONTRIBUTING.md)，并按[签署指南](https://gitcode.com/openJiuwen/community/blob/main/cla/cla使用指南.md)完成 CLA 签署。开发时请先 fork 本仓库，从最新的 `main` 创建分支，完成修改与验证后提交 Pull Request。

## License

本项目基于 [Apache License 2.0](https://gitcode.com/openJiuwen/FAST-Evolve/tree/main/LICENSE) 开源。

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。
