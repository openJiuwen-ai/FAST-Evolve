#!/usr/bin/env python
"""
Hexagon Packing Evaluator (11 unit hexagons)

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict
import numpy as np
import math

N_HEX = 11
BENCHMARK = 1 / 3.930092
TOL = 1e-6


def validate_hexagon(inner_hex_data, outer_hex_data, outer_hex_side_length) -> bool:
    if np.isnan(inner_hex_data).any() if isinstance(inner_hex_data, np.ndarray) else any(np.isnan(x) for x in inner_hex_data):
        return False
    if np.isnan(outer_hex_data).any() if isinstance(outer_hex_data, np.ndarray) else any(np.isnan(x) for x in outer_hex_data):
        return False
    if np.isnan(outer_hex_side_length):
        return False
    if not isinstance(inner_hex_data, np.ndarray):
        inner_hex_data = np.array(inner_hex_data)
    if not isinstance(outer_hex_data, np.ndarray):
        outer_hex_data = np.array(outer_hex_data)
    if inner_hex_data.shape != (N_HEX, 3):
        return False
    if outer_hex_data.shape != (3,):
        return False
    try:
        outer_hex_center = outer_hex_data[:2]
        outer_hex_angle_degrees = outer_hex_data[-1]
        inner_hex_params_list = [(x, y, 1, angle) for x, y, angle in inner_hex_data]
        outer_hex_params = (outer_hex_center[0], outer_hex_center[1], outer_hex_side_length, outer_hex_angle_degrees)
        for i in range(len(inner_hex_params_list)):
            for j in range(i + 1, len(inner_hex_params_list)):
                if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j], TOL):
                    return False
        if not all_hexagons_contained(inner_hex_params_list, outer_hex_params, TOL):
            return False
    except Exception:
        return False
    return True


def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices


def normalize_vector(v):
    magnitude = math.sqrt(v[0] ** 2 + v[1] ** 2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude != 0 else (0.0, 0.0)


def get_normals(vertices):
    normals = []
    for i in range(len(vertices)):
        p1, p2 = vertices[i], vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals


def project_polygon(vertices, axis):
    min_proj, max_proj = float("inf"), float("-inf")
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj


def overlap_1d(min1, max1, min2, max2, tol=TOL):
    return max1 >= min2 - tol and max2 >= min1 - tol


def polygons_intersect(vertices1, vertices2, tol=TOL):
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2
    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2, tol):
            return False
    return True


def hexagons_are_disjoint(hex1_params, hex2_params, tol=TOL):
    return not polygons_intersect(hexagon_vertices(*hex1_params), hexagon_vertices(*hex2_params), tol)


def is_inside_hexagon(point, hex_params, tol=TOL):
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1, p2 = hex_vertices[i], hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0]
        if cross_product < -tol:
            return False
    return True


def all_hexagons_contained(inner_hex_params_list, outer_hex_params, tol=TOL):
    for inner_hex_params in inner_hex_params_list:
        for vertex in hexagon_vertices(*inner_hex_params):
            if not is_inside_hexagon(vertex, outer_hex_params, tol):
                return False
    return True


def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees, tol=TOL):
    inner_hex_params_list = [(x, y, 1, angle) for x, y, angle in inner_hex_data]
    outer_hex_params = (outer_hex_center[0], outer_hex_center[1], outer_hex_side_length, outer_hex_angle_degrees)
    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j], tol):
                raise AssertionError(f"Hexagons {i+1} and {j+1} intersect!")
    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params, tol):
        raise AssertionError("Not all inner hexagons are contained in the outer hexagon!")


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    program_path_escaped = program_path.replace('\\', '/')
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace('\\', '/')
        script = f"""
import sys, numpy as np, os, traceback
sys.path.insert(0, os.path.dirname('{program_path_escaped}'))
try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)
    inner_hex_data, outer_hex_data, outer_hex_side_length = program.hexagon_packing_11()
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'inner_hex_data': inner_hex_data, 'outer_hex_data': outer_hex_data, 'outer_hex_side_length': outer_hex_side_length}}, f)
except Exception as e:
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f)
"""
        temp_file.write(script.encode())
        temp_file_path = temp_file.name
    results_path = f"{temp_file_path}.results"
    try:
        process = subprocess.Popen([sys.executable, temp_file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            if os.path.exists(results_path):
                import pickle
                with open(results_path, "rb") as f:
                    results = pickle.load(f)
                if "error" in results:
                    raise RuntimeError(results["error"])
                return results["inner_hex_data"], results["outer_hex_data"], results["outer_hex_side_length"]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for p in [temp_file_path, results_path]:
            if os.path.exists(p):
                os.unlink(p)


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    start_time = time.time()
    try:
        inner_hex_data, outer_hex_data, outer_hex_side_length = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time
        if not isinstance(inner_hex_data, np.ndarray):
            inner_hex_data = np.array(inner_hex_data)
        if not isinstance(outer_hex_data, np.ndarray):
            outer_hex_data = np.array(outer_hex_data)

        valid = validate_hexagon(inner_hex_data, outer_hex_data, outer_hex_side_length)
        inv_outer_hex_side_length = 0.0
        if valid:
            inv_outer_hex_side_length = float(1 / outer_hex_side_length)

        target_ratio = inv_outer_hex_side_length / BENCHMARK if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "inv_outer_hex_side_length": inv_outer_hex_side_length,
            "target_ratio": float(target_ratio),
            "combined_score": float(combined_score),
            "eval_time": float(eval_time),
            "validity": float(validity),
        }
    except Exception as e:
        return {"inv_outer_hex_side_length": 0.0, "target_ratio": 0.0, "combined_score": 0.0, "eval_time": float(time.time() - start_time), "validity": 0.0, "error": str(e), "traceback": traceback.format_exc()}


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]
    exclude_keys = {"combined_score", "validity", "eval_time", "inv_outer_hex_side_length", "target_ratio"}
    return {
        "status": "success" if is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": metrics,
        "misc": {k: result[k] for k in result if k not in exclude_keys},
    }


def main():
    parser = argparse.ArgumentParser()
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
    with open(output_path, "w") as f:
        json.dump(format_result(result, args.artifact_ref), f, indent=2)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(result.get("traceback") or result.get("error") or "Unknown error")
        sys.exit(1)


if __name__ == "__main__":
    main()
