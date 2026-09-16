"""Read-only MCP boundary. Notification mutation stays behind graph approval."""

import os
from pathlib import Path
from fastmcp import FastMCP
from .data import seed_database
from .tools import summarize, retrieve_rules

mcp = FastMCP("business-insight-tools")


@mcp.tool
def query_refund_metrics(
    dimension: str = "channel", channel: str | None = None, product: str | None = None
) -> dict:
    """Query synthetic refund counts for two fixed September 2026 windows."""
    path = Path(os.getenv("INSIGHT_DATA_DIR", "data/runtime")) / "orders.db"
    seed_database(path)
    return summarize(path, dimension, channel, product)


@mcp.tool
def search_business_rules(query: str) -> list[dict]:
    """Retrieve documented metric definitions and investigation guidance."""
    return retrieve_rules(query)


if __name__ == "__main__":
    mcp.run()
