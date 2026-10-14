from types import SimpleNamespace
from typing import Any

import pytest

from ai_code_graph.review import DEFAULT_MODEL, ReviewError, build_prompt, run_review


class FakeMessages:
    def __init__(self, stop_reason: str = "end_turn", text: str = "## Architecture\nFine.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
        )

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.response


def fake_client(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(**kwargs))


def test_review_sends_prompt_and_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACG_MODEL", raising=False)
    client = fake_client()
    out = run_review("<code_map>x</code_map>", client=client)
    call = client.messages.calls[0]
    assert call["model"] == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert call["messages"] == [{"role": "user", "content": "<code_map>x</code_map>"}]
    assert "structural map" in call["system"]
    assert out.startswith(f"<!-- ai-code-graph review | model: {DEFAULT_MODEL} -->")
    assert out.rstrip().endswith("Fine.")


def test_model_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACG_MODEL", "claude-opus-5-5")
    client = fake_client()
    run_review("p", client=client)
    assert client.messages.calls[0]["model"] == "claude-opus-5-5"


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ReviewError, match="ANTHROPIC_API_KEY is not set"):
        run_review("p")


def test_refusal_is_an_error() -> None:
    with pytest.raises(ReviewError, match="declined"):
        run_review("p", client=fake_client(stop_reason="refusal", text=""))


def test_prompt_includes_diff_and_rejects_oversized_input() -> None:
    prompt = build_prompt("# Code map", "diff --git a/x b/x")
    assert prompt.index("<code_map>") < prompt.index("<git_diff>")
    assert "<git_diff>" not in build_prompt("# Code map", None)
    with pytest.raises(ReviewError, match="limit"):
        build_prompt("x" * 500_000, None)
