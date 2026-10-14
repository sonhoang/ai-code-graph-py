"""Command-line interface: ``ai-code-graph <command>`` (alias ``acg``)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from . import __version__, indexer, query
from .config import Config, ConfigError
from .model import Graph
from .overview import render_overview
from .review import ReviewError, build_prompt, git_diff, run_review

DEFAULT_STATS_QUERIES = ["search", "sync", "import", "signature"]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = Config.load(Path(args.root))
        return args.func(config, args)
    except (ConfigError, indexer.GraphNotFound, LookupError, query.StaleGraph, ReviewError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # Output piped into `head` and closed early; silence the flush at exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-code-graph",
        description="Index a Python codebase and search it by signature.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, func: Callable[[Config, argparse.Namespace], int], help: str):
        cmd = sub.add_parser(name, help=help, description=help)
        cmd.set_defaults(func=func)
        return cmd

    add("create", _create, "full scan; writes graph.json, meta.json and overview.md")
    add("sync", _sync, "reparse only added or changed files, drop removed ones")

    s = add("search", _search, "ranked signature lines for symbols matching QUERY")
    s.add_argument("query", nargs="+")
    s.add_argument("--kind", choices=query.KINDS)
    s.add_argument("--path", help="path prefix or glob to search under")
    s.add_argument("--limit", type=int, default=20)

    e = add("extract", _extract, "print the exact source of one symbol")
    e.add_argument("qualname", help='dotted name, e.g. "pkg.mod.Class.method" or "Class.method"')

    o = add("overview", _overview, "print the overview (from the saved graph)")
    o.add_argument("package", nargs="?", help="only modules under this dotted prefix")

    i = add("imports", _imports, "what a module imports")
    i.add_argument("module", help="dotted name or path")
    w = add("who-imports", _who_imports, "which modules import a module")
    w.add_argument("module", help="dotted name or path")

    r = add("review", _review, "send the overview (and optionally git diff) to Claude")
    r.add_argument("--diff", nargs="?", const="", metavar="BASE", help="include `git diff [BASE]`")
    r.add_argument("--dry-run", action="store_true", help="print the prompt instead of sending it")

    st = add("stats", _stats, "measure search output size against grep and full files")
    st.add_argument("queries", nargs="*", help=f"default: {' '.join(DEFAULT_STATS_QUERIES)}")
    st.add_argument("--include", action="append", help="override include globs (repeatable)")
    st.add_argument("--limit", type=int, default=20)
    st.add_argument("--title", default=None)

    add("mcp", _mcp, "run the MCP server on stdio")
    return p


def _create(config: Config, args: argparse.Namespace) -> int:
    graph, report = indexer.create(config)
    _print_build(config, graph, report)
    return 0


def _sync(config: Config, args: argparse.Namespace) -> int:
    graph, report = indexer.sync(config)
    _print_build(config, graph, report)
    return 0


def _print_build(config: Config, graph: Graph, report: indexer.SyncReport) -> None:
    symbols = sum(len(m.symbols) for m in graph.modules.values())
    print(f"{report.summary()}; {len(graph.modules)} modules, {symbols} symbols")
    for m in sorted(graph.modules.values(), key=lambda m: m.path):
        if m.error and (m.path in report.added or m.path in report.changed):
            print(f"  skipped {m.path}: {m.error}", file=sys.stderr)


def _search(config: Config, args: argparse.Namespace) -> int:
    graph = indexer.load(config)
    try:
        hits = query.search(graph, " ".join(args.query), kind=args.kind, path=args.path)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc
    print(query.format_hits(hits, args.limit))
    return 0 if hits else 1


def _extract(config: Config, args: argparse.Namespace) -> int:
    module, symbol, source = query.extract(indexer.load(config), config.root, args.qualname)
    print(f"# {module.path}:{symbol.start}-{symbol.end}")
    print(source, end="")
    return 0


def _overview(config: Config, args: argparse.Namespace) -> int:
    print(render_overview(indexer.load(config), prefix=args.package), end="")
    return 0


def _imports(config: Config, args: argparse.Namespace) -> int:
    print(query.imports_of(indexer.load(config), args.module))
    return 0


def _who_imports(config: Config, args: argparse.Namespace) -> int:
    print(query.who_imports(indexer.load(config), args.module))
    return 0


def _review(config: Config, args: argparse.Namespace) -> int:
    overview = render_overview(indexer.load(config))
    diff = git_diff(config.root, args.diff or None) if args.diff is not None else None
    prompt = build_prompt(overview, diff)
    if args.dry_run:
        print(prompt)
        return 0
    out = config.output_dir / "review.md"
    out.write_text(run_review(prompt), encoding="utf-8")
    print(f"wrote {out.relative_to(config.root)}")
    return 0


def _stats(config: Config, args: argparse.Namespace) -> int:
    from .stats import measure, render_table

    if args.include:
        config = replace(config, include=tuple(args.include))
    graph, _ = indexer.build(config)  # in memory; does not touch the output directory
    rows = measure(graph, config.root, args.queries or DEFAULT_STATS_QUERIES, args.limit)
    symbols = sum(len(m.symbols) for m in graph.modules.values())
    title = args.title or f"{config.root.name}: {len(graph.modules)} modules, {symbols} symbols"
    print(render_table(rows, title))
    return 0


def _mcp(config: Config, args: argparse.Namespace) -> int:
    from .mcp_server import serve

    serve(config.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
