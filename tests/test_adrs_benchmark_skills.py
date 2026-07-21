from pathlib import Path


ADRS_BENCHMARKS = [
    "cloudcast",
    "eplb",
    "llm_sql",
    "prism",
    "txn_scheduling",
]

SOLVER_PROMPT_MARKERS = {
    "cloudcast": [
        "parallel paths and overlapping transfers",
        "BroadCastTopology",
        "reduce redundant transfers",
    ],
    "eplb": [
        "load metrics recorded by the vLLM server",
        "make replicas of some experts",
        "perfect load balancing is NP-hard",
    ],
    "llm_sql": [
        "maximize prefix hit count (PHC)",
        "sum of squares of the string lengths",
        "combined_score = 0.95 * average_hit_rate",
        "distinct_value_threshold: float = 0.8",
        "prefix-aware greedy approaches",
    ],
    "prism": [
        "maximum KVPR across all GPUs",
        "model.req_rate / model.slo",
        "GPU_MEM_SIZE",
    ],
    "txn_scheduling": [
        "EVOLVE-BLOCK-START",
        "Read-write and write-write conflicts",
        "increases makespan the least",
        "transaction length or number of writes",
    ],
}


def test_adrs_benchmarks_follow_skill_workflow_layout():
    repo_root = Path(__file__).resolve().parents[1]
    skills_root = repo_root / "benchmarks" / "skills"

    for name in ADRS_BENCHMARKS:
        benchmark_dir = skills_root / name
        skill_md = benchmark_dir / "SKILL.md"

        assert skill_md.is_file(), name
        assert (benchmark_dir / "initial_program.py").is_file(), name
        assert (benchmark_dir / "scripts" / "evaluator.py").is_file(), name

        frontmatter = skill_md.read_text(encoding="utf-8").split("---", 2)
        assert len(frontmatter) == 3, name
        assert f"name: {name}" in frontmatter[1], name
        assert "--run-id" in skill_md.read_text(encoding="utf-8"), name
        assert "--artifact-ref" in skill_md.read_text(encoding="utf-8"), name
        assert "--out" in skill_md.read_text(encoding="utf-8"), name


def test_adrs_skills_preserve_solver_facing_prompt_guidance():
    repo_root = Path(__file__).resolve().parents[1]
    skills_root = repo_root / "benchmarks" / "skills"

    for name, markers in SOLVER_PROMPT_MARKERS.items():
        content = (skills_root / name / "SKILL.md").read_text(encoding="utf-8")
        normalized_content = " ".join(content.split())
        for marker in markers:
            assert marker in normalized_content, f"{name}: missing solver prompt marker {marker!r}"


def test_adrs_python_sources_compile():
    repo_root = Path(__file__).resolve().parents[1]
    skills_root = repo_root / "benchmarks" / "skills"

    for name in ADRS_BENCHMARKS:
        benchmark_dir = skills_root / name
        python_files = [
            path
            for path in benchmark_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        ]
        for path in python_files:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
