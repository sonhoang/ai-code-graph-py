import asyncio
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from ai_code_graph.config import Config
from ai_code_graph.mcp_server import build_server
from ai_code_graph.model import Graph

TOOLS = {"search", "extract", "overview", "who_imports"}


def call(root: Path, tool: str, args: dict[str, object]) -> tuple[bool, str]:
    async def run() -> tuple[bool, str]:
        async with Client(build_server(root)) as client:
            result = await client.call_tool(tool, args)
            return bool(result.is_error), "".join(getattr(c, "text", "") for c in result.content)

    return asyncio.run(run())


def test_tools_are_registered_and_read_only(project: Path) -> None:
    async def run():
        async with Client(build_server(project)) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(run())
    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    search = next(t for t in tools if t.name == "search")
    assert search.input_schema["required"] == ["text"]


def test_search_tool_returns_compact_lines(graph: Graph, config: Config) -> None:
    is_error, text = call(config.root, "search", {"text": "area", "limit": 1})
    assert not is_error
    assert text.splitlines()[0] == (
        "sample_pkg/shapes.py:35: def area(shape: Circle, /, precision: int = 2) -> float"
        "  # Area of a shape, rounded."
    )


def test_extract_overview_and_who_imports_tools(graph: Graph, config: Config) -> None:
    _, text = call(config.root, "extract", {"qualname": "Circle.unit"})
    assert text.startswith("sample_pkg/shapes.py:20-22\n    @classmethod\n")
    _, text = call(config.root, "overview", {"package": "sample_pkg.util"})
    assert "### sample_pkg.util.io" in text and "### sample_pkg.shapes" not in text
    _, text = call(config.root, "who_imports", {"module": "sample_pkg.shapes"})
    assert text.startswith("sample_pkg.shapes is imported by 2 modules:")


def test_tool_errors_are_reported_not_raised(project: Path) -> None:
    is_error, text = call(project, "search", {"text": "circle"})  # no graph yet
    assert is_error and "ai-code-graph create" in text


def test_stdio_server_end_to_end(graph: Graph, config: Config) -> None:
    """Start `ai-code-graph mcp` as a subprocess and talk to it over stdio."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ai_code_graph", "--root", str(config.root), "mcp"],
    )

    async def run() -> tuple[set[str], str]:
        async with Client(params) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("search", {"text": "Circle", "limit": 1})
            return names, result.content[0].text  # type: ignore[union-attr]

    names, text = asyncio.run(run())
    assert names == TOOLS
    assert text.startswith("sample_pkg/shapes.py:10: @dataclass(frozen=True) class Circle")
