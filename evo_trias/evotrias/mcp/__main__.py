"""``python -m evotrias.mcp``: serve the controller MCP over stdio."""

import os

from evotrias.logger import logger
from evotrias.mcp import mcp


def main() -> None:
    logger.info(f"mcp server {mcp.name} starting pid={os.getpid()} cwd={os.getcwd()}")
    try:
        mcp.run()
    except KeyboardInterrupt:
        logger.info(f"mcp server {mcp.name} interrupted")
        raise
    except Exception:
        logger.critical(f"mcp server {mcp.name} crashed", exc_info=True)
        raise
    logger.info(f"mcp server {mcp.name} stopped")


if __name__ == "__main__":
    main()
