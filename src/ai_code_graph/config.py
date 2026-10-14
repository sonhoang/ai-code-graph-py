"""Project configuration and file discovery.

Configuration lives in the scanned project's own ``pyproject.toml``::

    [tool.ai-code-graph]
    include = ["src/**/*.py"]
    exclude = ["tests/fixtures/**"]
    output = ".ai-code-graph"

Every key is optional.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_OUTPUT = ".ai-code-graph"

# Directory names never worth descending into. Pruning during the walk (rather than
# filtering afterwards) is what keeps a scan of a repo with a large .venv fast.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        ".tox",
        ".nox",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    root: Path
    include: tuple[str, ...] = ("**/*.py",)
    exclude: tuple[str, ...] = ()
    output: str = DEFAULT_OUTPUT

    @property
    def output_dir(self) -> Path:
        return self.root / self.output

    @classmethod
    def load(cls, root: Path) -> Config:
        root = root.resolve()
        pyproject = root / "pyproject.toml"
        table: dict[str, object] = {}
        if pyproject.is_file():
            with pyproject.open("rb") as fh:
                table = tomllib.load(fh).get("tool", {}).get("ai-code-graph", {})
        unknown = set(table) - {"include", "exclude", "output"}
        if unknown:
            raise ConfigError(f"unknown [tool.ai-code-graph] keys: {', '.join(sorted(unknown))}")
        defaults = cls(root=root)
        return cls(
            root=root,
            include=_patterns(table.get("include", defaults.include), "include"),
            exclude=_patterns(table.get("exclude", defaults.exclude), "exclude"),
            output=str(table.get("output", defaults.output)),
        )


def discover(config: Config) -> list[str]:
    """Sorted posix paths (relative to the root) of the Python files to index."""
    root = config.root
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIRS
            and not d.endswith(".egg-info")
            and here / d != config.output_dir
            and not (here / d / "pyvenv.cfg").exists()  # any virtualenv, whatever its name
        )
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            rel = (here / filename).relative_to(root).as_posix()
            if matches_any(rel, config.include) and not matches_any(rel, config.exclude):
                found.append(rel)
    return sorted(found)


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_glob_regex(p).fullmatch(path) for p in patterns)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob: ``**`` spans directories, ``*`` and ``?`` do not.

    ``fnmatch`` is not used because its ``*`` also matches ``/``, and ``**/*.py``
    would then fail to match top-level files.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _patterns(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"[tool.ai-code-graph] {key} must be a list of glob strings")
    return tuple(value)
