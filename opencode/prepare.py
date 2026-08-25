"""One-way sync of agents and skills into the opencode workspace.

Sources (read-only, single source of truth):
  agent_evolve_plugin/agents/*.md   -> .opencode/agents/
  agent_evolve_plugin/skills/*      -> .opencode/skills/
  benchmarks/skills/<p>, p in PROBLEMS -> .opencode/skills/

Every copied file goes through one rule pipeline; a file that matches
no rule is effectively copied verbatim.
"""

from __future__ import annotations

import shutil
from pathlib import Path

PROBLEMS = ["circle_packing"]

WORKSPACE = Path(__file__).resolve().parent
ROOT = WORKSPACE.parent
AGENTS_DST = WORKSPACE / ".opencode" / "agents"
SKILLS_DST = WORKSPACE / ".opencode" / "skills"

# Content rules: plain text replacements applied to every copied .md file.
CONTENT_RULES = [
    ("mcp__plugin_agent_evolve_plugin_ctrl__", "evotrias_"),  # MCP tool name format
    ("agent_evolve_plugin:", ""),                             # subagent_type is a bare name
    ("run_in_background", "background"),                      # task tool arg name
    ("benchmarks/skills/", "../benchmarks/skills/"),          # cwd is opencode/
]

# Builtin tool names, Claude -> opencode. MCP names are already handled
# by CONTENT_RULES before this map is consulted.
TOOL_NAMES = {
    "Skill": "skill",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Glob": "glob",
    "Grep": "grep",
    "Bash": "bash",
    "WebFetch": "webfetch",
}


def apply_content_rules(text: str) -> str:
    for old, new in CONTENT_RULES:
        text = text.replace(old, new)
    return text


def convert_agent(text: str) -> str:
    """Rewrite Claude agent frontmatter to opencode format, keep body as is.

    Output frontmatter carries only: description, mode, tools.
    """
    lines = text.split("\n")
    end = lines.index("---", 1)
    description = ""
    tools: list[str] = []
    for line in lines[1:end]:
        if line.startswith("description:"):
            description = line.removeprefix("description:").strip()
        elif line.startswith("tools:"):
            tools = [t.strip() for t in line.removeprefix("tools:").split(",")]
    out = ["---", f"description: {description}", "mode: subagent"]
    if tools:
        out.append("tools:")
        out.append('  "*": false')
        out += [f"  {TOOL_NAMES.get(t, t)}: true" for t in tools]
    out.append("---")
    return "\n".join(out) + "\n" + "\n".join(lines[end + 1 :])


def sync_file(src: Path, dst: Path) -> None:
    if src.suffix == ".md":
        dst.write_text(apply_content_rules(src.read_text(encoding="utf-8")), encoding="utf-8")
    else:
        shutil.copy2(src, dst)


def sync_skill(src_dir: Path) -> None:
    kebab = src_dir.name.replace("_", "-")
    for src in src_dir.rglob("*"):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        dst = SKILLS_DST / kebab / src.relative_to(src_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        sync_file(src, dst)
        if src.name == "SKILL.md":
            text = dst.read_text(encoding="utf-8")
            text = text.replace(f"name: {src_dir.name}", f"name: {kebab}")
            # argument-hint is Claude-only and its bracket value is invalid
            # YAML; opencode silently drops the whole skill on parse failure.
            text = "\n".join(l for l in text.split("\n") if not l.startswith("argument-hint:"))
            dst.write_text(text, encoding="utf-8")


def main() -> None:
    for d in (AGENTS_DST, SKILLS_DST):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    for src in sorted((ROOT / "agent_evolve_plugin" / "agents").glob("*.md")):
        text = apply_content_rules(src.read_text(encoding="utf-8"))
        (AGENTS_DST / src.name).write_text(convert_agent(text), encoding="utf-8")

    skill_dirs = sorted(p for p in (ROOT / "agent_evolve_plugin" / "skills").iterdir() if p.is_dir())
    skill_dirs += [ROOT / "benchmarks" / "skills" / p for p in PROBLEMS]
    for src_dir in skill_dirs:
        sync_skill(src_dir)

    print(f"Synced {len(list(AGENTS_DST.glob('*.md')))} agents, {len(list(SKILLS_DST.iterdir()))} skills.")
    print()
    print("Next steps:")
    print('  1. Set env var: OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS="true"')
    print(f"  2. Run opencode in {WORKSPACE}")
    print("  3. Start an experiment, e.g.:")
    print("     /evolve solve circle packing, with max task number is 5, concurrency is 2, root number is 3")


if __name__ == "__main__":
    main()
