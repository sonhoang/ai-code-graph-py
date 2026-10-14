from pathlib import Path

import pytest

from ai_code_graph import indexer, parser
from ai_code_graph.config import Config


@pytest.fixture
def parse_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which files get (re)parsed."""
    calls: list[str] = []

    def spy(path: str, data: bytes):
        calls.append(path)
        return parser.parse_module(path, data)

    monkeypatch.setattr(indexer, "parse_module", spy)
    return calls


def test_create_writes_the_three_outputs(config: Config) -> None:
    graph, report = indexer.create(config)
    assert report.full_rebuild and len(report.added) == 4
    out = config.output_dir
    assert sorted(p.name for p in out.iterdir()) == ["graph.json", "meta.json", "overview.md"]
    meta = indexer.read_meta(config)
    assert meta is not None
    assert meta["files"]["sample_pkg/shapes.py"] == graph.modules["sample_pkg/shapes.py"].sha256
    assert "### sample_pkg.shapes (`sample_pkg/shapes.py`)" in (out / "overview.md").read_text()


def test_sync_without_changes_parses_nothing(config: Config, parse_calls: list[str]) -> None:
    indexer.create(config)
    meta_mtime = (config.output_dir / "meta.json").stat().st_mtime_ns
    parse_calls.clear()
    _, report = indexer.sync(config)
    assert parse_calls == []
    assert (config.output_dir / "meta.json").stat().st_mtime_ns == meta_mtime  # not rewritten
    assert (report.added, report.changed, report.removed, report.unchanged) == ([], [], [], 4)


def test_sync_add_change_delete(project: Path, config: Config, parse_calls: list[str]) -> None:
    indexer.create(config)
    parse_calls.clear()

    (project / "sample_pkg" / "extra.py").write_text("def triangle(sides: int = 3) -> int: ...\n")
    shapes = project / "sample_pkg" / "shapes.py"
    shapes.write_text(shapes.read_text().replace("def area(", "def surface_area("))
    (project / "sample_pkg" / "util" / "io.py").unlink()

    graph, report = indexer.sync(config)

    assert report.added == ["sample_pkg/extra.py"]
    assert report.changed == ["sample_pkg/shapes.py"]
    assert report.removed == ["sample_pkg/util/io.py"]
    assert report.unchanged == 2
    assert sorted(parse_calls) == ["sample_pkg/extra.py", "sample_pkg/shapes.py"]

    names = {s.qualname for _, s in graph.symbols()}
    assert {"triangle", "surface_area"} <= names
    assert "area" not in names and "dump_circle" not in names
    # The reverse view is rebuilt: the deleted module no longer imports shapes.
    assert graph.modules["sample_pkg/shapes.py"].imported_by == ["sample_pkg/__init__.py"]
    # And what was written matches what was returned.
    assert indexer.load(config).to_dict() == graph.to_dict()


def test_new_module_resolves_imports_in_unchanged_files(
    project: Path, config: Config, parse_calls: list[str]
) -> None:
    (project / "sample_pkg" / "uses_later.py").write_text("from .later import thing\n")
    graph, _ = indexer.create(config)
    assert graph.modules["sample_pkg/uses_later.py"].imports[0].resolved == []

    (project / "sample_pkg" / "later.py").write_text("thing = 1\n")
    parse_calls.clear()
    graph, _ = indexer.sync(config)
    # Only the new file was parsed, but linking is global, so the old import now resolves.
    assert parse_calls == ["sample_pkg/later.py"]
    assert graph.modules["sample_pkg/uses_later.py"].imports[0].resolved == ["sample_pkg/later.py"]


def test_tool_version_change_forces_full_rebuild(
    config: Config, parse_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    indexer.create(config)
    monkeypatch.setattr(indexer, "__version__", "99.0")
    parse_calls.clear()
    _, report = indexer.sync(config)
    assert report.full_rebuild and len(parse_calls) == 4


def test_syntax_error_is_tolerated_and_recovers(project: Path, config: Config) -> None:
    broken = project / "sample_pkg" / "broken.py"
    broken.write_text("def oops(:\n    pass\n")
    graph, _ = indexer.create(config)
    assert graph.modules["sample_pkg/broken.py"].error is not None
    assert len(graph.modules) == 5  # the rest of the package is still indexed
    assert "## Files that failed to parse" in (config.output_dir / "overview.md").read_text()

    broken.write_text("def oops():\n    pass\n")
    graph, report = indexer.sync(config)
    assert report.changed == ["sample_pkg/broken.py"]
    assert graph.modules["sample_pkg/broken.py"].error is None


def test_discovery_skips_venvs_caches_and_output(project: Path, config: Config) -> None:
    for junk in (".venv/lib/x.py", "env2/lib/y.py", "sample_pkg/__pycache__/z.py", "build/b.py"):
        (project / junk).parent.mkdir(parents=True, exist_ok=True)
        (project / junk).write_text("def junk(): ...\n")
    (project / "env2" / "pyvenv.cfg").write_text("home = /usr/bin\n")  # a venv with any name
    indexer.create(config)
    (config.output_dir / "stray.py").write_text("def junk(): ...\n")
    graph, _ = indexer.sync(config)
    assert all(
        not m.path.endswith(("x.py", "y.py", "z.py", "b.py", "stray.py"))
        for m in graph.modules.values()
    )


def test_config_include_exclude_and_output(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        '[tool.ai-code-graph]\nexclude = ["sample_pkg/util/**"]\noutput = "map"\n'
    )
    config = Config.load(project)
    graph, _ = indexer.create(config)
    assert sorted(graph.modules) == ["sample_pkg/__init__.py", "sample_pkg/shapes.py"]
    assert (project / "map" / "graph.json").is_file()
