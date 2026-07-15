"""Tavily search client factory for the classification agent.

The ReAct agent (agent.py) drives its own iterative searches through the
`web_search` tool; this module just centralises how we construct and validate
the Tavily client so the API-key handling lives in one place.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class RetrievalConfigError(RuntimeError):
    """Raised when Tavily is not configured."""


def get_client():
    """Return a configured Tavily client, or raise with an actionable message."""
    try:
        from tavily import TavilyClient
    except ImportError as exc:  # pragma: no cover - import guard
        raise RetrievalConfigError(
            "The 'tavily-python' package is required. Install it with: "
            "pip install tavily-python"
        ) from exc

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or api_key.startswith("your-"):
        raise RetrievalConfigError(
            "TAVILY_API_KEY is not set. Add it to your .env file (see .env.example)."
        )
    return TavilyClient(api_key=api_key)
