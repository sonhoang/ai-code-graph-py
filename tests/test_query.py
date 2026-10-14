from pathlib import Path

import pytest

from ai_code_graph import indexer, query
from ai_code_graph.config import Config
from ai_code_graph.model import Graph


def top(graph: Graph, text: str, **filters: str) -> list[str]:
    return [h.fullname for h in query.search(graph, text, **filters)]


def test_exact_name_ranks_first(graph: Graph) -> None:
    assert top(graph, "circle")[0] == "sample_pkg.shapes.Circle"
    assert top(graph, "area")[0] == "sample_pkg.shapes.area"


def test_ranking_order_is_explainable(graph: Graph) -> None:
    # exact name (Circle) > name substring (dump_circle, ...) > qualified name (Circle.unit)
    hits = top(graph, "circle")
    assert hits.index("sample_pkg.shapes.Circle") < hits.index("sample_pkg.util.io.dump_circle")
    assert hits.index("sample_pkg.util.io.dump_circle") < hits.index(
        "sample_pkg.shapes.Circle.unit"
    )


def test_all_terms_must_match(graph: Graph) -> None:
    assert top(graph, "circle unit") == ["sample_pkg.shapes.Circle.unit"]
    assert top(graph, "circle nonexistent") == []


def test_dotted_query_matches_qualified_name(graph: Graph) -> None:
    assert top(graph, "Circle.diameter")[0] == "sample_pkg.shapes.Circle.diameter"


def test_signature_and_docstring_terms(graph: Graph) -> None:
    assert "sample_pkg.shapes.fetch_shapes" in top(graph, "timeout")  # parameter name
    assert top(graph, "rounded") == ["sample_pkg.shapes.area"]  # docstring only


def test_kind_and_path_filters(graph: Graph) -> None:
    assert top(graph, "shapes", kind="async") == ["sample_pkg.shapes.fetch_shapes"]
    assert set(top(graph, "circle", kind="method")) == {
        "sample_pkg.shapes.Circle.diameter",
        "sample_pkg.shapes.Circle.unit",
        "sample_pkg.shapes.Circle.scaled",
        "sample_pkg.shapes.Circle.Meta.describe",
    }
    assert set(top(graph, "circle", path="sample_pkg/util/")) == {
        "sample_pkg.util.io.dump_circle",
        "sample_pkg.util.io.load_circle",
        "sample_pkg.util.io.default_circle",
    }
    assert top(graph, "circle", path="**/io.py") == top(graph, "circle", path="sample_pkg/util/")
    with pytest.raises(ValueError):
        query.search(graph, "x", kind="variable")


def test_public_symbols_outrank_private_ones(graph: Graph) -> None:
    hits = top(graph, "exported")
    assert hits == ["sample_pkg.not_exported"]
    assert top(graph, "helper") == ["sample_pkg._private_helper"]


def test_compact_line_format_and_limit(graph: Graph) -> None:
    out = query.format_hits(query.search(graph, "circle"), limit=1)
    first, more = out.splitlines()
    assert first == (
        "sample_pkg/shapes.py:10: @dataclass(frozen=True) class Circle  # A circle with a radius."
    )
    assert more.startswith("... ") and "more" in more
    assert query.format_hits([], limit=5) == "no matches"


def test_extract_returns_exact_source(graph: Graph, config: Config) -> None:
    module, symbol, source = query.extract(graph, config.root, "Circle.diameter")
    assert (module.path, symbol.start, symbol.end) == ("sample_pkg/shapes.py", 15, 18)
    assert source == (
        "    @property\n"
        "    def diameter(self) -> float:\n"
        '        """Twice the radius."""\n'
        "        return self.radius * 2\n"
    )
    _, _, full = query.extract(graph, config.root, "sample_pkg.shapes.fetch_shapes")
    assert full.startswith("async def fetch_shapes(\n") and full.endswith("    return []\n")


def test_extract_is_byte_exact_with_crlf_line_endings(project: Path, config: Config) -> None:
    (project / "sample_pkg" / "crlf.py").write_bytes(b"x = 1\r\n\r\ndef f():\r\n    return 1\r\n")
    graph, _ = indexer.create(config)
    _, _, source = query.extract(graph, config.root, "crlf.f")
    assert source == "def f():\r\n    return 1\r\n"


def test_extract_unknown_and_ambiguous(project: Path, config: Config) -> None:
    (project / "sample_pkg" / "other.py").write_text("def area(): ...\n")
    graph, _ = indexer.create(config)
    with pytest.raises(LookupError, match="no symbol"):
        query.extract(graph, config.root, "nope")
    with pytest.raises(LookupError, match="ambiguous") as err:
        query.extract(graph, config.root, "area")
    assert "sample_pkg.other.area" in str(err.value)
    assert "sample_pkg.shapes.area" in str(err.value)
    # A full name is always unambiguous.
    assert query.extract(graph, config.root, "sample_pkg.other.area")[2] == "def area(): ...\n"


def test_extract_refuses_stale_files(project: Path, graph: Graph, config: Config) -> None:
    shapes = project / "sample_pkg" / "shapes.py"
    shapes.write_text("\n\n" + shapes.read_text())  # every line moves down by two
    with pytest.raises(query.StaleGraph, match="sync"):
        query.extract(graph, config.root, "Circle.diameter")


def test_imports_and_who_imports(graph: Graph) -> None:
    assert query.who_imports(graph, "sample_pkg.shapes").splitlines() == [
        "sample_pkg.shapes is imported by 2 modules:",
        "sample_pkg/__init__.py:3: from .shapes import Circle, area",
        "sample_pkg/util/io.py:5: import sample_pkg.shapes",
        "sample_pkg/util/io.py:7: from .. import shapes",
        "sample_pkg/util/io.py:8: from ..shapes import Circle",
    ]
    assert "nothing in the graph imports" in query.who_imports(graph, "sample_pkg/util/io.py")
    out = query.imports_of(graph, "sample_pkg.util.io")
    assert "sample_pkg/util/io.py:3: import json  -> external" in out
    with pytest.raises(LookupError, match=r"did you mean: sample_pkg\.shapes"):
        query.who_imports(graph, "shapes")


def test_overloads_fold_and_long_signatures_clip(project: Path, config: Config) -> None:
    params = ", ".join(f"option_{i}: int = {i}" for i in range(30))
    (project / "sample_pkg" / "api.py").write_text(
        "from typing import overload\n\n"
        "@overload\ndef parse(x: int) -> int: ...\n"
        "@overload\ndef parse(x: str) -> str: ...\n"
        "def parse(x):\n    return x\n\n"
        f"def configure({params}) -> None:\n    pass\n"
    )
    graph, _ = indexer.create(config)
    hits = query.search(graph, "parse")
    assert [(h.symbol.line, h.overloads) for h in hits] == [(7, 2)]
    assert hits[0].render() == "sample_pkg/api.py:7: def parse(x)  (+2 overloads)"

    line = query.search(graph, "configure")[0].render()
    assert line.endswith(" ...") and len(line) < 240
    # extract still returns the whole definition
    assert "option_29: int = 29" in query.extract(graph, config.root, "api.configure")[2]
