"""Read-only queries over a graph: search, extract, imports and who-imports.

All text output uses the same ``path:line: ...`` shape as grep, so editors and
models can jump to it, but each line carries a declaration head instead of
whatever text happened to match.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, replace
from pathlib import Path

from .config import matches_any
from .model import Graph, Import, Module, Symbol
from .parser import sha256

KINDS = ("class", "function", "method", "async")
DOC_WIDTH = 72


class StaleGraph(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Hit:
    module: Module
    symbol: Symbol
    score: int
    overloads: int = 0  # typing.overload stubs folded into this hit

    @property
    def fullname(self) -> str:
        return f"{self.module.name}.{self.symbol.qualname}"

    def render(self) -> str:
        s = self.symbol
        extra = f"  (+{self.overloads} overloads)" if self.overloads else ""
        if s.doc:
            extra += "  # " + (s.doc if len(s.doc) <= DOC_WIDTH else s.doc[: DOC_WIDTH - 3] + "...")
        return f"{self.module.path}:{s.line}: {s.head()}{extra}"


def search(
    graph: Graph, query: str, *, kind: str | None = None, path: str | None = None
) -> list[Hit]:
    """Symbols matching every whitespace-separated term, best first.

    Ranking is deliberately simple and explainable: a term scores by *where* it
    matches (exact name > dotted-name suffix > name prefix > substring of the name >
    qualified name > signature > docstring), scores add up across terms, and public
    symbols get a small bonus. Ties go to the shorter qualified name. ``@overload``
    stubs are folded into the implementation they describe.
    """
    terms = query.lower().split()
    if not terms:
        return []
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    hits: list[Hit] = []
    for module, symbol in graph.symbols():
        if kind == "async" and not symbol.is_async:
            continue
        if kind not in (None, "async") and symbol.kind != kind:
            continue
        if path and not _path_matches(module.path, path):
            continue
        score = _score(module, symbol, terms)
        if score:
            hits.append(Hit(module, symbol, score))
    hits = _fold_overloads(hits)
    hits.sort(key=lambda h: (-h.score, len(h.fullname), h.module.path, h.symbol.line))
    return hits


def _fold_overloads(hits: list[Hit]) -> list[Hit]:
    """Attach ``@overload`` stubs to the implementation with the same qualified name."""

    def key(hit: Hit) -> tuple[str, str]:
        return hit.module.path, hit.symbol.qualname

    stubs: dict[tuple[str, str], list[Hit]] = {}
    out: list[Hit] = []
    for hit in hits:
        if _is_overload(hit.symbol):
            stubs.setdefault(key(hit), []).append(hit)
        else:
            out.append(hit)
    for i, hit in enumerate(out):
        folded = stubs.pop(key(hit), [])
        if folded:
            best = max(hit.score, *(h.score for h in folded))
            out[i] = replace(hit, score=best, overloads=len(folded))
    # Stubs whose implementation did not match the query stay as they are.
    out.extend(h for group in stubs.values() for h in group)
    return out


def _is_overload(symbol: Symbol) -> bool:
    return any(d in ("overload", "typing.overload") for d in symbol.decorators)


def _score(module: Module, symbol: Symbol, terms: list[str]) -> int:
    name = symbol.name.lower()
    full = f"{module.name}.{symbol.qualname}".lower()
    total = 0
    for term in terms:
        if name == term:
            points = 100
        elif full.endswith("." + term):
            points = 90  # e.g. "Graph.link"
        elif name.startswith(term):
            points = 60
        elif term in name:
            points = 40
        elif term in full:
            points = 20
        elif term in symbol.signature.lower():
            points = 10
        elif symbol.doc and term in symbol.doc.lower():
            points = 5
        else:
            return 0  # every term must match somewhere
        total += points
    return total + (5 if symbol.is_public else 0)


def _path_matches(path: str, pattern: str) -> bool:
    if any(c in pattern for c in "*?"):
        return matches_any(path, (pattern,))
    return path.startswith(pattern)


def format_hits(hits: list[Hit], limit: int) -> str:
    if not hits:
        return "no matches"
    lines = [h.render() for h in hits[:limit]]
    if len(hits) > limit:
        lines.append(f"... {len(hits) - limit} more (raise --limit or narrow the query)")
    return "\n".join(lines)


def find_symbol(graph: Graph, qualname: str) -> tuple[Module, Symbol]:
    """Resolve a dotted name: full (``pkg.mod.Class.method``) or a unique suffix of one."""
    exact: list[tuple[Module, Symbol]] = []
    suffix: list[tuple[Module, Symbol]] = []
    for module, symbol in graph.symbols():
        full = f"{module.name}.{symbol.qualname}"
        if full == qualname:
            exact.append((module, symbol))
        elif full.endswith("." + qualname):
            suffix.append((module, symbol))
    candidates = exact or suffix
    if not candidates:
        raise LookupError(f"no symbol named {qualname!r}; try `ai-code-graph search {qualname}`")
    if len(candidates) > 1:
        names = "\n".join(f"  {m.name}.{s.qualname}  ({m.path}:{s.line})" for m, s in candidates)
        raise LookupError(f"{qualname!r} is ambiguous; use one of:\n{names}")
    return candidates[0]


def extract(graph: Graph, root: Path, qualname: str) -> tuple[Module, Symbol, str]:
    """The exact source of one symbol (decorators through the last line of its body).

    Refuses to answer if the file changed since the graph was built, rather than
    returning the wrong lines.
    """
    module, symbol = find_symbol(graph, qualname)
    data = (root / module.path).read_bytes()
    if sha256(data) != module.sha256:
        raise StaleGraph(f"{module.path} changed since the last sync; run `ai-code-graph sync`")
    # newline="" splits on \n, \r\n and \r like the Python tokenizer, unlike str.splitlines().
    lines = io.StringIO(data.decode("utf-8"), newline="").readlines()
    return module, symbol, "".join(lines[symbol.start - 1 : symbol.end])


def find_module(graph: Graph, key: str) -> Module:
    module = graph.find_module(key)
    if module is None:
        close = sorted(m.name for m in graph.modules.values() if key in m.name)[:10]
        hint = f"; did you mean: {', '.join(close)}" if close else ""
        raise LookupError(f"no module {key!r} in the graph{hint}")
    return module


def imports_of(graph: Graph, key: str) -> str:
    module = find_module(graph, key)
    if not module.imports:
        return f"{module.name} has no imports"
    lines = []
    for imp in module.imports:
        local = ", ".join(graph.modules[p].name for p in imp.resolved) or "external"
        lines.append(f"{module.path}:{imp.line}: {imp.raw}  -> {local}")
    return "\n".join(lines)


def who_imports(graph: Graph, key: str) -> str:
    module = find_module(graph, key)
    lines = [
        f"{importer}:{imp.line}: {imp.raw}"
        for importer in sorted(module.imported_by)
        for imp in _imports_pointing_at(graph.modules[importer], module.path)
    ]
    if not lines:
        return f"nothing in the graph imports {module.name}"
    return f"{module.name} is imported by {len(module.imported_by)} modules:\n" + "\n".join(lines)


def _imports_pointing_at(module: Module, target_path: str) -> list[Import]:
    return [imp for imp in module.imports if target_path in imp.resolved]
