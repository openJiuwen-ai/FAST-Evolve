const sharedWorkspaceAgents = new Set([
  "evotrias_plugin:init-explorer",
  "evotrias_plugin:task-executor",
  "evotrias_plugin:meta-guidance-generator",
]);

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let event;
  try {
    event = JSON.parse(input);
  } catch {
    process.exitCode = 0;
    return;
  }

  const toolInput = event.tool_input;
  if (!sharedWorkspaceAgents.has(toolInput?.subagent_type)) {
    return;
  }
  if (!("isolation" in toolInput)) {
    return;
  }

  const { isolation: _ignoredIsolation, ...updatedInput } = toolInput;
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "allow",
        permissionDecisionReason:
          "EvoTrias persistent workers must use the shared main repository.",
        updatedInput,
      },
    }),
  );
});
