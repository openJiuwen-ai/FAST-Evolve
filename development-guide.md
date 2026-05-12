# AI-Assisted Development Guide

This guide explains how to leverage Claude for AI-assisted development in this project.

## Prerequisites

- A working Claude Code installation connected to a large language model
- Start the Claude Code CLI session in the project directory

## Initial Setup

### Configure GitCode Token

Before using AI-assisted development features, you need to configure your GitCode token:

1. Generate a token at [GitCode Token Settings](https://gitcode.com/setting/token-classic)
2. Add the token to your Claude Code settings:

Create or edit `~/.claude/settings.json`:

```json
{
  "env": {
    "GITCODE_TOKEN": "your_token_here"
  }
}
```

Replace `your_token_here` with your actual GitCode token.

## Using AI-Assisted Development

### Creating Issues

Use the `/dev-create-issue` slash command to create issues directly from your conversation with Claude.

**Usage:**

```
/dev-create-issue [issue description]
```

**Example:**

```
/dev-create-issue XXX did not work correctly.
```
