---
name: dev-create-issue
description: 当用户要求创建 issue、提交问题、报告 bug、提出 feature request 时使用。在 gitcode.com/taylor_cps/evolve_agent 仓库创建 issue。
---

# GitCode Issue 创建技能

当用户要求创建 issue 时，执行以下流程：

## 流程

1. **收集 issue 内容**
   - 如果用户提供了 issue 内容，整理成标题和正文
   - 如果用户没有明确提供，从对话上下文中总结 issue 内容
   - 要求用户提供一个标题（如果没有的话）
   - 要求用户提供问题复现概率（高/中/低）
   - **获取当前环境信息**（将在确认内容中展示）:
     - Git 分支: `git branch --show-current`
     - 最新 Commit: `git log -1 --pretty=format:"%H%n%s%n%an%n%ae%n%ci"`
     - Claude 模型: 从你的名字中获取（例如 "GLM 5.1"、"MiniMax-M2.5" 等）

2. **呈现给用户确认**
   - 将整理好的 issue 内容（标题 + 正文）以 markdown 格式展示给用户
   - **在正文末尾附加环境信息**（使用 markdown 块）:
     ```
     ---
     **复现概率**：`高` / `中` / `低`（根据用户描述判断）
     **环境信息**
     - Git 分支: `<分支名>`
     - 最新 Commit: `<commit hash> | <commit message> | <author> | <date>`
     - Claude 模型: `<模型名称>`
     ```
   - 明确告诉用户这是将要提交的内容，请求确认
   - 等待用户确认后再进行下一步

3. **调用 gitcode API 创建 issue**
   - 使用 gitcode API 技能在 https://gitcode.com/taylor_cps/evolve_agent 仓库创建 issue
   - 提交用户确认后的**精确版本**，不要做任何修改

4. **展示结果**
   - 向用户展示创建成功的 issue 链接
   - 提示用户可以添加额外的评论或图片

## 交互要求

- 全程用中文与用户交流，不要使用英文
- 始终保持友好和清晰
- 在创建前必须获得用户的最终确认
- 提交的内容必须是用户确认过的精确版本

## 注意事项

- 如果对话上下文不足以总结 issue 内容，直接询问用户
- 确保标题简洁明了，正文包含足够的细节
- **标题要描述问题现象，而不是讲怎么优化**（例如："Task 批量下发"而非"Task 应该完成一个补位一个"）
- 创建成功后，务必提供 issue 链接并提示用户可以补充内容
- **GitCode 不是 GitHub**：创建 issue 时必须使用本 skill 提供的 Python + requests 方法或 gitcode-api skill 提供的接口，**不要使用** GitHub 相关的工具（如 github-plugin MCP 工具、mcp__plugin_github_github 系列工具等）
- 使用 `python -c` 执行包含中文的字符串时，命令行输出可能会显示乱码（这是由于 shell 转义问题），但 API 调用本身是正常的，issue 仍可成功创建。返回结果中的 `https://gitcode.com/taylor_cps/evolve_agent/issues/N` 就是创建的 issue 链接

## API 调用方法

**必须使用 Python + requests 库**，避免编码问题：
- 使用 `python -c` 直接执行，不要创建临时文件
- 先获取环境信息，然后执行 API 调用
- 执行完成后删除任何临时文件（如果有）

### 第一步：获取环境信息
```python
import subprocess
import os

# Git 分支
branch = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True).stdout.strip()

# 最新 Commit: hash | message | author | email | date
commit_info = subprocess.run(
    ['git', 'log', '-1', '--pretty=format:%H|%s|%an|%ae|%ci'],
    capture_output=True, text=True
).stdout.strip()

# Claude 模型
claude_model = os.environ.get('CLAUDE_MODEL', os.environ.get('ANTHROPIC_MODEL', 'unknown'))

print(f"BRANCH:{branch}")
print(f"COMMIT:{commit_info}")
print(f"CLAUDE_MODEL:{claude_model}")
```

### 第二步：创建 Issue
```python
import requests
import os

url = 'https://api.gitcode.com/api/v5/repos/taylor_cps/evolve_agent/issues'
token = os.environ.get('GITCODE_TOKEN', '')
headers = {
    'PRIVATE-TOKEN': token,
    'Content-Type': 'application/json'
}
data = {
    'title': 'Issue 标题',
    'body': 'Issue 正文内容\n\n---\n\n**环境信息**\n- Git 分支: xxx\n- 最新 Commit: xxx\n- Claude 模型: xxx'
}

resp = requests.post(url, headers=headers, json=data)
result = resp.json()
print(result.get('html_url'))
```

**不要使用：** curl、PowerShell、python3，这些都有编码问题。
