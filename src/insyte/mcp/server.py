"""The Insyte MCP server: exposes the tool service over the Model Context Protocol.

Every tool delegates to :class:`InsyteToolService`, so the safety guarantees (validation,
permission checks, row limits, timeouts, audit logging, no credential exposure) hold for MCP
clients exactly as they do for the CLI and TUI.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from insyte.mcp.tools import InsyteToolService

TOOL_NAMES = (
    "insyte_get_database_summary",
    "insyte_search_schema",
    "insyte_describe_table",
    "insyte_list_metrics",
    "insyte_get_metric_definition",
    "insyte_create_analysis_plan",
    "insyte_run_safe_sql",
    "insyte_compare_periods",
    "insyte_segment_metric",
    "insyte_generate_chart_spec",
    "insyte_get_query_history",
)


def build_mcp_server(service: InsyteToolService) -> FastMCP:
    """Create a FastMCP server whose tools delegate to the service."""

    mcp = FastMCP("insyte")

    @mcp.tool()
    def insyte_get_database_summary() -> dict:
        """Summarise the scanned database: schemas, tables, categories, and last scan time."""
        return service.get_database_summary()

    @mcp.tool()
    def insyte_search_schema(query: str, limit: int = 20) -> dict:
        """Search scanned tables and columns for a substring."""
        return service.search_schema(query, limit)

    @mcp.tool()
    def insyte_describe_table(name: str) -> dict:
        """Describe a table: columns, keys, and relationships (e.g. 'public.orders')."""
        return service.describe_table(name)

    @mcp.tool()
    def insyte_list_metrics() -> dict:
        """List semantic-layer metrics and dimensions available for analysis."""
        return service.list_metrics()

    @mcp.tool()
    def insyte_get_metric_definition(name: str) -> dict:
        """Get the full definition of one metric."""
        return service.get_metric_definition(name)

    @mcp.tool()
    def insyte_create_analysis_plan(question: str) -> dict:
        """Turn a natural-language question into a structured analysis plan."""
        return service.create_analysis_plan(question)

    @mcp.tool()
    def insyte_run_safe_sql(sql: str) -> dict:
        """Validate and run a read-only SQL query. Unsafe queries are rejected, not executed."""
        return service.run_safe_sql(sql)

    @mcp.tool()
    def insyte_compare_periods(metric: str, grain: str = "month") -> dict:
        """Compare a metric between the current and previous period for a grain."""
        return service.compare_periods(metric, grain)

    @mcp.tool()
    def insyte_segment_metric(metric: str, dimension: str, limit: int = 20) -> dict:
        """Break a metric down by a dimension, ranked by contribution."""
        return service.segment_metric(metric, dimension, limit)

    @mcp.tool()
    def insyte_generate_chart_spec(
        kind: str, columns: list[str], row_count: int, label: str = "result"
    ) -> dict:
        """Recommend a chart type for a result shape (kind: aggregate/timeseries/segment)."""
        return service.generate_chart_spec(kind, columns, row_count, label)

    @mcp.tool()
    def insyte_get_query_history(limit: int = 20) -> dict:
        """Return recent audited queries (successful, blocked, and errored)."""
        return service.get_query_history(limit)

    return mcp
