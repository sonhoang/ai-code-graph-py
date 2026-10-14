"""The graph data model: modules, symbols and imports, plus the global link step.

Parsing (see ``parser.py``) is a pure function of one file's text, so its result
can be cached by content hash. Everything that depends on *other* files - the
module's dotted name, where its imports point, who imports it - is computed here
in ``Graph.link()``, which is cheap and runs over the whole graph after every
create or sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
HEAD_WIDTH = 200  # compact output clips longer signatures; `extract` has the full text
DECORATOR_WIDTH = 40


@dataclass(slots=True)
class Symbol:
    kind: str  # "class" | "function" | "method"
    name: str
    qualname: str  # dotted path inside the module, e.g. "Graph.link"
    signature: str  # declaration head without body, e.g. "def link(self) -> None"
    line: int  # line of the `def` / `class` keyword
    start: int  # first line, including decorators
    end: int  # last line of the body
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)
    doc: str | None = None  # first line of the docstring

    @property
    def is_public(self) -> bool:
        return not any(part.startswith("_") for part in self.qualname.split("."))

    def head(self) -> str:
        """Decorators plus signature, clipped to one readable line."""
        decorators = "".join(f"@{_clip_decorator(d)} " for d in self.decorators)
        sig = self.signature
        if len(sig) > HEAD_WIDTH:
            sig = sig[: HEAD_WIDTH - 4] + " ..."
        return decorators + sig


def _clip_decorator(text: str) -> str:
    # `@deprecated("a long message ...")` -> `@deprecated(...)`
    if len(text) <= DECORATOR_WIDTH or "(" not in text:
        return text
    return text.split("(", 1)[0] + "(...)"


@dataclass(slots=True)
class Import:
    line: int
    module: str  # as written, without leading dots ("" for `from . import x`)
    level: int = 0  # number of leading dots; 0 means absolute
    names: list[str] = field(default_factory=list)  # empty for `import a.b`
    # Filled in by Graph.link():
    target: str = ""  # absolute dotted module name
    resolved: list[str] = field(default_factory=list)  # paths of local modules it loads

    @property
    def raw(self) -> str:
        dotted = "." * self.level + self.module
        if not self.names:
            return f"import {dotted}"
        return f"from {dotted} import {', '.join(self.names)}"


@dataclass(slots=True)
class Module:
    path: str  # posix path relative to the project root
    sha256: str
    doc: str | None = None
    all: list[str] | None = None  # literal __all__, when present
    error: str | None = None  # syntax error message; symbols/imports are then empty
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    # Filled in by Graph.link():
    name: str = ""
    imported_by: list[str] = field(default_factory=list)

    @property
    def is_package(self) -> bool:
        return self.path.endswith("__init__.py")

    @property
    def package(self) -> str:
        """The package that relative imports in this module are resolved against."""
        return self.name if self.is_package else self.name.rpartition(".")[0]

    def exported(self) -> list[Symbol]:
        """Top-level symbols a user of this module is meant to see."""
        top = [s for s in self.symbols if "." not in s.qualname]
        if self.all is not None:
            return [s for s in top if s.name in self.all]
        return [s for s in top if s.is_public]


def module_name(path: str, package_dirs: set[str]) -> str:
    """Dotted name for ``path``, walking up while the parent directory is a package.

    ``src/pkg/sub/mod.py`` becomes ``pkg.sub.mod`` when ``src/pkg`` and
    ``src/pkg/sub`` contain an ``__init__.py`` and ``src`` does not. This handles
    the src layout and flat layouts without any configuration.
    """
    p = PurePosixPath(path)
    parts = [] if p.name == "__init__.py" else [p.stem]
    parent = p.parent
    while parent.name and str(parent) in package_dirs:
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts)) or p.stem


@dataclass
class Graph:
    modules: dict[str, Module] = field(default_factory=dict)  # keyed by path

    # -- queries ---------------------------------------------------------------

    def symbols(self) -> Iterator[tuple[Module, Symbol]]:
        for path in sorted(self.modules):
            module = self.modules[path]
            for symbol in module.symbols:
                yield module, symbol

    def find_module(self, key: str) -> Module | None:
        """Look a module up by path or dotted name."""
        if key in self.modules:
            return self.modules[key]
        return next((m for m in self.modules.values() if m.name == key), None)

    # -- linking ---------------------------------------------------------------

    def link(self) -> None:
        """Recompute module names, import targets and the imported_by view."""
        package_dirs = {
            str(PurePosixPath(p).parent) for p in self.modules if p.endswith("__init__.py")
        }
        by_name: dict[str, str] = {}
        for path in sorted(self.modules):
            module = self.modules[path]
            module.name = module_name(path, package_dirs)
            module.imported_by = []
            # On a name collision (two top-level scripts with the same stem) keep the first.
            by_name.setdefault(module.name, path)

        for path in sorted(self.modules):
            module = self.modules[path]
            for imp in module.imports:
                imp.target = _absolute_target(module, imp)
                imp.resolved = _resolve(imp, by_name)
                for target_path in imp.resolved:
                    importers = self.modules[target_path].imported_by
                    if target_path != path and path not in importers:
                        importers.append(path)

    # -- (de)serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "modules": [asdict(self.modules[p]) for p in sorted(self.modules)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Graph:
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError(f"unsupported graph schema {data.get('schema')!r}")
        graph = cls()
        for raw in data["modules"]:
            raw = dict(raw)
            raw["symbols"] = [Symbol(**s) for s in raw["symbols"]]
            raw["imports"] = [Import(**i) for i in raw["imports"]]
            graph.modules[raw["path"]] = Module(**raw)
        return graph


def _absolute_target(module: Module, imp: Import) -> str:
    if imp.level == 0:
        return imp.module
    base = module.package.split(".") if module.package else []
    # `from . import x` -> package; each extra dot climbs one level.
    keep = len(base) - (imp.level - 1)
    if keep < 0:
        return imp.module  # climbs above the top-level package; leave it unresolved
    prefix = ".".join(base[:keep])
    return ".".join(p for p in (prefix, imp.module) if p)


def _resolve(imp: Import, by_name: dict[str, str]) -> list[str]:
    """Paths of local modules loaded by an import statement."""
    resolved: list[str] = []
    if imp.names:
        # `from pkg import x`: pkg itself, plus pkg.x when x is a submodule.
        if imp.target in by_name:
            resolved.append(by_name[imp.target])
        for name in imp.names:
            sub = f"{imp.target}.{name}" if imp.target else name
            if sub in by_name and by_name[sub] not in resolved:
                resolved.append(by_name[sub])
        return resolved
    # `import a.b.c` binds `a` but loads a.b.c; point at the deepest local match.
    parts = imp.target.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in by_name:
            return [by_name[candidate]]
    return []
