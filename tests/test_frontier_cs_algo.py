import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "benchmarks" / "skills" / "frontier_cs_algo"
MANIFEST_PATH = SKILL_DIR / "suite_manifest.json"
EVALUATOR_PATH = SKILL_DIR / "scripts" / "evaluator.py"


def _load_evaluator():
    spec = spec_from_file_location("frontier_cs_algo_evaluator", EVALUATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVALUATOR = _load_evaluator()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frontier_cs_algo_manifest_contains_172_coral_problem_packs():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    problems = manifest["problems"]
    ids = [problem["problem_id"] for problem in problems]

    assert manifest["benchmark"] == "frontier_cs_algo"
    assert manifest["track"] == "algorithmic"
    assert manifest["problem_count"] == 172
    assert manifest["source"]["revision"] == "7d6df442f86d2e1bd53bd5f12c7b381d6b7b7430"
    assert manifest["judge_reference"]["revision"] == "c7ec558160646dbe797828ccd1a4fded503ab87c"
    assert len(ids) == len(set(ids)) == 172
    assert ids == sorted(ids, key=int)
    assert "0" in ids
    assert "258" in ids
    assert "12" not in ids

    type_counts = {"default": 0, "interactive": 0}
    for problem in problems:
        problem_dir = SKILL_DIR / "problems" / problem["problem_id"]
        statement = problem_dir / "statement.txt"
        initial_program = problem_dir / "initial_program.cpp"
        metadata = json.loads((problem_dir / "metadata.json").read_text(encoding="utf-8"))

        assert statement.is_file()
        assert initial_program.is_file()
        assert metadata == problem
        assert _sha256(statement) == problem["statement_sha256"]
        assert _sha256(initial_program) == problem["initial_program_sha256"]
        type_counts[problem["problem_type"]] += 1

    assert type_counts == {"default": 98, "interactive": 74}


def test_normalize_problem_id_uses_manifest_not_a_contiguous_range():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert EVALUATOR.normalize_problem_id("000", manifest) == "0"
    assert EVALUATOR.normalize_problem_id("258", manifest) == "258"
    with pytest.raises(ValueError, match="not in"):
        EVALUATOR.normalize_problem_id("12", manifest)
    with pytest.raises(ValueError, match="numeric"):
        EVALUATOR.normalize_problem_id("task_001", manifest)


def test_evaluate_candidate_polls_and_uses_bounded_score(tmp_path):
    artifact = tmp_path / "solution.cpp"
    artifact.write_text("int main() { return 0; }\n", encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def submit(self, problem_id, code):
            assert problem_id == "0"
            assert "int main" in code
            return "submission-1"

        def get_result(self, submission_id):
            assert submission_id == "submission-1"
            self.calls += 1
            if self.calls == 1:
                return 404, {}
            return 200, {
                "status": "done",
                "score": 10.58,
                "scoreUnbounded": 47003.94,
                "passed": False,
                "result": "Wrong Answer",
                "cases": [
                    {"status": "Correct"},
                    {"status": "Wrong Answer"},
                    {"status": "Time Limit Exceeded"},
                ],
            }

    timestamps = iter([0.0, 0.1, 0.2, 0.3])
    sleeps = []
    result = EVALUATOR.evaluate_candidate(
        artifact,
        "0",
        client=FakeClient(),
        timeout_seconds=10,
        poll_interval=0.01,
        monotonic=lambda: next(timestamps),
        sleep=sleeps.append,
    )

    assert result["success"] is True
    assert result["fitness"] == 10.58
    assert result["score_bounded"] == 10.58
    assert "score_unbounded" not in result
    assert result["case_count"] == 3
    assert result["accepted_case_count"] == 1
    assert result["runtime_failure_count"] == 1
    assert sleeps == [0.01]


def test_judge_client_uses_official_submit_and_result_endpoints(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.command, self.path, self.headers["Content-Type"], body))
            payload = json.dumps({"sid": "submission-http"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            requests.append((self.command, self.path, "", b""))
            payload = json.dumps(
                {
                    "status": "done",
                    "score": 80.0,
                    "scoreUnbounded": 105.0,
                    "passed": False,
                    "result": "Wrong Answer",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifact = tmp_path / "solution.cpp"
    artifact.write_text("int main() { return 0; }\n", encoding="utf-8")
    try:
        result = EVALUATOR.evaluate_candidate(
            artifact,
            "0",
            client=EVALUATOR.JudgeClient(f"http://127.0.0.1:{server.server_port}"),
            timeout_seconds=10,
            poll_interval=0,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["fitness"] == 80.0
    assert requests[0][0:2] == ("POST", "/submit")
    assert requests[1][0:2] == ("GET", "/result/submission-http")
    assert "multipart/form-data; boundary=" in requests[0][2]
    assert b'name="pid"\r\n\r\n0\r\n' in requests[0][3]
    assert b'name="lang"\r\n\r\ncpp\r\n' in requests[0][3]
    assert b'filename="solution.cpp"' in requests[0][3]
    assert b"int main()" in requests[0][3]


def test_format_result_keeps_problem_profile_and_bounded_fitness():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    formatted = EVALUATOR.format_result(
        {
            "success": True,
            "fitness": 100.0,
            "score_bounded": 100.0,
            "eval_time": 1.25,
            "submission_id": "sid-1",
            "judge_status": "done",
        },
        "runs/run/tasks/task_001/attempts/attempt_001/solution.cpp",
        problem_id="0",
        judge_url="http://localhost:8081",
        run_id="run",
        task_id="task_001",
        manifest=manifest,
    )

    assert formatted["status"] == "success"
    assert formatted["fitness"] == 100.0
    assert formatted["metrics"] == {
        "score_bounded": 100.0,
        "eval_time": 1.25,
    }
    assert formatted["profile"] == {
        "benchmark": "frontier_cs_algo",
        "track": "algorithmic",
        "problem_id": "0",
        "suite_source_revision": "7d6df442f86d2e1bd53bd5f12c7b381d6b7b7430",
        "judge_reference_revision": "c7ec558160646dbe797828ccd1a4fded503ab87c",
        "judge_url": "http://localhost:8081",
    }
    assert formatted["misc"]["submission_id"] == "sid-1"


def test_default_evaluation_timeout_matches_frontier_cs_protocol():
    assert EVALUATOR.DEFAULT_EVALUATION_TIMEOUT == 300.0


def test_failure_result_has_null_fitness():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    formatted = EVALUATOR.format_result(
        {"success": False, "error": "judge unavailable"},
        "solution.cpp",
        problem_id="0",
        judge_url="http://localhost:8081",
        run_id="run",
        task_id="task_001",
        manifest=manifest,
    )

    assert formatted["status"] == "failure"
    assert formatted["fitness"] is None
    assert formatted["misc"]["error"] == "judge unavailable"


def test_shared_skill_and_executor_support_problem_specific_cpp_artifacts():
    benchmark_skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    init_explore = (
        REPO_ROOT / "agent_evolve_plugin" / "skills" / "init-explore" / "SKILL.md"
    ).read_text(encoding="utf-8")
    task_exec = (
        REPO_ROOT / "agent_evolve_plugin" / "skills" / "evolve-task-exec" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "main_artifact_name = solution.cpp" in benchmark_skill
    assert "problems/<problem_id>/initial_program.cpp" in benchmark_skill
    assert "initial_program_reference" in init_explore
    assert "main_artifact_name" in task_exec
    assert "solution.cpp" in task_exec
