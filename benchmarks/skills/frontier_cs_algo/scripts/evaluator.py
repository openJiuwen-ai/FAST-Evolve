#!/usr/bin/env python
"""Host-side evaluator for one Frontier-CS algorithmic problem."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SKILL_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = SKILL_DIR / "suite_manifest.json"
DEFAULT_JUDGE_URL = "http://localhost:8081"
DEFAULT_EVALUATION_TIMEOUT = 300.0


class JudgeError(RuntimeError):
    """Raised when the judge API cannot complete an evaluation."""


def load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as source:
        manifest = json.load(source)
    if manifest.get("benchmark") != "frontier_cs_algo":
        raise ValueError(f"Unexpected benchmark manifest: {MANIFEST_PATH}")
    return manifest


def normalize_problem_id(raw_problem_id: str, manifest: dict[str, Any]) -> str:
    value = str(raw_problem_id).strip()
    if not value.isdigit():
        raise ValueError(f"problem_id must be numeric, got {raw_problem_id!r}")
    problem_id = str(int(value))
    valid_ids = {str(problem["problem_id"]) for problem in manifest.get("problems", [])}
    if problem_id not in valid_ids:
        raise ValueError(
            f"problem_id {problem_id} is not in the {manifest.get('problem_count', 0)}-problem "
            "Frontier-CS algorithmic manifest"
        )
    return problem_id


def _multipart_body(
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    content: bytes,
) -> tuple[bytes, str]:
    boundary = f"----AgentEvolve{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


class JudgeClient:
    """Minimal client for the official Frontier-CS algorithmic judge API."""

    def __init__(self, base_url: str, *, request_timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                status = int(response.status)
                raw = response.read()
        except HTTPError as exc:
            status = int(exc.code)
            raw = exc.read()
        except (URLError, TimeoutError, OSError) as exc:
            raise JudgeError(f"Judge request failed at {self.base_url}: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JudgeError(
                f"Judge returned non-JSON response for {method} {path} (HTTP {status})"
            ) from exc
        if not isinstance(payload, dict):
            raise JudgeError(f"Judge returned invalid JSON object for {method} {path}")
        return status, payload

    def submit(self, problem_id: str, code: str) -> str:
        body, boundary = _multipart_body(
            {"pid": problem_id, "lang": "cpp"},
            file_field="code",
            filename="solution.cpp",
            content=code.encode("utf-8"),
        )
        status, payload = self._request_json(
            "POST",
            "/submit",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if status < 200 or status >= 300:
            detail = payload.get("message") or payload.get("error") or payload
            raise JudgeError(f"Judge submission failed (HTTP {status}): {detail}")
        submission_id = payload.get("sid")
        if not submission_id:
            raise JudgeError("Judge submission response did not include 'sid'")
        return str(submission_id)

    def get_result(self, submission_id: str) -> tuple[int, dict[str, Any]]:
        return self._request_json("GET", f"/result/{submission_id}")


def _finite_float(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JudgeError(f"Judge result has invalid {field}: {value!r}") from exc
    if not math.isfinite(number):
        raise JudgeError(f"Judge result has non-finite {field}: {value!r}")
    return number


def _case_metrics(payload: dict[str, Any]) -> dict[str, int]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return {}
    accepted = 0
    runtime_failures = 0
    for case in cases:
        status = str(case.get("status", "")) if isinstance(case, dict) else ""
        if status in {"Accepted", "Correct"}:
            accepted += 1
        elif status and status != "Wrong Answer":
            runtime_failures += 1
    return {
        "case_count": len(cases),
        "accepted_case_count": accepted,
        "runtime_failure_count": runtime_failures,
    }


def evaluate_candidate(
    artifact_ref: Path,
    problem_id: str,
    *,
    client: JudgeClient,
    timeout_seconds: float,
    poll_interval: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    started = monotonic()
    if not artifact_ref.is_file():
        raise FileNotFoundError(f"Candidate file not found: {artifact_ref}")
    if artifact_ref.suffix.lower() not in {".cpp", ".cc", ".cxx"}:
        raise ValueError(f"Frontier-CS candidate must be C++ source: {artifact_ref}")
    code = artifact_ref.read_text(encoding="utf-8")
    if not code.strip():
        raise ValueError(f"Candidate file is empty: {artifact_ref}")

    submission_id = client.submit(problem_id, code)
    while True:
        elapsed = monotonic() - started
        if elapsed > timeout_seconds:
            raise JudgeError(
                f"Evaluation timed out after {timeout_seconds:g}s "
                f"(submission_id={submission_id})"
            )

        status_code, payload = client.get_result(submission_id)
        if status_code == 404:
            sleep(poll_interval)
            continue
        if status_code < 200 or status_code >= 300:
            detail = payload.get("message") or payload.get("error") or payload
            raise JudgeError(f"Judge result request failed (HTTP {status_code}): {detail}")

        judge_status = str(payload.get("status", "")).lower()
        if judge_status == "error":
            detail = payload.get("message") or payload.get("error") or "unknown judge error"
            raise JudgeError(f"Judge evaluation failed: {detail}")
        if judge_status == "done":
            bounded_score = _finite_float(payload.get("score", 0.0), field="score")
            return {
                "success": True,
                "score_bounded": bounded_score,
                "fitness": bounded_score,
                "eval_time": float(monotonic() - started),
                "submission_id": submission_id,
                "judge_status": judge_status,
                "passed": bool(payload.get("passed", False)),
                "judge_result": payload.get("result"),
                **_case_metrics(payload),
            }
        sleep(poll_interval)


def format_result(
    result: dict[str, Any],
    artifact_ref: str,
    *,
    problem_id: str,
    judge_url: str,
    run_id: str,
    task_id: str,
    subagent_id: str = "",
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if manifest is None:
        manifest = load_manifest()
    source = dict(manifest.get("source", {}))
    judge_reference = dict(manifest.get("judge_reference", {}))
    success = bool(result.get("success"))
    metric_keys = (
        "score_bounded",
        "eval_time",
        "passed",
        "case_count",
        "accepted_case_count",
        "runtime_failure_count",
    )
    metrics = {key: result[key] for key in metric_keys if key in result}
    misc_keys = ("submission_id", "judge_status", "judge_result")
    misc = {
        "run_id": run_id,
        "task_id": task_id,
        "subagent_id": subagent_id,
        **{key: result[key] for key in misc_keys if key in result},
    }
    if result.get("error"):
        misc["error"] = str(result["error"])
    return {
        "status": "success" if success else "failure",
        "artifact_ref": artifact_ref,
        "fitness": float(result["fitness"]) if success else None,
        "metrics": metrics,
        "profile": {
            "benchmark": "frontier_cs_algo",
            "track": "algorithmic",
            "problem_id": problem_id,
            "suite_source_revision": source.get("revision"),
            "judge_reference_revision": judge_reference.get("revision"),
            "judge_url": judge_url,
        },
        "misc": misc,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--problem-id", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--out")
    parser.add_argument(
        "--judge-url",
        default=os.environ.get("FRONTIER_CS_JUDGE_URL", DEFAULT_JUDGE_URL),
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_EVALUATION_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()

    artifact_ref = Path(args.artifact_ref)
    output_path = Path(args.out) if args.out else artifact_ref.parent / "eval_result.json"
    manifest: dict[str, Any] | None = None
    try:
        manifest = load_manifest()
        problem_id = normalize_problem_id(args.problem_id, manifest)
        result = evaluate_candidate(
            artifact_ref,
            problem_id,
            client=JudgeClient(args.judge_url),
            timeout_seconds=args.timeout,
            poll_interval=args.poll_interval,
        )
    except Exception as exc:
        problem_id = str(args.problem_id)
        result = {"success": False, "error": str(exc)}

    formatted = format_result(
        result,
        args.artifact_ref,
        problem_id=problem_id,
        judge_url=args.judge_url.rstrip("/"),
        run_id=args.run_id,
        task_id=args.task_id,
        subagent_id=args.subagent_id,
        manifest=manifest if manifest is not None else {"benchmark": "frontier_cs_algo"},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(formatted, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if formatted["status"] == "success":
        print(output_path)
        raise SystemExit(0)
    print(formatted["misc"].get("error", "Unknown evaluation failure"))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
