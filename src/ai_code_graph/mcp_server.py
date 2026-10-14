"""MCP server over stdio, exposing four read-only tools.

Built on ``MCPServer`` from the official MCP Python SDK (it was called ``FastMCP``
before mcp 2.0). Tools read ``.ai-code-graph/graph.json`` on every call, so a
``sync`` run in a terminal is picked up without restarting the server. They never
write: keeping the index fresh stays an explicit step the user controls.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, query
from .config import Config
from .indexer import GraphNotFound, load
from .model import Graph
from .overview import render_overview

INSTRUCTIONS = """\
Index of this Python codebase. To find where a class, function or method is \
defined, call `search` before grepping or opening files: it returns one \
`path:line: signature  # docstring` line per match, without bodies. Then call \
`extract` for the exact source of the one symbol you need, or read just those \
lines. `overview` gives a per-package table of contents; `who_imports` lists the \
modules that depend on a module. Results reflect the last `ai-code-graph sync`."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def build_server(root: Path) -> MCPServer:
    config = Config.load(root)

    def graph() -> Graph:
        try:
            return load(config)
        except GraphNotFound as exc:
            raise ToolError(str(exc)) from exc

    server = MCPServer("ai-code-graph", version=__version__, instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def search(text: str, kind: str | None = None, path: str | None = None, limit: int = 20) -> str:
        """Find classes, functions and methods by name, qualified name, signature or docstring.

        Args:
            text: Words to match; every word must match. Example: "sync report".
            kind: Optional filter: class, function, method or async.
            path: Optional path prefix or glob, e.g. "src/pkg/" or "**/models/*.py".
            limit: Maximum lines to return.
        """
        try:
            hits = query.search(graph(), text, kind=kind, path=path)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return query.format_hits(hits, limit)

    @server.tool(annotations=READ_ONLY)
    def extract(qualname: str) -> str:
        """Exact source of one symbol.

        Args:
            qualname: Dotted name such as "pkg.module.Class.method", or a unique suffix
                such as "Class.method".
        """
        try:
            module, symbol, source = query.extract(graph(), config.root, qualname)
        except (LookupError, query.StaleGraph) as exc:
            raise ToolError(str(exc)) from exc
        return f"{module.path}:{symbol.start}-{symbol.end}\n{source}"

    @server.tool(annotations=READ_ONLY)
    def overview(package: str | None = None) -> str:
        """Table of contents: modules with their exported signatures, grouped by package.

        Args:
            package: Optional dotted prefix to limit the output, e.g. "pkg.sub".
        """
        return render_overview(graph(), prefix=package)

    @server.tool(annotations=READ_ONLY)
    def who_imports(module: str) -> str:
        """Modules that import the given module (dotted name or path), with the import lines."""
        try:
            return query.who_imports(graph(), module)
        except LookupError as exc:
            raise ToolError(str(exc)) from exc

    return server


def serve(root: Path) -> None:
    build_server(root).run("stdio")
