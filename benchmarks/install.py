#!/usr/bin/env python
"""Benchmark installer — injects Skill and evaluator permissions into settings.local.json.

Usage:
    uv run python benchmarks/install.py circle_packing circle_packing_rect
    uv run python benchmarks/install.py --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).parent / "skills"
SETTINGS_LOCAL = Path(__file__).resolve().parent.parent / ".claude" / "settings.local.json"


def discover_benchmarks() -> list[str]:
    """Return benchmark names that have SKILL.md + scripts/evaluator.py."""
    names = []
    for child in sorted(BENCHMARKS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if (child / "SKILL.md").exists() and (child / "scripts" / "evaluator.py").exists():
            names.append(child.name)
    return names


def validate_benchmark(name: str) -> bool:
    """Check that a benchmark directory has the required files."""
    bench_dir = BENCHMARKS_DIR / name
    skill_md = bench_dir / "SKILL.md"
    evaluator = bench_dir / "scripts" / "evaluator.py"
    if not bench_dir.is_dir():
        print(f"  ERROR: directory not found: {bench_dir}")
        return False
    if not skill_md.exists():
        print(f"  ERROR: SKILL.md not found: {skill_md}")
        return False
    if not evaluator.exists():
        print(f"  ERROR: scripts/evaluator.py not found: {evaluator}")
        return False
    return True


def load_settings(*, clear: bool = False) -> dict:
    """Load settings.local.json, creating initial structure if absent.

    Args:
        clear: If True, start with a fresh empty structure instead of loading existing.
    """
    if not clear and SETTINGS_LOCAL.exists():
        with SETTINGS_LOCAL.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"permissions": {"allow": []}}


def save_settings(settings: dict) -> None:
    """Persist settings.local.json with sorted allow list for determinism."""
    SETTINGS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    settings["permissions"]["allow"].sort()
    with SETTINGS_LOCAL.open("w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")


def install_benchmark(name: str, settings: dict) -> bool:
    """Inject permissions for one benchmark. Returns True if any entry was added."""
    if not validate_benchmark(name):
        return False

    allow_list = settings.setdefault("permissions", {}).setdefault("allow", [])
    entries = [
        f"Skill(benchmarks:{name})",
        f"Bash(uv run python benchmarks/skills/{name}/scripts/evaluator.py *)",
    ]

    added = False
    for entry in entries:
        if entry not in allow_list:
            allow_list.append(entry)
            added = True

    if added:
        print(f"  + {name}: permissions injected")
    else:
        print(f"  = {name}: already installed (skipped)")

    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Install benchmark permissions")
    parser.add_argument("benchmarks", nargs="*", help="Benchmark names to install")
    parser.add_argument("--all", action="store_true", help="Install all discovered benchmarks")
    parser.add_argument("--clear", action="store_true", help="Clear existing settings before installing")
    args = parser.parse_args()

    if args.all:
        names = discover_benchmarks()
        if not names:
            print("No benchmarks found.")
            sys.exit(1)
        print(f"Discovered {len(names)} benchmarks")
    elif args.benchmarks:
        names = args.benchmarks
    else:
        parser.error("Provide benchmark names or use --all")

    settings = load_settings(clear=args.clear)
    if args.clear:
        print("Cleared existing settings.")

    any_changed = False

    for name in names:
        if install_benchmark(name, settings):
            any_changed = True

    if any_changed:
        save_settings(settings)
        print(f"\nSettings written to {SETTINGS_LOCAL}")
    else:
        print("\nNo changes needed.")


if __name__ == "__main__":
    main()
