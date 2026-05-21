#!/usr/bin/env python
"""Standalone evaluator for packing 11 unit hexagons in a regular hexagon."""

import argparse
import json
import math
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict

import numpy as np


N_HEX = 11
BENCHMARK = 1 / 3.930092
TOL = 1e-6


def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    angle_radians = math.radians(angle_degrees)
    return [
        (
            center_x + side_length * math.cos(angle_radians + 2 * math.pi * i / 6),
            center_y + side_length * math.sin(angle_radians + 2 * math.pi * i / 6),
        )
        for i in range(6)
    ]


def normalize_vector(v):
    magnitude = math.sqrt(v[0] ** 2 + v[1] ** 2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude != 0 else (0.0, 0.0)


def get_normals(vertices):
    normals = []
    for i in range(len(vertices)):
        p1, p2 = vertices[i], vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normals.append(normalize_vector((-edge[1], edge[0])))
    return normals


def project_polygon(vertices, axis):
    projections = [vertex[0] * axis[0] + vertex[1] * axis[1] for vertex in vertices]
    return min(projections), max(projections)


def polygons_intersect(vertices1, vertices2, tol=TOL):
    for axis in get_normals(vertices1) + get_normals(vertices2):
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if max1 < min2 - tol or max2 < min1 - tol:
            return False
    return True


def hexagons_are_disjoint(hex1_params, hex2_params, tol=TOL):
    return not polygons_intersect(hexagon_vertices(*hex1_params), hexagon_vertices(*hex2_params), tol)


def is_inside_hexagon(point, hex_params, tol=TOL):
    vertices = hexagon_vertices(*hex_params)
    for i in range(len(vertices)):
        p1, p2 = vertices[i], vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        rel = (point[0] - p1[0], point[1] - p1[1])
        if edge[0] * rel[1] - edge[1] * rel[0] < -tol:
            return False
    return True


def validate_hexagons(inner_hex_data, outer_hex_data, outer_hex_side_length):
    if not isinstance(inner_hex_data, np.ndarray):
        inner_hex_data = np.array(inner_hex_data)
    if not isinstance(outer_hex_data, np.ndarray):
        outer_hex_data = np.array(outer_hex_data)
    if inner_hex_data.shape != (N_HEX, 3) or outer_hex_data.shape != (3,):
        return False, "invalid_shape"
    if not np.isfinite(inner_hex_data).all() or not np.isfinite(outer_hex_data).all():
        return False, "nan_or_inf"
    try:
        outer_side = float(outer_hex_side_length)
    except (TypeError, ValueError):
        return False, "invalid_outer_side"
    if not math.isfinite(outer_side) or outer_side <= 0:
        return False, "invalid_outer_side"

    inner_params = [(float(x), float(y), 1.0, float(angle)) for x, y, angle in inner_hex_data]
    outer_params = (float(outer_hex_data[0]), float(outer_hex_data[1]), outer_side, float(outer_hex_data[2]))
    for i in range(len(inner_params)):
        for j in range(i + 1, len(inner_params)):
            if not hexagons_are_disjoint(inner_params[i], inner_params[j], TOL):
                return False, f"hexagons_{i + 1}_{j + 1}_intersect"
    for inner in inner_params:
        for vertex in hexagon_vertices(*inner):
            if not is_inside_hexagon(vertex, outer_params, TOL):
                return False, "containment_violation"
    return True, ""


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    program_path_escaped = program_path.replace("\\", "/")
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace("\\", "/")
        script = f"""
import os
import pickle
import sys
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))
try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)
    inner_hex_data, outer_hex_data, outer_hex_side_length = program.hexagon_packing_11()
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'inner_hex_data': inner_hex_data, 'outer_hex_data': outer_hex_data, 'outer_hex_side_length': outer_hex_side_length}}, f)
except Exception as e:
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f)
"""
        temp_file.write(script.encode())
        temp_file_path = temp_file.name
    results_path = f"{temp_file_path}.results"
    try:
        process = subprocess.Popen([sys.executable, temp_file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.communicate(timeout=timeout_seconds)
            if not os.path.exists(results_path):
                raise RuntimeError("Results file not found")
            with open(results_path, "rb") as handle:
                results = pickle.load(handle)
            if "error" in results:
                raise RuntimeError(results["error"])
            return results["inner_hex_data"], results["outer_hex_data"], results["outer_hex_side_length"]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for path in (temp_file_path, results_path):
            if os.path.exists(path):
                os.unlink(path)


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    start_time = time.time()
    try:
        inner_hex_data, outer_hex_data, outer_hex_side_length = run_program_with_timeout(program_path, timeout_seconds)
        eval_time = time.time() - start_time
        valid, reason = validate_hexagons(inner_hex_data, outer_hex_data, outer_hex_side_length)
        value = float(1 / outer_hex_side_length) if valid else 0.0
        target_ratio = value / BENCHMARK if valid else 0.0
        result = {"inv_outer_hex_side_length": value, "target_ratio": float(target_ratio), "validity": 1.0 if valid else 0.0, "eval_time": float(eval_time), "combined_score": float(target_ratio if valid else 0.0)}
        if not valid:
            result["validation_error"] = reason
        return result
    except Exception as exc:
        return {"inv_outer_hex_side_length": 0.0, "target_ratio": 0.0, "validity": 0.0, "eval_time": float(time.time() - start_time), "combined_score": 0.0, "error": str(exc), "traceback": traceback.format_exc()}


def is_successful(result: Dict[str, Any]) -> bool:
    return not result.get("traceback") and not result.get("error") and result.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metric_keys = ["inv_outer_hex_side_length", "target_ratio", "validity", "eval_time"]
    exclude_keys = {"combined_score", *metric_keys}
    return {"status": "success" if is_successful(result) else "failure", "artifact_ref": artifact_ref, "fitness": result.get("combined_score", 0.0), "metrics": {key: result[key] for key in metric_keys if key in result}, "misc": {key: result[key] for key in result if key not in exclude_keys}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Hexagon Packing 11 Evaluator")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", help="Output JSON file path")
    args = parser.parse_args()
    if not os.path.exists(args.artifact_ref):
        print(f"Program file not found: {args.artifact_ref}")
        sys.exit(1)
    result = evaluate(args.artifact_ref, timeout_seconds=args.timeout)
    result.update({"run_id": args.run_id, "task_id": args.task_id, "subagent_id": args.subagent_id, "mode": args.mode, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(format_result(result, args.artifact_ref), handle, indent=2, ensure_ascii=False)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    print(result.get("traceback") or result.get("error") or f"Validity check failed: {result.get('validation_error')}")
    sys.exit(1)


if __name__ == "__main__":
    main()
