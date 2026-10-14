from pathlib import Path

from ai_code_graph.model import Symbol
from ai_code_graph.parser import parse_module

FIXTURE = Path(__file__).parent / "fixtures" / "sample_pkg"


def symbols(path: str) -> dict[str, Symbol]:
    module = parse_module(path, (FIXTURE / path).read_bytes())
    return {s.qualname: s for s in module.symbols}


def test_function_signatures_keep_defaults_annotations_and_markers() -> None:
    syms = symbols("shapes.py")
    assert syms["area"].signature == "def area(shape: Circle, /, precision: int = 2) -> float"
    assert syms["area"].kind == "function"
    assert syms["area"].doc == "Area of a shape, rounded."
    fetch = syms["fetch_shapes"]
    assert fetch.is_async
    assert fetch.signature == (
        "async def fetch_shapes(source: str, *sources: str, timeout: float | None = None, "
        "**options: object) -> list[Circle]"
    )


def test_classes_methods_and_decorators() -> None:
    syms = symbols("shapes.py")
    circle = syms["Circle"]
    assert (circle.kind, circle.signature) == ("class", "class Circle")
    assert circle.decorators == ["dataclass(frozen=True)"]
    assert circle.doc == "A circle with a radius."
    assert (circle.start, circle.line) == (9, 10)  # start includes the decorator

    assert syms["Circle.diameter"].kind == "method"
    assert syms["Circle.diameter"].decorators == ["property"]
    assert syms["Circle.unit"].decorators == ["classmethod"]
    assert syms["Circle.scaled"].signature == (
        "def scaled(self, factor: float = 2.0, *, clamp: bool = False) -> Circle"
    )
    assert syms["Circle.Meta"].kind == "class"
    assert syms["Circle.Meta.describe"].kind == "method"


def test_nested_functions_are_not_indexed() -> None:
    assert "Circle.scaled.helper" not in symbols("shapes.py")
    assert all(s.name != "helper" for s in symbols("shapes.py").values())


def test_module_docstring_and_literal_all() -> None:
    module = parse_module("__init__.py", (FIXTURE / "__init__.py").read_bytes())
    assert module.doc == "Sample package used by the test suite."
    assert module.all == ["Circle", "area"]
    # __all__ wins over the underscore convention: not_exported is public but unlisted.
    assert [s.name for s in module.exported()] == []  # Circle/area are re-exports, not defined


def test_exported_without_all_uses_underscore_convention() -> None:
    module = parse_module("m.py", b"def a(): ...\ndef _b(): ...\nclass C:\n    def m(self): ...\n")
    assert [s.name for s in module.exported()] == ["a", "C"]


def test_imports_are_recorded_raw_and_future_is_skipped() -> None:
    module = parse_module("util/io.py", (FIXTURE / "util" / "io.py").read_bytes())
    assert [i.raw for i in module.imports] == [
        "import json",
        "import sample_pkg.shapes",
        "from .. import shapes",
        "from ..shapes import Circle",
    ]
    shapes = parse_module("shapes.py", (FIXTURE / "shapes.py").read_bytes())
    assert "__future__" not in [i.module for i in shapes.imports]


def test_lazy_imports_inside_functions_are_found() -> None:
    module = parse_module("m.py", b"def f():\n    import os\n    return os\n")
    assert [(i.raw, i.line) for i in module.imports] == [("import os", 2)]


def test_definitions_under_if_and_try_are_indexed() -> None:
    src = b"""\
import sys
if sys.version_info >= (3, 12):
    def new(): ...
else:
    def old(): ...
try:
    from fast import impl
except ImportError:
    def impl(): ...
"""
    names = [s.name for s in parse_module("m.py", src).symbols]
    assert names == ["new", "old", "impl"]


def test_class_signature_with_bases_and_keywords() -> None:
    src = b"class A(Base, Generic[T], metaclass=Meta, **extra): ...\n"
    assert parse_module("m.py", src).symbols[0].signature == (
        "class A(Base, Generic[T], metaclass=Meta, **extra)"
    )


def test_syntax_error_is_recorded_not_raised() -> None:
    module = parse_module("bad.py", b"def ok():\n    pass\n\ndef broken(:\n")
    assert module.error is not None
    assert module.error.startswith("SyntaxError")
    assert "line 4" in module.error
    assert module.symbols == [] and module.imports == []
    assert len(module.sha256) == 64
