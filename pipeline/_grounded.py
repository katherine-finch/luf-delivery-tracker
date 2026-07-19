"""Shared helpers for the grounded one-off writers (describe, retrospective).

Both stages do the same thing structurally: build the LLM + Tavily clients, run
one web search per project, and hand the model a numbered block of snippets to
summarise. Only the query, prompt, and output column differ. Those common pieces
live here so the two modules stay thin.
"""

from __future__ import annotations

from .agent import _build_chat_model
from .retrieve import get_client as _get_tavily_client


class GroundedError(RuntimeError):
    """Raised when a search fails or the model/search backend is unavailable."""


def get_backends():
    """Return the (chat model, tavily client) pair both writers need."""
    return _build_chat_model(), _get_tavily_client()


def search_context(tavily, query: str, max_results: int = 5) -> str:
    """Return a numbered block of search snippets to ground a summary."""
    try:
        response = tavily.search(query=query, max_results=max_results, search_depth="basic")
    except Exception as exc:  # pragma: no cover - network guard
        raise GroundedError(f"Search failed for query {query!r}: {exc}") from exc
    results = response.get("results", [])
    return "\n\n".join(
        f"[{i}] {r.get('title', '').strip()}\n{r.get('content', '').strip()}"
        for i, r in enumerate(results, start=1)
    )
