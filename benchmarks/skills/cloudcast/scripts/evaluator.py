import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

# The helper modules live beside this evaluator.
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from simulator import BCSimulator
from utils import make_nx_graph

BENCHMARK_DIR = Path(__file__).resolve().parents[1]


def _simulation_metrics(simulator, total_cost, max_transfer_time):
    """Return diagnostic cost and topology metrics without changing scoring."""
    egress_cost = 0.0
    unique_edge_cost = 0.0
    partition_edge_count = 0
    topology_edges = []

    for source, destination, edge_data in simulator.g.edges(data=True):
        partitions = sorted(str(partition) for partition in edge_data["partitions"])
        edge_cost = float(edge_data["cost"])
        partition_count = len(partitions)
        egress_cost += partition_count * simulator.partition_data_vol * edge_cost
        unique_edge_cost += edge_cost
        partition_edge_count += partition_count
        topology_edges.append(
            {
                "source": str(source),
                "destination": str(destination),
                "partitions": partitions,
            }
        )

    topology_edges.sort(
        key=lambda edge: (edge["source"], edge["destination"], edge["partitions"])
    )
    topology_hash = hashlib.sha256(
        json.dumps(topology_edges, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    instance_cost = float(total_cost) - egress_cost
    if abs(instance_cost) < 1e-12:
        instance_cost = 0.0

    return {
        "total_cost": float(total_cost),
        "egress_cost": egress_cost,
        "instance_cost": instance_cost,
        "max_transfer_time": float(max_transfer_time),
        "node_count": simulator.g.number_of_nodes(),
        "edge_count": simulator.g.number_of_edges(),
        "partition_edge_count": partition_edge_count,
        "unique_edge_cost": unique_edge_cost,
        "topology_hash": topology_hash,
    }


def _combined_topology_hash(per_config):
    """Return one stable fingerprint for all configuration topologies."""
    topology_hashes = {
        config_name: metrics["topology_hash"]
        for config_name, metrics in sorted(per_config.items())
    }
    return hashlib.sha256(
        json.dumps(topology_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_broadcast_topology(bc_t, source_node, terminal_nodes, num_partitions, G):
    """
    Validate that the broadcast topology is complete and correct.
    
    Returns:
        (is_valid, error_message) tuple
    """
    # Check 1: Verify all destinations are present
    if set(bc_t.dsts) != set(terminal_nodes):
        missing_dsts = set(terminal_nodes) - set(bc_t.dsts)
        extra_dsts = set(bc_t.dsts) - set(terminal_nodes)
        return False, f"Destination mismatch: missing={missing_dsts}, extra={extra_dsts}"
    
    # Check 2: Verify source matches
    if bc_t.src != source_node:
        return False, f"Source mismatch: expected={source_node}, got={bc_t.src}"
    
    # Check 3: Verify all partitions exist for all destinations
    missing_partitions = []
    empty_partitions = []
    invalid_paths = []
    
    for dst in terminal_nodes:
        if dst not in bc_t.paths:
            return False, f"Missing destination '{dst}' in paths"
        
        for partition_id in range(num_partitions):
            partition_key = str(partition_id)
            
            # Check if partition exists
            if partition_key not in bc_t.paths[dst]:
                missing_partitions.append((dst, partition_id))
                continue
            
            partition_paths = bc_t.paths[dst][partition_key]
            
            # Check if partition paths are None or empty
            if partition_paths is None or len(partition_paths) == 0:
                empty_partitions.append((dst, partition_id))
                continue
            
            # Check 4: Verify paths form valid routes from source to destination
            # Build a path from edges
            path_nodes = [source_node]
            path_valid = True
            
            for edge in partition_paths:
                if len(edge) < 3:
                    invalid_paths.append((dst, partition_id, "edge format invalid"))
                    path_valid = False
                    break
                
                edge_src, edge_dst, _edge_data = edge[0], edge[1], edge[2]
                
                # Verify edge exists in graph
                if not G.has_edge(edge_src, edge_dst):
                    invalid_paths.append((dst, partition_id, f"edge {edge_src}->{edge_dst} not in graph"))
                    path_valid = False
                    break
                
                # Verify path continuity
                if path_nodes[-1] != edge_src:
                    invalid_paths.append((dst, partition_id, f"path discontinuity: expected {path_nodes[-1]}, got {edge_src}"))
                    path_valid = False
                    break
                
                path_nodes.append(edge_dst)
            
            # Check if path reaches destination (only if path was valid so far)
            if path_valid and path_nodes[-1] != dst:
                invalid_paths.append((dst, partition_id, f"path does not reach destination: ends at {path_nodes[-1]}, expected {dst}"))
    
    # Compile validation errors
    errors = []
    if missing_partitions:
        errors.append(f"Missing partitions: {missing_partitions}")
    if empty_partitions:
        errors.append(f"Empty partitions: {empty_partitions}")
    if invalid_paths:
        errors.append(f"Invalid paths: {invalid_paths}")
    
    if errors:
        return False, "Validation failed: " + "; ".join(errors)
    
    # Check 5: Verify all data volumes are accounted for
    # Count total partitions that should be transferred
    expected_total_partitions = len(terminal_nodes) * num_partitions
    
    # Count partitions actually present
    actual_partitions = 0
    for dst in terminal_nodes:
        for partition_id in range(num_partitions):
            partition_key = str(partition_id)
            if (partition_key in bc_t.paths[dst] and 
                bc_t.paths[dst][partition_key] is not None and 
                len(bc_t.paths[dst][partition_key]) > 0):
                actual_partitions += 1
    
    if actual_partitions != expected_total_partitions:
        return False, f"Data loss detected: expected {expected_total_partitions} partitions, got {actual_partitions}"
    
    return True, None


def evaluate(program_path):
    """
    Evaluate the evolved broadcast optimization program across multiple configurations.
    
    Args:
        program_path: Path to the evolved program file
        
    Returns:
        Dictionary with evaluation metrics including required 'combined_score'
    """
    try:
        # Load the evolved program
        spec = importlib.util.spec_from_file_location("program", program_path)
        program = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(program)
        
        # Check if the required function exists
        if not hasattr(program, "search_algorithm"):
            return {
                "combined_score": 0.0,
                "runs_successfully": 0.0,
                "error": "Missing search_algorithm function"
            }
        
        # Configuration - individual JSON file paths (relative to evaluator location)
        evaluator_dir = BENCHMARK_DIR
        config_files = [
            os.path.join(evaluator_dir, "examples/config/intra_aws.json"),
            os.path.join(evaluator_dir, "examples/config/intra_azure.json"),
            os.path.join(evaluator_dir, "examples/config/intra_gcp.json"),
            os.path.join(evaluator_dir, "examples/config/inter_agz.json"),
            os.path.join(evaluator_dir, "examples/config/inter_gaz2.json"),
        ]
        
        # Filter to only include files that exist
        existing_configs = [f for f in config_files if os.path.exists(f)]
        
        if not existing_configs:
            return {
                "combined_score": 0.0,
                "runs_successfully": 0.0,
                "error": f"No configuration files found. Checked: {config_files}"
            }
        
        num_vms = 2
        total_cost = 0.0
        total_egress_cost = 0.0
        total_instance_cost = 0.0
        per_config = {}
        successful_configs = 0
        failed_configs = 0
        
        # Keep generated path/evaluation files isolated from the repository
        # and from concurrent task-executor processes.
        with TemporaryDirectory(prefix="cloudcast-eval-") as work_dir:
            work_dir = Path(work_dir)

            # Process each configuration file
            for jsonfile in existing_configs:
                try:
                    print(f"Processing config: {os.path.basename(jsonfile)}")

                    # Load configuration.
                    with open(jsonfile, encoding="utf-8") as f:
                        config_name = os.path.basename(jsonfile).split(".")[0]
                        config = json.loads(f.read())

                    # Create graph.
                    G = make_nx_graph(num_vms=int(num_vms))

                    # Source and destination nodes.
                    source_node = config["source_node"]
                    terminal_nodes = config["dest_nodes"]

                    # Create output directories in the isolated workspace.
                    directory = work_dir / "paths" / config_name
                    directory.mkdir(parents=True, exist_ok=True)

                    # Run the evolved algorithm.
                    num_partitions = config["num_partitions"]
                    bc_t = program.search_algorithm(source_node, terminal_nodes, G, num_partitions)

                    bc_t.set_num_partitions(config["num_partitions"])

                    # Validate the broadcast topology before evaluation.
                    is_valid, validation_error = validate_broadcast_topology(
                        bc_t, source_node, terminal_nodes, num_partitions, G
                    )

                    if not is_valid:
                        print(f"Validation failed for {config_name}: {validation_error}")
                        return {
                            "combined_score": 0.0,
                            "runs_successfully": 0.0,
                            "error": f"Invalid broadcast topology: {validation_error}",
                        }

                    # Save the generated paths.
                    outf = directory / "search_algorithm.json"
                    with outf.open("w", encoding="utf-8") as outfile:
                        outfile.write(
                            json.dumps(
                                {
                                    "algo": "search_algorithm",
                                    "source_node": bc_t.src,
                                    "terminal_nodes": bc_t.dsts,
                                    "num_partitions": bc_t.num_partitions,
                                    "generated_path": bc_t.paths,
                                }
                            )
                        )

                    # Evaluate the generated paths.
                    output_dir = work_dir / "evals" / config_name
                    output_dir.mkdir(parents=True, exist_ok=True)

                    # Run simulation.
                    simulator = BCSimulator(int(num_vms), str(output_dir))
                    # The legacy simulator prints every generated path edge;
                    # keep that diagnostic out of the task-executor context.
                    with redirect_stdout(io.StringIO()):
                        max_transfer_time, cost = simulator.evaluate_path(str(outf), config)

                    config_metrics = _simulation_metrics(
                        simulator,
                        total_cost=cost,
                        max_transfer_time=max_transfer_time,
                    )

                    # Accumulate results.
                    total_cost += cost
                    total_egress_cost += config_metrics["egress_cost"]
                    total_instance_cost += config_metrics["instance_cost"]
                    per_config[config_name] = config_metrics
                    successful_configs += 1

                    print(f"Config {config_name}: cost={cost:.2f}")

                except Exception as e:
                    print(f"Failed to process {os.path.basename(jsonfile)}: {str(e)}")
                    failed_configs += 1
                    break
        
        # Check if we have any successful evaluations
        if failed_configs != 0:
            return {
                "combined_score": 0.0,
                "runs_successfully": 0.0,
                "error": "1 or more configuration files failed to process"
            }
        
        # Calculate aggregate metrics
        avg_cost = total_cost / successful_configs
        success_rate = successful_configs / (successful_configs + failed_configs)
        
        print(f"Summary: {successful_configs} successful, {failed_configs} failed")
        print(f"Total cost: {total_cost:.2f}")
        
        # Calculate metrics for SkyDiscover
        # Normalize scores (higher is better)
        cost_score = 1.0 / (1.0 + total_cost)  # Lower cost = higher score
        
        # Combined score considering total cost, and success rate
        combined_score = cost_score
        
        return {
            "combined_score": combined_score,  # Required by SkyDiscover
            "runs_successfully": success_rate,
            "total_cost": total_cost,
            "avg_cost": avg_cost,
            "successful_configs": successful_configs,
            "failed_configs": failed_configs,
            "cost_score": cost_score,
            "success_rate": success_rate,
            "egress_cost": total_egress_cost,
            "instance_cost": total_instance_cost,
            "per_config": per_config,
            "topology_hash": _combined_topology_hash(per_config),
        }

    except Exception as e:
        print(f"Evaluation failed: {str(e)}")
        print(traceback.format_exc())
        return {
            "combined_score": 0.0,  # Required by SkyDiscover
            "runs_successfully": 0.0,
            "error": str(e)
        }


def is_successful(result):
    return not result.get("error") and not result.get("traceback")


def format_result(result, artifact_ref):
    metric_keys = [
        "validity", "eval_time", "combined_score", "runs_successfully",
        "total_cost", "avg_cost", "successful_configs", "failed_configs",
        "cost_score", "success_rate", "egress_cost", "instance_cost",
        "per_config", "topology_hash",
    ]
    metrics = {key: result[key] for key in metric_keys if key in result and key != "combined_score"}
    profile = {key: result[key] for key in ("validity", "eval_time") if key in result}
    misc = {key: value for key, value in result.items() if key not in {*metric_keys, "run_id", "task_id", "subagent_id", "mode", "timestamp"}}
    return {
        "status": "success" if is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": float(result.get("combined_score", 0.0)),
        "metrics": metrics,
        "profile": profile,
        "misc": misc,
    }


def main():
    parser = argparse.ArgumentParser(description="Cloudcast benchmark evaluator")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out")
    args = parser.parse_args()

    if not os.path.exists(args.artifact_ref):
        print(f"Program file not found: {args.artifact_ref}")
        raise SystemExit(1)

    result = evaluate(args.artifact_ref)
    result.update({"run_id": args.run_id, "task_id": args.task_id, "subagent_id": args.subagent_id, "mode": args.mode, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(format_result(result, args.artifact_ref), output_file, indent=2, ensure_ascii=False, default=str)

    if is_successful(result):
        print(output_path)
        raise SystemExit(0)
    print(result.get("traceback") or result.get("error") or "Unknown evaluation failure")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
