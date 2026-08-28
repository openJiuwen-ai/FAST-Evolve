// TS->Python bridge: forwarding opencode events to evotrias broker (.opencode/plugins/hooks_entry.py).
import { dirname, join } from "node:path"

// Bus-event throughput gate: message.part.delta fires per token and must never spawn a python process each.
const FORWARDED_EVENTS = new Set(["session.created", "session.idle"])

const PYTHON_TIMEOUT_MS = 30_000

export const EvotriasBridge = async ({ directory }: { directory: string }) => {
  const entry = join(".opencode", "plugins", "hooks_entry.py")
  const env = { ...process.env, PYTHONPATH: dirname(directory) }

  const dispatch = async (event: string, payload: unknown): Promise<any | null> => {
    try {
      const proc = Bun.spawn(["uv", "run", "python", entry, event], {
        cwd: directory,
        env,
        stdin: new TextEncoder().encode(JSON.stringify(payload)),
        stdout: "pipe",
        stderr: "pipe",
      })
      const timer = setTimeout(() => proc.kill(), PYTHON_TIMEOUT_MS)
      const [out, err] = await Promise.all([
        new Response(proc.stdout).text(),
        new Response(proc.stderr).text(),
      ])
      const code = await proc.exited
      clearTimeout(timer)
      if (code !== 0) {
        console.error(`evotrias-bridge: ${event} exited ${code}: ${err.trim()}`)
        return null
      }
      const trimmed = out.trim()
      return trimmed ? JSON.parse(trimmed) : null
    } catch (error) {
      // Hook failures must never break tool execution.
      console.error(`evotrias-bridge: dispatch failed for ${event}:`, error)
      return null
    }
  }

  return {
    "tool.execute.before": async (input: any, output: any) => {
      const result = await dispatch("tool.execute.before", { input, output })
      if (result?.args) {
        // Mutate args in place: opencode's tool executor holds a reference
        // to the original object, so replacing output.args is invisible to it.
        for (const key of Object.keys(output.args)) delete output.args[key]
        Object.assign(output.args, result.args)
      }
    },
    "tool.execute.after": async (input: any, output: any) => {
      await dispatch("tool.execute.after", { input, output })
    },
    event: async ({ event }: { event: any }) => {
      if (!FORWARDED_EVENTS.has(event?.type)) return
      await dispatch(event.type, event)
    },
  }
}
