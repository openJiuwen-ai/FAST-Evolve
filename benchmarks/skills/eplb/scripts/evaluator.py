import argparse
import functools
import importlib.util
import json
import os
import time
import traceback
from pathlib import Path
from typing import TypedDict

import torch

# Workload data belongs to the benchmark root.  The environment override keeps
# the evaluator easy to exercise with a small fixture without changing the
# default benchmark contract.
BENCHMARK_DIR = Path(__file__).resolve().parents[1]
WORKLOAD_PATH = os.environ.get("ADRS_EPLB_WORKLOAD", str(BENCHMARK_DIR / "expert-load.json"))
REBALANCE_INTERVAL = 100

NUM_REPLICAS = 288
NUM_GROUPS = 8
NUM_GPUS = 32
NUM_NODES = 4

@functools.cache
def load_workloads(path: str) -> list[torch.Tensor]: 
    with open(path) as f:
        data = json.load(f)

    total_len = len(data['load_history'])
    workloads = []
    for i in range(0, total_len, REBALANCE_INTERVAL):
        start = i
        end = min(start + REBALANCE_INTERVAL, total_len)

        load = torch.tensor([x['logical_expert_load'] for x in data['load_history'][start:end]]).sum(dim=0)
        workloads.append(load)

    return workloads

class EvaluationResult(TypedDict, total=False):
    balancedness_score_gpu: float
    balancedness_score_expert: float
    times_algorithm: float
    times_inference: float
    speed_score: float
    combined_score: float
    error: str

def simulate_inference(
        log2phy: torch.Tensor,
        logcnt: torch.Tensor,
        workload: torch.Tensor,
    ) -> tuple[float, float]:
    '''
    Simulate a MoE inference with the given expert mapping, and return the balancedness factor.
    '''
    # workload 形状: (num_layers, num_logical_experts) - 每层每个逻辑专家的负载
    num_layers, num_logical_experts = workload.shape
    
    # 初始化物理专家负载累积器
    num_physical_experts = NUM_REPLICAS
    total_physical_load = torch.zeros(num_layers, num_physical_experts, dtype=torch.float, device=workload.device)
    
    # 对每个逻辑专家，分配负载到其物理副本
    for layer_id in range(num_layers):
        for logical_id in range(num_logical_experts):
            # 获取该逻辑专家的负载
            logical_load = workload[layer_id][logical_id].item()
            
            # 跳过零负载
            if logical_load <= 0:
                continue
                
            num_replicas = int(logcnt[layer_id][logical_id].item())

            # 跳过零副本
            if num_replicas <= 0:
                continue

            # 获取物理专家映射
            physical_ids = log2phy[layer_id][logical_id][:num_replicas]
                
            # 计算每个副本的负载（基于有效副本数量）
            replica_load = logical_load / num_replicas
            
            # 分配负载到有效的物理专家
            total_physical_load[layer_id, physical_ids] += replica_load
    
    # 计算 balancedness
    total_load = total_physical_load.sum()
    if total_load == 0:
        return 0.0, 0.0
    
    # Compute expert load
    expert_layer_avg = total_physical_load.mean(dim=1).sum().item()
    expert_layer_max = total_physical_load.max(dim=1).values.sum().item()
    balancedness_expert = expert_layer_avg / expert_layer_max if expert_layer_max > 0 else 0.0

    # 计算 GPU 负载
    gpu_load = total_physical_load.view(num_layers, NUM_GPUS, -1).sum(dim=2)
    
    # 计算每层的平均负载和最大负载，然后求和
    layer_avg = gpu_load.mean(dim=1)  # (num_layers,)
    layer_max = gpu_load.max(dim=1).values  # (num_layers,)
    
    avg_load = layer_avg.sum().item()
    max_load = layer_max.sum().item()
    
    # 计算 balancedness: avg_load / max_load
    balancedness_gpu = avg_load / max_load if max_load > 0 else 0.0
    
    # print(f'balancedness per GPU: {balancedness}, balancedness per expert: {balancedness_expert}')
    
    return balancedness_gpu, balancedness_expert

def evaluate(program_path: str) -> EvaluationResult:
    try:
        if not os.path.exists(WORKLOAD_PATH):
            raise FileNotFoundError(
                f"EPLB workload not found: {WORKLOAD_PATH}. "
                "Download expert-load.json into the benchmark directory."
            )
        workloads = load_workloads(WORKLOAD_PATH)
        spec = importlib.util.spec_from_file_location("program", program_path)
        assert spec is not None
        program = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(program)

        if not hasattr(program, "rebalance_experts"):
            print('Error: program does not have `rebalance_experts` function')
            return {
                "balancedness_score_gpu": 0.0,
                "balancedness_score_expert": 0.0,
                "times_algorithm": 0.0,
                "times_inference": 0.0,
                "speed_score": 0.0,
                "combined_score": 0.0,
                "error": "Missing `rebalance_experts` function",
            }

        balancedness_scores_gpu = []
        balancedness_scores_expert = []
        times_algorithm = []
        times_inference = []
        for i in range(len(workloads) - 1):
            start_time = time.perf_counter()
            phy2log, log2phy, logcnt = program.rebalance_experts(
                workloads[i],
                NUM_REPLICAS,
                NUM_GROUPS,
                NUM_NODES,
                NUM_GPUS,
            )
            end_time_algorithm = time.perf_counter()

            # Validate outputs to prevent reward hacking
            if phy2log.shape[1] != NUM_REPLICAS:
                return {
                    "balancedness_score_gpu": 0.0,
                    "balancedness_score_expert": 0.0,
                    "times_algorithm": 0.0,
                    "times_inference": 0.0,
                    "speed_score": 0.0,
                    "combined_score": 0.0,
                    "error": f"phy2log shape wrong: {tuple(phy2log.shape)}",
                }

            if not torch.all(logcnt.sum(dim=1) == NUM_REPLICAS):
                sums = logcnt.sum(dim=1)
                return {
                    "balancedness_score_gpu": 0.0,
                    "balancedness_score_expert": 0.0,
                    "times_algorithm": 0.0,
                    "times_inference": 0.0,
                    "speed_score": 0.0,
                    "combined_score": 0.0,
                    "error": f"logcnt sums != {NUM_REPLICAS}: {sums[:5].tolist()}...",
                }

            if (logcnt < 0).any():
                return {
                    "balancedness_score_gpu": 0.0,
                    "balancedness_score_expert": 0.0,
                    "times_algorithm": 0.0,
                    "times_inference": 0.0,
                    "speed_score": 0.0,
                    "combined_score": 0.0,
                    "error": "logcnt contains negative values",
                }

            next_workload = workloads[i + 1]
            has_load = next_workload > 0
            has_no_replicas = logcnt == 0
            unhandled = has_load & has_no_replicas
            if unhandled.any():
                unhandled_count = int(unhandled.sum().item())
                return {
                    "balancedness_score_gpu": 0.0,
                    "balancedness_score_expert": 0.0,
                    "times_algorithm": 0.0,
                    "times_inference": 0.0,
                    "speed_score": 0.0,
                    "combined_score": 0.0,
                    "error": f"Unhandled load: {unhandled_count} experts have load but 0 replicas",
                }

            balancedness_score_gpu, balancedness_score_expert = simulate_inference(log2phy, logcnt, workloads[i + 1])
            end_time = time.perf_counter()
            balancedness_scores_gpu.append(balancedness_score_gpu)
            balancedness_scores_expert.append(balancedness_score_expert)
            print(f'time_algorithm: {end_time_algorithm - start_time}, time_inference: {end_time - start_time}')
            times_algorithm.append(end_time_algorithm - start_time)
            times_inference.append(end_time - start_time)
            
        avg_balancedness_score_gpu = sum(balancedness_scores_gpu) / len(balancedness_scores_gpu)
        avg_balancedness_score_expert = sum(balancedness_scores_expert) / len(balancedness_scores_expert)
        avg_time_algorithm = sum(times_algorithm) / len(times_algorithm)
        avg_time_inference = sum(times_inference) / len(times_inference)
        speed_score = 0.002 / avg_time_inference
        print(f'avg_time_algorithm: {avg_time_algorithm}, avg_time_inference: {avg_time_inference}, speed_score: {speed_score}')
        combined_score = (avg_balancedness_score_expert + speed_score) / 2
        return {
            "balancedness_score_gpu": float(avg_balancedness_score_gpu),
            "balancedness_score_expert": float(avg_balancedness_score_expert),
            "times_algorithm": float(avg_time_algorithm),
            "times_inference": float(avg_time_inference),
            "speed_score": float(speed_score),
            "combined_score": float(combined_score),
        }
    except Exception as e:
        traceback.print_exc()
        print(f'Error during evaluation: {str(e)}')
        return {
            "balancedness_score_gpu": 0.0,
            "balancedness_score_expert": 0.0,
            "times_algorithm": 0.0,
            "times_inference": 0.0,
            "speed_score": 0.0,
            "combined_score": 0.0,
            "error": str(e),
        }


def is_successful(result):
    return not result.get("error") and not result.get("traceback")


def format_result(result, artifact_ref):
    metric_keys = [
        "balancedness_score_gpu", "balancedness_score_expert", "times_algorithm",
        "times_inference", "speed_score", "combined_score",
    ]
    metrics = {key: result[key] for key in metric_keys if key in result and key != "combined_score"}
    profile = {key: result[key] for key in ("times_algorithm", "times_inference") if key in result}
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
    parser = argparse.ArgumentParser(description="EPLB benchmark evaluator")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--timeout", type=int, default=360)
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
