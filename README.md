# ai-code-graph-py

A Python port of [ai-code-graph](https://github.com/sonhoang/ai-code-graph). The
TypeScript original indexes TypeScript codebases; this version indexes Python
codebases, using the standard-library `ast` module.

It keeps a persistent graph of a codebase (modules, classes, functions, methods
and imports), updates it incrementally by file hash, and answers questions with
one compact line per declaration:

```
$ ai-code-graph search graph link
src/ai_code_graph/model.py:139: def link(self) -> None  # Recompute module names, import targets and the imported_by view.
```

The point is to let an AI assistant find an API without reading whole files. The
same queries are available as a CLI and as an MCP server, so Claude can call them
as tools. An optional `review` command sends the code map to Claude for a
structural review.

The `.ai-code-graph/` folder in this repo is the tool's output for its own source,
committed so you can see what it produces without running anything.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> ai-code-graph-py && cd ai-code-graph-py
uv sync
uv run ai-code-graph --root /path/to/your/project create   # full scan
uv run ai-code-graph --root /path/to/your/project sync     # later: only changed files
```

Or install it as a tool so `ai-code-graph` (alias `acg`) is on your PATH, then
run it from the project directory:

```bash
uv tool install /path/to/ai-code-graph-py
cd /path/to/your/project
acg create
```

Commands:

| Command | What it does |
|---|---|
| `create` | Full scan; writes `graph.json`, `meta.json`, `overview.md` |
| `sync` | Reparses only added or changed files, drops removed ones, prints the counts |
| `search QUERY... [--kind K] [--path P] [--limit N]` | Ranked signature lines. `--kind` is `class`, `function`, `method` or `async`; `--path` is a prefix or glob |
| `extract QUALNAME` | Exact source of one symbol, e.g. `pkg.mod.Class.method` or a unique suffix such as `Class.method` |
| `overview [PACKAGE]` | The table of contents, optionally for one package |
| `imports MODULE` / `who-imports MODULE` | What a module imports / which modules import it, with the import lines |
| `review [--diff [BASE]] [--dry-run]` | Sends the overview (and optionally `git diff`) to Claude, writes `review.md` |
| `stats [QUERY...] [--include GLOB]` | Measures search output against grep and full files (see below) |
| `mcp` | Runs the MCP server on stdio |

Example output:

```
$ acg who-imports ai_code_graph.indexer
ai_code_graph.indexer is imported by 5 modules:
src/ai_code_graph/cli.py:11: from . import __version__, indexer, query
src/ai_code_graph/mcp_server.py:19: from .indexer import GraphNotFound, load
tests/conftest.py:6: from ai_code_graph import indexer
tests/test_query.py:5: from ai_code_graph import indexer, query
tests/test_sync.py:5: from ai_code_graph import indexer, parser

$ acg extract SyncReport.summary
# src/ai_code_graph/indexer.py:39-44
    def summary(self) -> str:
        mode = "full scan" if self.full_rebuild else "incremental"
        ...
```

### Configuration

Optional, in the scanned project's `pyproject.toml`:

```toml
[tool.ai-code-graph]
include = ["**/*.py"]           # default
exclude = ["tests/fixtures/**"] # default: nothing beyond the always-skipped dirs
output = ".ai-code-graph"       # default
```

Globs are matched against paths relative to the root; `**` spans directories and
`*` does not. Virtualenvs (any directory containing `pyvenv.cfg`), `.git`,
`__pycache__`, `build`, `dist`, `node_modules`, `site-packages` and tool caches are
never descended into. Unknown keys are an error rather than silently ignored.

## Using it from Claude via MCP

`ai-code-graph mcp` starts a stdio MCP server with four read-only tools:

| Tool | Arguments | Returns |
|---|---|---|
| `search` | `text`, optional `kind`, `path`, `limit` | `path:line: signature  # docstring` lines |
| `extract` | `qualname` | `path:start-end` and the exact source of one symbol |
| `overview` | optional `package` | The Markdown table of contents |
| `who_imports` | `module` | Importing modules and their import lines |

The server's instructions tell the model to call `search` before grepping or
opening files. Each tool reads `graph.json` on every call, so running `acg sync` in
a terminal is picked up without restarting the server. Build the index once with
`acg create` before connecting.

Claude Code, after `uv tool install` (run in the project directory; `--scope
project` writes a `.mcp.json` you can commit):

```bash
claude mcp add --scope project ai-code-graph -- ai-code-graph mcp
```

which is equivalent to this `.mcp.json`:

```json
{
  "mcpServers": {
    "ai-code-graph": {
      "command": "ai-code-graph",
      "args": ["mcp"]
    }
  }
}
```

Claude Desktop does not start servers in your project directory, so pass the
root explicitly in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ai-code-graph": {
      "command": "uvx",
      "args": [
        "--from", "/absolute/path/to/ai-code-graph-py",
        "ai-code-graph", "--root", "/absolute/path/to/your/project", "mcp"
      ]
    }
  }
}
```

## How it works

```
discover files ──> parse (per file, cached by sha256) ──> link (whole graph) ──> write
  config.py            parser.py                            model.py              indexer.py
```

1. Discover. Walk the root, prune skipped directories, apply include/exclude.
2. Parse. `parser.parse_module(path, bytes)` turns one file into a `Module`: its
   docstring, literal `__all__`, symbols and raw imports. Each symbol records its
   kind, qualified name inside the module, a signature head rebuilt from the AST
   (parameters with annotations and defaults, `/` and `*` markers, return type,
   PEP 695 type parameters, class bases and keywords), decorators, the first
   docstring line, and its line range including decorators. Functions nested in
   functions are skipped; definitions under module- or class-level `if`/`try`/`with`
   are kept. A file that fails to parse is recorded with its error and the scan
   continues.
3. Link. `Graph.link()` computes everything that depends on other files: each
   module's dotted name (walking up while the parent directory has an
   `__init__.py`, so src and flat layouts both work without configuration), the
   absolute target of every import including relative ones, which local modules
   each import loads, and the reverse `imported_by` view.
4. Write. `graph.json` (the full graph, sorted, no absolute paths or
   timestamps), `meta.json` (tool version, time, sha256 per file) and
   `overview.md`.

`sync` hashes every discovered file, reuses the cached `Module` when the hash
matches, reparses the rest, then runs the link step over everything. A new file can
therefore make an import in an unchanged file resolve, without reparsing that file.
If `meta.json` was written by a different tool version, the cache is ignored and
the scan is full. A `sync` with no changes writes nothing.

Code layout, about 1,400 lines including docstrings, in `src/ai_code_graph/`:

| Module | Role |
|---|---|
| `model.py` | Dataclasses (`Symbol`, `Import`, `Module`, `Graph`), the link step, JSON (de)serialisation |
| `parser.py` | `ast` to `Module` |
| `config.py` | `[tool.ai-code-graph]` loading, file discovery, glob matching |
| `indexer.py` | `build`, `create`, `sync`, reading and writing the output folder |
| `query.py` | Search ranking, extract, imports / who-imports |
| `overview.py` | `overview.md` rendering |
| `mcp_server.py` | The MCP tools |
| `review.py` | The optional Claude call |
| `stats.py` | The measurement used below |
| `cli.py` | argparse front end |

## Design decisions and trade-offs

`ast` rather than tree-sitter. The standard library parses exactly the grammar
of the running interpreter, needs no compiled dependency, and gives decorators,
annotations and defaults as structured nodes, so signatures can be rebuilt instead
of sliced out of text. The costs: only Python, it needs syntactically valid
files (a broken file is recorded and skipped rather than partially indexed, which
tree-sitter's error recovery would allow), and the parse uses the grammar of the
Python running the tool, so 3.12 generics in a scanned project need the tool on
3.12+. tree-sitter would be the choice for multiple languages or for indexing
while a file is mid-edit.

Signatures only. Search returns one line per declaration: location, declaration
head, first docstring line. That is enough to choose what to read next; `extract`
then returns one exact definition. Two limits keep lines short on real libraries:
signatures over 200 characters are clipped (`extract` has the full text) and
`@overload` stubs are folded into their implementation. Both were added after
the first measurement on the `mcp` package, where a few `__init__` signatures of
3,000 characters and repeated overloads made `search call_tool` larger than
`grep -rn call_tool`.

Ranking is a fixed table, not embeddings or BM25: per term, an exact name match
scores more than a dotted-suffix match (`Graph.link`), then name prefix, name
substring, qualified name, signature text and docstring; every term must match;
public names get a small bonus. It is easy to predict and to test, and it is
weak on vague natural-language queries.

Read-only MCP. The tools never write. Updating the index is an explicit `sync`
by the user (or a hook), so a model cannot change files through this server, and
tool calls stay cheap and predictable. The tools carry `read_only_hint`.
`extract` refuses to answer when the file's hash no longer matches the graph,
instead of returning lines that have moved.

Incremental by content hash, not mtime. Hashes are correct after checkouts,
rebases and copies that preserve or reset mtimes. The cost is that `sync`
still reads every file: on the 2,288-file `anthropic` package a no-op sync took
about 1.5 s against about 5.8 s for a full scan, measured on a busy laptop. An
mtime-then-hash check would remove most of that read.

Parse and link are separate on purpose. Parsing depends only on a file's bytes,
which is what makes the hash a valid cache key; everything cross-file is
recomputed by a cheap link pass. Caching link results per file would make sync
wrong when files are added or removed.

The review sends only the overview (and the diff if asked), never the source
files. It fails with a clear message when `ANTHROPIC_API_KEY` is missing and
refuses input over 400,000 characters instead of truncating it silently.
`--dry-run` prints exactly what would be sent. The model comes from `ACG_MODEL`
(default `claude-sonnet-5-5`).

The SDK dependency is mcp 2.x, where `FastMCP` was renamed `MCPServer`; the server
code uses `MCPServer` and the tool decorator API, which is otherwise the same as
FastMCP.

## Measured token savings

`ai-code-graph stats` runs a few queries against a freshly built in-memory graph
and compares three outputs for each:

- search: what `search QUERY` returns (at most 20 lines);
- grep -rn: case-insensitive `grep -rn QUERY` over the same files, the usual
  first step for an assistant without an index;
- full files: the complete text of the files that contain the shown hits, which
  is what gets pasted when those files are opened to find the API.

Token counts are characters divided by 4, a rough approximation rather than a
tokenizer count; the character counts behind them are exact. The numbers below
are from real runs on 2026-10-14 (reproduce with the commands shown).

This repository (`uv run acg stats sync extract overview import`), 20 modules,
165 symbols:

| query | hits | search tokens | grep -rn tokens | files | full-file tokens | search / files |
|---|---:|---:|---:|---:|---:|---:|
| `sync` | 18 | 505 | 1,116 | 4 | 5,040 | 10.0% |
| `extract` | 8 | 235 | 694 | 5 | 6,818 | 3.5% |
| `overview` | 9 | 222 | 677 | 5 | 5,106 | 4.4% |
| `import` | 20 | 494 | 4,476 | 10 | 13,766 | 3.6% |
| total | | 1,456 | 6,964 | | 30,729 | 4.7% |

The installed `mcp` 2.3.0 package (`acg --root .venv/lib/python3.12/site-packages
stats call_tool list_tools session stdio --include 'mcp/**/*.py'`), 124 modules,
1,249 symbols:

| query | hits | search tokens | grep -rn tokens | files | full-file tokens | search / files |
|---|---:|---:|---:|---:|---:|---:|
| `call_tool` | 9 | 544 | 800 | 7 | 60,396 | 0.9% |
| `list_tools` | 6 | 256 | 482 | 5 | 53,730 | 0.5% |
| `session` | 20 | 737 | 17,185 | 14 | 100,365 | 0.7% |
| `stdio` | 20 | 691 | 1,323 | 3 | 20,529 | 3.4% |
| total | | 2,228 | 19,790 | | 235,021 | 0.9% |

The installed `anthropic` 1.13.0 package (same command with
`--include 'anthropic/**/*.py'` and queries `stream retry count_tokens batch`),
2,288 modules, 6,183 symbols:

| query | hits | search tokens | grep -rn tokens | files | full-file tokens | search / files |
|---|---:|---:|---:|---:|---:|---:|
| `stream` | 20 | 933 | 102,377 | 9 | 127,625 | 0.7% |
| `retry` | 20 | 856 | 11,708 | 11 | 57,055 | 1.5% |
| `count_tokens` | 7 | 381 | 1,230 | 5 | 89,910 | 0.4% |
| `batch` | 20 | 532 | 18,514 | 9 | 111,919 | 0.5% |
| total | | 2,702 | 133,830 | | 386,509 | 0.7% |

How to read this:

- Against opening the files, search output is 0.4% to 10% of the size. That is
  the case the tool is built for. The full-file column is an upper bound: an
  assistant that reads only a line range spends less.
- Against grep the gain depends on the query. For a common word (`stream`,
  `session`, `retry`, `batch`) grep returns every usage and search is roughly 14 to
  110 times smaller. For a
  rare, exact identifier (`call_tool`, `list_tools`) grep is already small and the
  saving is under 2x.
- Search caps output at 20 lines and grep does not, so for common words part of
  the difference is the cap. The two also answer different questions: search finds
  declarations, grep finds usages. `who-imports` covers module-level usages; call
  sites are not indexed.
- These are output sizes, not end-to-end measurements of an assistant finishing a
  task with and without the tool. That would be the more meaningful number, and it
  needs an evaluation harness and API access, which this repo does not have.

## Limitations and next steps

- Python only, and only syntactically valid files are indexed.
- Static only: no call graph, no type inference, no resolution of names bound at
  runtime (`importlib`, `__getattr__`, star imports are recorded but not expanded).
- Module-level constants and class attributes are not indexed, only classes,
  functions and methods.
- Module names are inferred from `__init__.py` files; namespace packages (no
  `__init__.py`) resolve as top-level modules, and two scripts with the same file
  name in different directories collide (the first one wins for import resolution).
- Search is lexical. Ranking by embeddings or by how often a symbol is imported
  are natural next steps; the latter is cheap since `imported_by` exists.
- `sync` reads every file to hash it; an mtime check first would make no-op syncs
  near-instant on large trees.
- The `review` command is tested with a mocked client only. It has not been run
  against the live API from this repository.
- Next: index class attributes and module constants, add call edges for direct
  calls, an mtime fast path for `sync`, and a small task-level eval (questions
  about a real repo, answered with and without the MCP tools) to replace the size
  comparison above with an end-to-end one.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check && uv run ruff format --check
uv run acg sync   # refresh the committed .ai-code-graph/ after changing src/
```

The tests use a small fixture package in `tests/fixtures/sample_pkg` (copied to a
temporary directory per test). They cover signature reconstruction, import
resolution, incremental sync, ranking, extract exactness, syntax-error tolerance,
the MCP tools (in process and through a real stdio subprocess) and the CLI. Claude
is mocked; the suite needs no API key and no network.
