"""Build the graph (create), keep it current (sync), and read/write ``.ai-code-graph/``."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, discover
from .model import Graph
from .overview import render_overview
from .parser import parse_module, sha256

GRAPH_FILE = "graph.json"
META_FILE = "meta.json"
OVERVIEW_FILE = "overview.md"


class GraphNotFound(FileNotFoundError):
    pass


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    full_rebuild: bool = False

    @property
    def has_changes(self) -> bool:
        return self.full_rebuild or bool(self.added or self.changed or self.removed)

    def summary(self) -> str:
        mode = "full scan" if self.full_rebuild else "incremental"
        return (
            f"{mode}: {len(self.added)} added, {len(self.changed)} changed, "
            f"{len(self.removed)} removed, {self.unchanged} unchanged"
        )


def build(config: Config, previous: Graph | None = None) -> tuple[Graph, SyncReport]:
    """Scan ``config.root``; reparse only files whose sha256 differs from ``previous``.

    Pure with respect to the output directory: nothing is written. ``create`` and
    ``sync`` call this and then ``write``; ``stats`` uses it for an in-memory graph.
    """
    old = previous.modules if previous else {}
    report = SyncReport(full_rebuild=previous is None)
    graph = Graph()
    for path in discover(config):
        data = (config.root / path).read_bytes()
        cached = old.get(path)
        if cached is not None and cached.sha256 == sha256(data):
            graph.modules[path] = cached
            report.unchanged += 1
            continue
        graph.modules[path] = parse_module(path, data)
        (report.changed if cached is not None else report.added).append(path)
    report.removed = sorted(set(old) - set(graph.modules))
    graph.link()
    return graph, report


def create(config: Config) -> tuple[Graph, SyncReport]:
    graph, report = build(config)
    write(config, graph)
    return graph, report


def sync(config: Config) -> tuple[Graph, SyncReport]:
    """Incremental update. Falls back to a full scan when there is no usable previous graph.

    Writes nothing when no file changed, so committed outputs do not churn.
    """
    graph, report = build(config, previous=_load_previous(config))
    if report.has_changes:
        write(config, graph)
    return graph, report


def load(config: Config) -> Graph:
    path = config.output_dir / GRAPH_FILE
    if not path.is_file():
        raise GraphNotFound(f"no graph at {path}; run `ai-code-graph create` first")
    return Graph.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write(config: Config, graph: Graph) -> None:
    out = config.output_dir
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "tool": "ai-code-graph-py",
        "tool_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": {path: graph.modules[path].sha256 for path in sorted(graph.modules)},
    }
    _write_atomic(out / GRAPH_FILE, _dumps(graph.to_dict()))
    _write_atomic(out / META_FILE, _dumps(meta))
    _write_atomic(out / OVERVIEW_FILE, render_overview(graph))


def read_meta(config: Config) -> dict[str, Any] | None:
    path = config.output_dir / META_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_previous(config: Config) -> Graph | None:
    """The cached graph, or None if it is missing, unreadable, or from another tool version.

    A different tool version may parse files differently, so its cache is not trusted.
    """
    meta = read_meta(config)
    if not meta or meta.get("tool_version") != __version__:
        return None
    try:
        graph = load(config)
    except (GraphNotFound, ValueError, KeyError, TypeError):
        return None
    hashes: dict[str, str] = meta.get("files", {})
    # Only reuse modules whose hash agrees with meta.json.
    graph.modules = {p: m for p, m in graph.modules.items() if hashes.get(p) == m.sha256}
    return graph


def _dumps(data: Any) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
