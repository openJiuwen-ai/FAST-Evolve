from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER_SKILL = REPO_ROOT / "agent_evolve_plugin" / "skills" / "evolve" / "SKILL.md"
META_AGENT = REPO_ROOT / "agent_evolve_plugin" / "agents" / "meta-guidance-generator.md"
PROJECT_SETTINGS = REPO_ROOT / ".claude" / "settings.json"

GET_REQUEST_TOOL = (
    "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__" "evolve_get_meta_guidance_request"
)
COMMIT_GUIDANCE_TOOL = (
    "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__" "evolve_commit_meta_guidance"
)


def _agent_parts(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw_frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(raw_frontmatter)
    assert isinstance(metadata, dict)
    return metadata, body


def _dispatch_template(skill_text: str, agent_name: str) -> str:
    marker = f"**Template for {agent_name} dispatch:**"
    after_marker = skill_text.split(marker, 1)[1]
    return after_marker.split("```", 2)[1].strip()


def test_meta_guidance_agent_is_stateless_and_strictly_tool_scoped():
    metadata, body = _agent_parts(META_AGENT)

    assert metadata["name"] == "meta-guidance-generator"
    assert metadata["model"] == "inherit"
    assert {item.strip() for item in str(metadata["tools"]).split(",")} == {
        "Read",
        "Glob",
        "Grep",
        GET_REQUEST_TOOL,
        COMMIT_GUIDANCE_TOOL,
    }
    assert "do not poll" in body.lower()
    assert "never modify project files" in body.lower()
    assert body.index("evolve_get_meta_guidance_request") < body.index(
        "evolve_commit_meta_guidance"
    )
    assert "Never return the guidance JSON to the orchestrator." in body
    assert "make at most one additional commit attempt" in body


def test_meta_guidance_agent_returns_only_the_compact_result_contract():
    _, body = _agent_parts(META_AGENT)

    assert (
        "meta_guidance_result: committed request_id=<request_id> " "guidance_count=<count>"
    ) in body
    assert (
        "meta_guidance_result: not_committed request_id=<request_id> " "reason=<reason_code>"
    ) in body
    assert (
        "meta_guidance_result: mcp_error request_id=<request_id> " "reason=<short_reason_code>"
    ) in body
    assert "Do not include the request payload, generated guidance" in body


def test_orchestrator_passes_only_ids_and_never_handles_guidance_payload():
    master = MASTER_SKILL.read_text(encoding="utf-8")

    assert _dispatch_template(master, "meta-guidance-generator") == (
        "run_id: <run_id>\nrequest_id: <request_id>"
    )
    assert "evolve_get_meta_guidance_request" not in master
    assert "evolve_commit_meta_guidance" not in master
    assert "current_best_assessment" not in master
    assert "passes only `run_id` and `request_id`" in master


def test_meta_worker_is_foreground_reconciled_before_replenishment():
    master = MASTER_SKILL.read_text(encoding="utf-8")
    scheduling = master.split("// Convergence/meta-evolve check & replenishment", 1)[1]

    dispatch_position = scheduling.index("Dispatch to meta-guidance-generator")
    postcheck_position = scheduling.index("post_meta = evolve_check_convergence")
    replenish_position = scheduling.index("// Replenish:")

    assert dispatch_position < postcheck_position < replenish_position
    assert "`run_in_background: false`" in scheduling
    assert "meta_requests_attempted_this_reconciliation = set()" in scheduling
    assert "do not dispatch the same request_id again during this reconciliation pass" in scheduling
    assert "meta-guidance generation failure alone is not a stop signal" in scheduling
    assert "Treat post_meta as authoritative" in scheduling
    assert (
        "IF the worker result status is `mcp_error` and the authoritative "
        "postcondition does not confirm a commit"
    ) in scheduling
    assert "Apply the persistent MCP error rule during finalization." in scheduling


def test_project_permissions_allow_dispatching_the_meta_worker():
    settings = json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))

    assert "Agent(meta-guidance-generator)" in settings["permissions"]["allow"]
