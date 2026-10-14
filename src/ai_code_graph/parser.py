"""Turn one Python file into a ``Module`` using the standard-library ``ast``.

The result depends only on the file's bytes, which is what makes caching by
sha256 in ``meta.json`` safe. Names that need other files (dotted module names,
import targets) are filled in later by ``Graph.link()``.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterator

from .model import Import, Module, Symbol

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_module(path: str, data: bytes) -> Module:
    module = Module(path=path, sha256=sha256(data))
    try:
        tree = ast.parse(data, filename=path)
    except (SyntaxError, ValueError) as exc:  # ValueError: e.g. null bytes in source
        line = getattr(exc, "lineno", None)
        msg = getattr(exc, "msg", None) or str(exc)
        module.error = f"{type(exc).__name__}: {msg}" + (f" (line {line})" if line else "")
        return module

    module.doc = _first_doc_line(tree)
    module.all = _literal_all(tree)
    module.symbols = list(_collect_symbols(tree.body, prefix="", in_class=False))
    module.imports = sorted(_collect_imports(tree), key=lambda imp: imp.line)
    return module


def _collect_symbols(body: list[ast.stmt], prefix: str, in_class: bool) -> Iterator[Symbol]:
    """Classes, functions and methods; recurses into class bodies but not function bodies.

    Functions defined inside other functions are implementation details, so they are
    deliberately left out of the index.
    """
    for node in _flatten(body):
        if isinstance(node, ast.ClassDef):
            qualname = prefix + node.name
            yield _symbol(node, "class", qualname, _class_signature(node))
            yield from _collect_symbols(node.body, prefix=qualname + ".", in_class=True)
        elif isinstance(node, FunctionNode):
            kind = "method" if in_class else "function"
            yield _symbol(node, kind, prefix + node.name, _function_signature(node))


def _flatten(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements, looking inside module/class-level ``if``/``try``/``with`` blocks.

    Definitions guarded by ``if sys.version_info >= ...`` or ``try: ... except ImportError``
    are still part of the module's API.
    """
    for node in body:
        if isinstance(node, ast.If):
            yield from _flatten(node.body)
            yield from _flatten(node.orelse)
        elif isinstance(node, ast.Try | ast.TryStar):
            yield from _flatten(node.body)
            for handler in node.handlers:
                yield from _flatten(handler.body)
            yield from _flatten(node.orelse)
            yield from _flatten(node.finalbody)
        elif isinstance(node, ast.With | ast.AsyncWith):
            yield from _flatten(node.body)
        else:
            yield node


def _symbol(node: ast.ClassDef | FunctionNode, kind: str, qualname: str, signature: str) -> Symbol:
    decorators = [ast.unparse(d) for d in node.decorator_list]
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    return Symbol(
        kind=kind,
        name=node.name,
        qualname=qualname,
        signature=signature,
        line=node.lineno,
        start=start,
        end=node.end_lineno or node.lineno,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=decorators,
        doc=_first_doc_line(node),
    )


def _function_signature(node: FunctionNode) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    head = f"{prefix} {node.name}{_type_params(node)}({_format_args(node.args)})"
    if node.returns is not None:
        head += f" -> {ast.unparse(node.returns)}"
    return head


def _format_args(args: ast.arguments) -> str:
    """Parameters in PEP 8 style (``x: int = 1``, ``x=1``), with ``/`` and ``*`` markers.

    ``ast.unparse(args)`` would print ``x: int=1``, which is valid but not how
    anyone writes it, and these lines are meant to be read.
    """

    def param(arg: ast.arg, default: ast.expr | None, star: str = "") -> str:
        text = star + arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}" if arg.annotation else f"={ast.unparse(default)}"
        return text

    positional = args.posonlyargs + args.args
    # `defaults` belong to the *last* positional parameters.
    pos_defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts = [param(a, d) for a, d in zip(positional, pos_defaults, strict=True)]
    if args.posonlyargs:
        parts.insert(len(args.posonlyargs), "/")
    if args.vararg:
        parts.append(param(args.vararg, None, "*"))
    elif args.kwonlyargs:
        parts.append("*")
    parts += [param(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)]
    if args.kwarg:
        parts.append(param(args.kwarg, None, "**"))
    return ", ".join(parts)


def _class_signature(node: ast.ClassDef) -> str:
    args = [ast.unparse(b) for b in node.bases]
    args += [
        f"{k.arg}={ast.unparse(k.value)}" if k.arg else f"**{ast.unparse(k.value)}"
        for k in node.keywords
    ]
    bases = f"({', '.join(args)})" if args else ""
    return f"class {node.name}{_type_params(node)}{bases}"


def _type_params(node: ast.AST) -> str:
    # PEP 695 generics (`def f[T](x: T)`), Python 3.12+. Absent on 3.11.
    params = getattr(node, "type_params", None)
    return f"[{', '.join(ast.unparse(p) for p in params)}]" if params else ""


def _first_doc_line(node: ast.Module | ast.ClassDef | FunctionNode) -> str | None:
    doc = ast.get_docstring(node)
    if not doc:
        return None
    return next((line.strip() for line in doc.splitlines() if line.strip()), None)


def _literal_all(tree: ast.Module) -> list[str] | None:
    """``__all__`` when it is a plain list/tuple of string literals; otherwise None."""
    for node in _flatten(tree.body):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            if isinstance(value, ast.List | ast.Tuple) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
            ):
                return [e.value for e in value.elts]  # type: ignore[attr-defined]
            return None
    return None


def _collect_imports(tree: ast.Module) -> Iterator[Import]:
    """Every import in the file, including lazy imports inside functions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue  # compiler directive, not a dependency
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield Import(line=node.lineno, module=alias.name)
        elif isinstance(node, ast.ImportFrom):
            yield Import(
                line=node.lineno,
                module=node.module or "",
                level=node.level,
                names=[alias.name for alias in node.names],
            )
