---
name: init-explore
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
2. Understand the verification method and what constitutes a successful solution.
3. Do not read verifier scripts (verify.py or similar) - only read the problem description SKILL.md.

---

## Step 2: Generate Directions

Generate exactly `num_directions` different directions. Each direction must be:
- One line, 10-50 words in English
- Clearly distinct from the other directions
- Feasible and meaningful for the given task

The directions should explore different approaches such as:
- Different architectural styles (functional, object-oriented, etc.)
- Different algorithmic approaches
- Using different standard libraries or minimal dependencies
- Focusing on readability vs performance vs simplicity
- Different data structures or design patterns

---

## Step 3: Save Directions

For each task, create the directory and save the direction file:

1. For task_id from 1 to num_directions:
   - Create directory: `runs/<run_id>/tasks/task_<task_id:03d>/`
   - Save file: `runs/<run_id>/tasks/task_<task_id:03d>/direction.txt`
   - File content: exactly one line with the direction description

Example:
```
runs/<run_id>/tasks/task_001/direction.txt: Simple implementation using pure mathematical calculations
runs/<run_id>/tasks/task_002/direction.txt: Object-oriented design with class structure
runs/<run_id>/tasks/task_003/direction.txt: Implementation leveraging Python standard libraries
runs/<run_id>/tasks/task_004/direction.txt: Code focused on readability and maintainability
```

---

## CRITICAL RULES

1. **Direction Minimalism**: Each direction.txt must contain exactly one line, 10-50 words.
2. **English Only**: Directions must be written in English.
3. **Minimal File I/O**: Only create the necessary directories and direction.txt files.
4. **No Verifier Reading**: Do NOT read verifier scripts.
