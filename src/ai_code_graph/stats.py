"""Measure what compact search output saves, against two baselines.

For each query it compares the size of:

* ``search``: what this tool returns (signature lines, top N hits);
* ``grep``: ``grep -rn``-style output for the same term over the same files,
  which is what an assistant without the index would usually run first;
* ``files``: the full text of every file that contains one of the shown hits,
  which is what an assistant pastes when it opens those files to find the API.

Token counts are estimated as characters / 4. That ratio is a rough rule of
thumb for English and code, not a tokenizer; the character counts are exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import Graph
from .query import format_hits, search


@dataclass(frozen=True)
class Measurement:
    query: str
    hits: int
    search_chars: int
    grep_chars: int
    file_count: int
    file_chars: int


def measure(graph: Graph, root: Path, queries: list[str], limit: int = 20) -> list[Measurement]:
    texts = {
        path: (root / path).read_text(encoding="utf-8", errors="replace") for path in graph.modules
    }
    rows = []
    for query in queries:
        hits = search(graph, query)
        shown = hits[:limit]
        files = {h.module.path for h in shown}
        rows.append(
            Measurement(
                query=query,
                hits=len(shown),
                search_chars=len(format_hits(hits, limit)),
                grep_chars=len(_grep(texts, query)),
                file_count=len(files),
                file_chars=sum(len(texts[p]) for p in files),
            )
        )
    return rows


def _grep(texts: dict[str, str], query: str) -> str:
    """Case-insensitive ``grep -rn`` for the query string."""
    needle = query.lower()
    return "\n".join(
        f"{path}:{n}:{line}"
        for path in sorted(texts)
        for n, line in enumerate(texts[path].splitlines(), start=1)
        if needle in line.lower()
    )


def render_table(rows: list[Measurement], title: str) -> str:
    def tok(chars: int) -> str:
        return f"{round(chars / 4):,}"

    lines = [
        f"{title}",
        "",
        "| query | hits | search tokens | grep -rn tokens | files | full-file tokens "
        "| search / files |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        ratio = f"{r.search_chars / r.file_chars:.1%}" if r.file_chars else "-"
        lines.append(
            f"| `{r.query}` | {r.hits} | {tok(r.search_chars)} | {tok(r.grep_chars)} "
            f"| {r.file_count} | {tok(r.file_chars)} | {ratio} |"
        )
    s = sum(r.search_chars for r in rows)
    g = sum(r.grep_chars for r in rows)
    f = sum(r.file_chars for r in rows)
    ratio = f"{s / f:.1%}" if f else "-"
    lines.append(f"| total | | {tok(s)} | {tok(g)} | | {tok(f)} | {ratio} |")
    return "\n".join(lines)
