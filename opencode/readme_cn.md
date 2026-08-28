# EvoTrias on OpenCode

通过 [opencode](https://opencode.ai) 运行 EvoTrias 进化实验。

## 安装

**在本目录**执行：

```
python prepare.py
```

然后设置一个环境变量：

```powershell
# Windows (PowerShell)
$env:OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS="true"
```

```bash
# Linux / macOS
export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true
```

## 运行实验

**在本目录**启动 opencode：

```
opencode
```

在 opencode 中用 `/evolve` 命令发起实验，例如：

```
/evolve solve circle packing, with max task number is 5, concurrency is 2, root number is 3
```
