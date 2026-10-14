from pathlib import Path

import pytest

from ai_code_graph import cli


def run(root: Path, *args: str) -> int:
    return cli.main(["--root", str(root), *args])


def test_create_sync_search_extract(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "create") == 0
    assert "full scan: 4 added" in capsys.readouterr().out
    assert run(project, "sync") == 0
    assert "incremental: 0 added, 0 changed, 0 removed, 4 unchanged" in capsys.readouterr().out

    assert run(project, "search", "load", "circle") == 0
    assert capsys.readouterr().out.startswith("sample_pkg/util/io.py:15: def load_circle(")

    assert run(project, "extract", "load_circle") == 0
    out = capsys.readouterr().out
    assert out.startswith("# sample_pkg/util/io.py:15-16\ndef load_circle(text: str) -> Circle:")


def test_search_without_matches_exits_1(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(project, "create")
    capsys.readouterr()
    assert run(project, "search", "zzz") == 1
    assert capsys.readouterr().out.strip() == "no matches"


def test_overview_imports_who_imports(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(project, "create")
    capsys.readouterr()
    assert run(project, "overview", "sample_pkg") == 0
    assert "- re-exports: Circle, area" in capsys.readouterr().out
    assert run(project, "imports", "sample_pkg.util.io") == 0
    assert "-> sample_pkg.shapes" in capsys.readouterr().out
    assert run(project, "who-imports", "sample_pkg/shapes.py") == 0
    assert "imported by 2 modules" in capsys.readouterr().out


def test_errors_exit_2_with_a_message(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "search", "circle") == 2
    assert "run `ai-code-graph create` first" in capsys.readouterr().err
    run(project, "create")
    assert run(project, "extract", "missing") == 2
    assert "no symbol named 'missing'" in capsys.readouterr().err
    (project / "pyproject.toml").write_text("[tool.ai-code-graph]\nincludes = []\n")
    assert run(project, "sync") == 2
    assert "unknown [tool.ai-code-graph] keys: includes" in capsys.readouterr().err


def test_review_without_key_fails_cleanly(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run(project, "create")
    assert run(project, "review") == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
    assert not (project / ".ai-code-graph" / "review.md").exists()


def test_review_dry_run_and_mocked_call(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run(project, "create")
    capsys.readouterr()
    assert run(project, "review", "--dry-run") == 0
    assert capsys.readouterr().out.startswith("<code_map>\n# Code map")

    sent: list[str] = []
    monkeypatch.setattr(cli, "run_review", lambda prompt: sent.append(prompt) or "# Review\n")
    assert run(project, "review") == 0
    assert (project / ".ai-code-graph" / "review.md").read_text() == "# Review\n"
    assert "### sample_pkg.shapes" in sent[0]


def test_stats_reports_real_sizes(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(project, "stats", "circle") == 0
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("| `circle` |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[1] == "11"  # hits shown
    assert not (project / ".ai-code-graph").exists()  # stats never writes the index
