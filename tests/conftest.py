import shutil
from pathlib import Path

import pytest

from ai_code_graph import indexer
from ai_code_graph.config import Config
from ai_code_graph.model import Graph

FIXTURE = Path(__file__).parent / "fixtures" / "sample_pkg"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fresh copy of the fixture package, so tests can edit files freely."""
    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root / "sample_pkg", ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.fixture
def config(project: Path) -> Config:
    return Config.load(project)


@pytest.fixture
def graph(config: Config) -> Graph:
    return indexer.create(config)[0]
