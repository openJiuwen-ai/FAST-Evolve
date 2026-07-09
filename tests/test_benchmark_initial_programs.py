from pathlib import Path


SKYDISCOVER_MATH_SKILLS = [
    "circle_packing",
    "circle_packing_rect",
    "erdos_min_overlap",
    "first_autocorr_ineq",
    "heilbronn_convex_13",
    "heilbronn_convex_14",
    "heilbronn_triangle",
    "hexagon_packing_11",
    "hexagon_packing_12",
    "matmul",
    "minimizing_max_min_dist_2",
    "minimizing_max_min_dist_3",
    "second_autocorr_ineq",
    "signal_processing",
    "sums_diffs_finite_sets",
    "third_autocorr_ineq",
    "uncertainty_ineq",
]


def test_skydiscover_math_skills_include_initial_programs():
    repo_root = Path(__file__).resolve().parents[1]
    skills_root = repo_root / "benchmarks" / "skills"

    missing = [
        skill
        for skill in SKYDISCOVER_MATH_SKILLS
        if not (skills_root / skill / "initial_program.py").is_file()
    ]

    assert missing == []
