---
name: evotrias-init-explore
description: Generate initial exploration directions for evolutionary tasks.
---

Generate initial exploration directions for evolutionary tasks.

First, confirm these critical identifiers from the context:
- run_id: Unique identifier for the evolution run
- task_description: What the task should accomplish
- num_directions: Number of directions to generate (default 4)

CRITICAL: If any of these are missing, ask immediately before proceeding.

Then execute the following steps in EXACT ORDER:

---

## Step 1: Understand Task

1. Locate and read the problem SKILL.md if it exists. The problem SKILL.md is typically found in the project root or in a benchmark directory.
2. Follow the problem SKILL.md's public instance-selection instructions using the original `task_description`. If the skill requires an instance or problem ID, validate it and read the public statement/metadata it declares. Never infer a benchmark instance from an evolution `task_id`.
3. Resolve the initial reference declared by the problem SKILL.md. It may be a problem-specific path such as `problems/<problem_id>/initial_program.cpp`. If the skill does not declare one, fall back to a sibling `initial_program.py` when present. Read the resolved file as `initial_program_reference`.
4. Understand the verification method, what constitutes a successful solution, and the reference program's main algorithmic approach if one exists.
5. Do not read verifier/evaluator scripts or judge-side resources. Only read the problem SKILL.md and the public problem resources and initial reference it explicitly declares.

---

## Step 2: Analyze Problem Structure

Before generating directions, analyze the problem to identify its key structural decision points — the places where a solver can meaningfully diverge into different approaches.

Ask yourself:
- What are the **core decisions** a solution must make? (e.g., one-shot vs iterative, exact vs heuristic, deterministic vs random)
- What **information is available** to the solver? (e.g., feedback after each step, no feedback at all, partial observability)
- What **constraints shape the solution space**? (e.g., fixed budget of attempts, single attempt only, time limit)
- What **failure modes** are possible? (e.g., wrong on first try, converges slowly, overshoots)

Write a brief problem analysis (3-5 lines) covering:
1. Problem type (one-shot, iterative, optimization, construction, etc.)
2. Available information and feedback
3. Key decision axes where approaches can diverge

This analysis is internal — do not save it to a file. It guides direction generation.

---

## Step 3: Generate Directions

Generate exactly `num_directions` different directions. Each direction must be:
- One line, 8-25 words in English (SHORT and PRECISE)
- Clearly distinct from the other directions
- Actionable, focusing on a specific approach
- Feasible and meaningful for the given task

If an `initial_program_reference` exists, use it as important context for the task interface, baseline algorithm, and known feasible starting route. Directions may improve, specialize, simplify, or deliberately depart from that reference. Keep the set diverse; do not generate four directions that merely restate the initial program's current approach.

Directions MUST be diverse enough so that following them produces significantly different solutions. Use the problem analysis from Step 2 to identify **problem-specific** axes of divergence — the decision points unique to this particular problem — rather than applying generic coding-style axes.

Examples of problem-specific axes for different types of tasks:
- **Sorting**: different algorithms (bubble, merge, heap, radix) — each uses fundamentally different logic
- **Pathfinding**: different strategies (BFS, DFS, Dijkstra, A*) — each explores the graph differently
- **String matching**: different approaches (brute force, KMP, Rabin-Karp, Boyer-Moore) — each has distinct pattern

Implementation choices such as mathematical representation, core primitives or operators, and available scientific-computing libraries are legitimate secondary axes when they enable a genuinely different problem-solving route. Consider them explicitly instead of automatically inheriting the initial program's stack, but do not force a dependency change when the underlying algorithm would remain the same.

EXAMPLES of good precise directions:
- "Pure functional approach with no classes, just functions"
- "Object-oriented design with clear class hierarchy"
- "Minimal dependencies, only basic Python built-ins"
- "Use Python standard library modules like itertools, collections"

EXAMPLES of BAD directions (avoid):
- "Good implementation" (too vague)
- "Write some code" (not actionable)
- "Better solution" (no specific direction)

---

## Step 4: Cross-Check Diversity

Compare all generated directions pairwise. For each pair, ask:

**Would following direction A and direction B produce solutions that are essentially the same?**

If two directions would converge to the same solution or use the same core strategy:
- Replace the weaker/less diverse direction with a new one that explores a genuinely different axis
- Re-check the new direction against all others
- Repeat until every pair is clearly distinct

Specific red flags to catch:
- Two directions that rely on the same mathematical insight (e.g., both use halving/midpoint logic)
- Two directions that differ only in surface details but share the same core algorithm
- Two directions that are both "generic good coding" without a specific strategy angle
- All directions inherit the initial program's representation, primitives, or dependencies even though plausible alternatives would enable different algorithms

This is a text-level comparison only — never execute or simulate tasks during this step.

---

## Step 5: Save Directions

For each task, create the directory and save the direction file:

1. For task_id from 1 to num_directions:
   - Create directory: `runs/<run_id>/tasks/task_<task_id:03d>/`
   - Save file: `runs/<run_id>/tasks/task_<task_id:03d>/direction.txt`
   - File content: exactly one line with the direction description

Example:
```
runs/<run_id>/tasks/task_001/direction.txt: Greedy approach making locally optimal choices
runs/<run_id>/tasks/task_002/direction.txt: Dynamic programming with memoization
runs/<run_id>/tasks/task_003/direction.txt: Brute-force try all possibilities
runs/<run_id>/tasks/task_004/direction.txt: Mathematical formula-based direct calculation
```

---

## CRITICAL RULES

1. **Direction Minimalism**: Each direction.txt must contain exactly one line, 8-25 words.
2. **English Only**: Directions must be written in English.
3. **Minimal File I/O**: Only create the necessary directories and direction.txt files.
4. **No Verifier Reading**: Do NOT read verifier scripts.
5. **Problem-First Diversity**: Derive diversity axes from the problem's structure, not from generic coding style categories.
6. **Pairwise Distinct**: Every direction must produce a fundamentally different solution; overlap in core strategy is unacceptable.
7. **Reference-Aware Roots**: When an `initial_program_reference` exists, directions should be informed by it but may include both close improvements and substantially different alternatives.
