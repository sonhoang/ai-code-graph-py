from ai_code_graph.model import Graph, Import, Module, module_name


def by_raw(module: Module) -> dict[str, Import]:
    return {imp.raw: imp for imp in module.imports}


def test_module_names_follow_package_dirs(graph: Graph) -> None:
    names = {m.path: m.name for m in graph.modules.values()}
    assert names == {
        "sample_pkg/__init__.py": "sample_pkg",
        "sample_pkg/shapes.py": "sample_pkg.shapes",
        "sample_pkg/util/__init__.py": "sample_pkg.util",
        "sample_pkg/util/io.py": "sample_pkg.util.io",
    }


def test_src_layout_and_scripts() -> None:
    packages = {"src/pkg", "src/pkg/sub"}
    assert module_name("src/pkg/sub/mod.py", packages) == "pkg.sub.mod"
    assert module_name("src/pkg/__init__.py", packages) == "pkg"
    assert module_name("scripts/tool.py", packages) == "tool"


def test_import_resolution(graph: Graph) -> None:
    imports = by_raw(graph.modules["sample_pkg/util/io.py"])
    assert imports["import json"].resolved == []
    assert imports["import sample_pkg.shapes"].resolved == ["sample_pkg/shapes.py"]
    assert imports["from ..shapes import Circle"].target == "sample_pkg.shapes"
    assert imports["from ..shapes import Circle"].resolved == ["sample_pkg/shapes.py"]
    # `from .. import shapes` loads the package and, since shapes is a submodule, shapes too.
    assert imports["from .. import shapes"].resolved == [
        "sample_pkg/__init__.py",
        "sample_pkg/shapes.py",
    ]


def test_relative_import_from_package_init(graph: Graph) -> None:
    imports = by_raw(graph.modules["sample_pkg/__init__.py"])
    assert imports["from .shapes import Circle, area"].resolved == ["sample_pkg/shapes.py"]


def test_imported_by_is_the_reverse_view(graph: Graph) -> None:
    assert graph.modules["sample_pkg/shapes.py"].imported_by == [
        "sample_pkg/__init__.py",
        "sample_pkg/util/io.py",
    ]
    assert graph.modules["sample_pkg/util/io.py"].imported_by == []


def test_relative_import_above_top_level_is_unresolved() -> None:
    graph = Graph({"a.py": Module(path="a.py", sha256="0", imports=[Import(1, "x", level=3)])})
    graph.link()
    assert graph.modules["a.py"].imports[0].resolved == []


def test_round_trip_through_json_dict(graph: Graph) -> None:
    again = Graph.from_dict(graph.to_dict())
    assert again.to_dict() == graph.to_dict()
