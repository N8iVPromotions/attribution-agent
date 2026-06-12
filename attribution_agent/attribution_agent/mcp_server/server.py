"""
mcp_server/server.py
--------------------
FastMCP server exposing attribution pipeline tools over stdio transport.

Run via stdio (for Claude Desktop or any MCP client):
    python -m mcp_server.server

Tools exposed:
  - list_clients
  - get_client_config
  - run_ingest_flow
  - generate_report
  - get_pipeline_status
  - get_cost_summary
  - get_recent_memories
"""
from __future__ import annotations
import json
import sys
import os
from pathlib import Path

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "fastmcp not installed. Add 'fastmcp>=0.1.0' to requirements.txt "
            "and run: pip install fastmcp"
        )

    mcp = FastMCP("Attribution Agent", version="2.0.0")

    @mcp.tool()
    def list_clients(agency_id: str = "") -> str:
        """List all attribution clients, optionally filtered by agency."""
        from config.client_config import CLIENT_REGISTRY, reload_client_registry
        reload_client_registry()
        clients = [
            {"client_id": cid, "client_name": c.client_name, "agency_id": c.agency_id}
            for cid, c in CLIENT_REGISTRY.items()
            if not agency_id or c.agency_id == agency_id
        ]
        return json.dumps(clients)

    @mcp.tool()
    def get_client_config(client_id: str) -> str:
        """Get full configuration for a client."""
        from config.client_config import get_client
        from dataclasses import asdict
        try:
            cfg = get_client(client_id)
            d = asdict(cfg)
            return json.dumps(d, default=str)
        except ValueError as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def get_pipeline_status(run_id: str = "", limit: int = 10) -> str:
        """Get recent pipeline runs. Filter by run_id if provided."""
        from utils.databricks_writer import fetch_recent_pipeline_runs
        rows = fetch_recent_pipeline_runs(limit=limit)
        if run_id:
            rows = [r for r in rows if r.get("run_id") == run_id]
        return json.dumps(rows, default=str)

    @mcp.tool()
    def generate_report(client_id: str) -> str:
        """Generate an AI insight report for a client (uses most recent channel data)."""
        from agents.insight.insight_agent import generate_insight_report
        from config.client_config import get_client
        try:
            cfg = get_client(client_id)
            report = generate_insight_report(client_id)
            return json.dumps(report.to_dict(), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def run_ingest_flow(client_id: str, dry_run: bool = True) -> str:
        """Run the ingest pipeline for a client (pulls Meta, HubSpot, Stripe data)."""
        if not dry_run:
            return json.dumps({"error": "Live ingest via MCP not permitted — use dry_run=true or the API"})
        from flows.ingest_flow import ingest_flow
        try:
            result = ingest_flow(client_id)
            return json.dumps(result, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def get_cost_summary(agency_id: str = "") -> str:
        """Get token cost summary for the current month."""
        from utils.observability_queries import get_monthly_cost_by_agency
        rows = get_monthly_cost_by_agency()
        if agency_id:
            rows = [r for r in rows if r.get("agency_id") == agency_id]
        return json.dumps(rows, default=str)

    @mcp.tool()
    def get_recent_memories(client_id: str, limit: int = 5) -> str:
        """Get the most recent agent memories for a client."""
        from utils.memory_store import MemoryStore
        memories = MemoryStore()._fetch(client_id, limit=limit)
        return json.dumps(memories, default=str)

    return mcp


if __name__ == "__main__":
    mcp = _make_server()
    mcp.run(transport="stdio")
