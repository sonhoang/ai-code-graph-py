"""Optional LLM review: send the code map (and optionally a git diff) to Claude.

This is the only part of the tool that sends anything off the machine, and it
runs only when you call ``ai-code-graph review``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anthropic

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_DIGEST_CHARS = 400_000  # roughly 100k tokens; well under the context window

SYSTEM_PROMPT = """\
You are reviewing a Python codebase from a structural map produced by a static \
indexer: one line per module, class, function and method, with signatures, first \
docstring lines and import relationships. You do not see function bodies unless a \
git diff is included.

Write a concise review in Markdown with these sections:
1. Architecture: what the main modules do and how they depend on each other.
2. Risks: concrete problems visible from the structure (import cycles, modules that \
too much depends on, inconsistent or surprising signatures, missing docstrings on \
public API). Cite `path:line` for each point.
3. The diff (only if one is included): bugs, regressions and missing tests in the \
changed lines.
4. Suggested next steps, most valuable first.

Only state what the map or diff supports. If something cannot be judged without \
reading the bodies, say so instead of guessing."""


class ReviewError(RuntimeError):
    pass


def git_diff(root: Path, base: str | None) -> str:
    args = ["git", "-C", str(root), "diff", *([base] if base else [])]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        raise ReviewError(f"git diff failed: {stderr.strip()}") from exc
    return result.stdout


def build_prompt(overview: str, diff: str | None) -> str:
    parts = ["<code_map>", overview.strip(), "</code_map>"]
    if diff:
        parts += ["", "<git_diff>", diff.strip(), "</git_diff>"]
    prompt = "\n".join(parts)
    if len(prompt) > MAX_DIGEST_CHARS:
        raise ReviewError(
            f"review input is {len(prompt):,} characters (limit {MAX_DIGEST_CHARS:,}); "
            "narrow the diff or the include globs rather than sending a truncated map"
        )
    return prompt


def run_review(prompt: str, *, model: str | None = None, client: Any | None = None) -> str:
    """Call Claude and return the review text. ``client`` is injectable for tests."""
    model = model or os.environ.get("ACG_MODEL", DEFAULT_MODEL)
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ReviewError(
                "ANTHROPIC_API_KEY is not set. `review` is the only command that calls an API; "
                "every other command works offline."
            )
        client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise ReviewError("the API rejected ANTHROPIC_API_KEY (401)") from exc
    except anthropic.NotFoundError as exc:
        raise ReviewError(f"model {model!r} not found; set ACG_MODEL to a valid model id") from exc
    except anthropic.RateLimitError as exc:
        raise ReviewError("rate limited by the API; retry later") from exc
    except anthropic.APIStatusError as exc:
        raise ReviewError(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ReviewError(f"could not reach the API: {exc}") from exc

    if response.stop_reason == "refusal":
        raise ReviewError("the model declined to review this input")
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens":
        print("warning: the review hit max_tokens and is incomplete", file=sys.stderr)
    return f"<!-- ai-code-graph review | model: {model} -->\n\n{text.strip()}\n"
