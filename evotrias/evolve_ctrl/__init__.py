"""Evolution control: parent/inspiration selection, convergence, budget, lineage.

Pure algorithm classes and derived in-memory views over ``evotrias.db``.
Nothing here holds state across calls or touches disk directly; the MCP layer
calls in, this layer reads and writes through db objects.  Dependency
direction is ``mcp -> evolve_ctrl -> db``.  Scheduling (which agent holds
which task) is not evolution control and lives in ``evotrias.scheduler``.

The entry point is ``evotrias.evolve_ctrl.controller``: one function per MCP
tool.  Every other module here is its implementation.
"""
