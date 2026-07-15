"""Step 3 -- classify each project's delivery status with a LangGraph ReAct agent.

For every project the agent is given a `web_search` tool and told to research the
project's current delivery status. It loops -- reason about what it still doesn't
know -> issue a more targeted search -> repeat -- until it can justify a status
or hits a search budget (`AGENT_MAX_SEARCHES`, default 5). It then returns a
structured verdict citing *every* source it relied on.

Central honesty rules (enforced in code, not just the prompt):
* The agent may only cite a URL that its search tool actually returned. Any
  citation outside that set is dropped, so every finding traces to a real,
  retrieved source.
* A concrete status must carry at least one valid citation; otherwise it is
  forced to ``unknown``. The agent never guesses.

The status categories and confidence tiers match the rubric documented in the
README ("How we gauge delivery status"), so predictions and the hand-labelled
ground truth are judged on one definition.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

from dotenv import load_dotenv

from .retrieve import get_client as _get_tavily_client

load_dotenv()

STATUSES = ("on_track", "delayed", "stalled", "rescoped", "cancelled", "completed")
CONFIDENCES = ("high", "med", "low")

_DEFAULT_MAX_SEARCHES = int(os.getenv("AGENT_MAX_SEARCHES", "5"))
_DEFAULT_MAX_RESULTS = 5

_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_DEFAULT_BEDROCK_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

# The agent's brief. Status signals + confidence tiers mirror the README rubric.
_AGENT_SYSTEM = """\
You are a careful public-policy analyst tracking the DELIVERY MOMENTUM of UK
Levelling Up Fund capital projects from public text -- NOT their value for money.

You have a `web_search` tool. Use it to gather evidence about the named
project's current delivery status. Search iteratively: after each result, decide
whether you have enough to classify, or whether a more specific query (about
construction start, opening, delays, or scope changes) would help. Stop
searching as soon as you can justify a status, and do not exceed your search
budget. Label on observable EVENTS, not sentiment ("council remains committed"
is not evidence; "construction began" is).

STATUS options and the signals that justify each:
- on_track:  construction/site works verifiably underway; no adverse scope or
             schedule news. (Delivery is moving, NOT necessarily on the original
             timeline.)
- delayed:   work is happening BUT explicit slippage ("extended to 2028").
- stalled:   funded but NO evidence of physical progress; "paused", "on hold".
- rescoped:  scope materially changed; "scaled back", element dropped.
- cancelled: "scrapped", "withdrawn", "returned funding".
- completed: "opened", "complete", a ribbon-cutting or reopening event.
- unknown:   the evidence does not support a decision. Use this rather than guess.

CONFIDENCE:
- high: a dated, physical event from a primary/official source.
- med:  credible progress but partly the body's own framing.
- low:  only forward-looking language ("progress expected in 2025").

CITATIONS: list EVERY source that informed your verdict. For each, give the
exact URL (from a web_search result) and a short paraphrase of the specific
finding that URL supports -- so a reader can see WHERE each fact came from.

When finished, reply with ONLY a JSON object (no prose, no tool call) with keys:
{
  "status": one of the status options,
  "confidence": "high" | "med" | "low",
  "justification": one sentence (<= 30 words) tying the evidence to the status,
  "citations": [
    {"source_url": "<exact URL returned by web_search>",
     "finding": "<short paraphrase (<= 25 words) of what THIS source shows>"}
  ]
}
Every source_url MUST be one returned by web_search. If status is "unknown",
set confidence to "low" and return an empty citations list.
"""

_AGENT_TASK = (
    "PROJECT: {project} (lead body: {council})\n\n"
    "Research this project's current delivery status and classify it."
)

# Sent when the agent uses up its search budget before volunteering a verdict.
# It forces a conclusion from the evidence already gathered -- no more searching --
# so the research isn't wasted. The same citation rules still apply.
_FINALIZE_INSTRUCTION = (
    "You have reached your search budget -- do NOT search again. Based ONLY on "
    "the evidence you have already gathered above, reply now with ONLY the JSON "
    "verdict described earlier (status, confidence, justification, citations). "
    "Cite only URLs that appeared in your search results. If the evidence is "
    "genuinely insufficient, return status \"unknown\" with an empty citations list."
)


class AgentConfigError(RuntimeError):
    """Raised when LangGraph / LangChain backends are not installed or configured."""


@dataclass
class Classification:
    """The pipeline's prediction for one project.

    ``citations`` is a list of ``{"source_url", "finding"}`` dicts -- one per
    source the agent relied on. It is serialised to a JSON string in the CSV so
    the dashboard can render each finding next to its source link.
    """

    project_name: str
    council: str
    status: str
    confidence: str
    justification: str
    citations: list
    model: str
    backend: str

    def to_row(self) -> dict:
        row = asdict(self)
        row["citations"] = json.dumps(self.citations, ensure_ascii=False)
        return row


def _parse_response(text) -> dict:
    """Extract the JSON verdict from the model reply, tolerating stray prose."""
    if isinstance(text, list):  # some models return a list of content blocks
        text = "".join(block.get("text", "") for block in text if isinstance(block, dict))
    text = str(text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _build_chat_model():
    """Return a LangChain chat model for the configured backend (temp=0).

    Backend is chosen by ``LLM_BACKEND`` in .env:
    * ``anthropic`` (default) -- personal Anthropic API key. Zero-setup path.
    * ``bedrock`` -- Claude via Amazon Bedrock using local AWS credentials
      (incl. SSO). Profile, region, model id all come from .env; nothing
      account-specific is committed.
    """
    backend = os.getenv("LLM_BACKEND", "anthropic").strip().lower()

    if backend == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - import guard
            raise AgentConfigError(
                "The anthropic backend needs langchain-anthropic. "
                "Install with: pip install langchain-anthropic"
            ) from exc
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key.startswith("your-"):
            raise AgentConfigError(
                "ANTHROPIC_API_KEY is not set (see .env.example), or switch "
                "LLM_BACKEND=bedrock."
            )
        model = os.getenv("ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)
        return ChatAnthropic(model=model, api_key=api_key, temperature=0.0, max_tokens=1024)

    if backend == "bedrock":
        try:
            from langchain_aws import ChatBedrockConverse
        except ImportError as exc:  # pragma: no cover - import guard
            raise AgentConfigError(
                "The bedrock backend needs langchain-aws. "
                "Install with: pip install langchain-aws boto3"
            ) from exc
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if not region:
            raise AgentConfigError("AWS_REGION is not set for the bedrock backend.")
        model = os.getenv("BEDROCK_MODEL_ID", _DEFAULT_BEDROCK_MODEL)
        profile = os.getenv("AWS_PROFILE")
        kwargs = {"model": model, "region_name": region, "temperature": 0.0}
        if profile:
            # For SSO profiles run `aws sso login --profile <name>` first.
            kwargs["credentials_profile_name"] = profile
        return ChatBedrockConverse(**kwargs)

    raise AgentConfigError(f"Unknown LLM_BACKEND '{backend}'. Use 'anthropic' or 'bedrock'.")


def _make_search_tool(collected_urls: dict, max_results: int):
    """Build a LangChain tool that searches Tavily and records the URLs it returns.

    ``collected_urls`` maps url -> title, so we can (a) validate the agent's
    final citation and (b) know which sources were actually seen.
    """
    from langchain_core.tools import tool

    tavily = _get_tavily_client()

    @tool
    def web_search(query: str) -> str:
        """Search the web for recent news/council coverage about a project.

        Args:
            query: A focused search query, e.g. the project name plus a term
                like 'construction', 'opening', 'delayed', or 'scaled back'.
        Returns:
            A numbered list of results, each with title, URL, and a snippet.
        """
        try:
            response = tavily.search(query=query, max_results=max_results, search_depth="basic")
        except Exception as exc:  # keep the loop alive on transient failures
            return f"Search failed: {exc}"

        results = response.get("results", [])
        if not results:
            return "No results found for that query."

        lines = []
        for i, r in enumerate(results, start=1):
            url = r.get("url", "")
            title = r.get("title", "").strip()
            if url:
                collected_urls[url] = title
            lines.append(f"[{i}] {title}\nURL: {url}\n{r.get('content', '').strip()}")
        return "\n\n".join(lines)

    return web_search


def _clean_citations(raw, collected_urls: dict) -> list:
    """Keep only citations whose URL the search tool actually returned."""
    cleaned = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        finding = str(item.get("finding") or "").strip()
        if url and url in collected_urls:
            cleaned.append({"source_url": url, "finding": finding})
    return cleaned


def _trim_for_finalization(messages: list) -> list:
    """Drop a trailing tool-call turn that never received its results.

    When the loop stops mid-step the last message can be an assistant turn that
    requested a search but never got a ``ToolMessage`` back. Feeding that dangling
    pair to a bare model call breaks Bedrock's tool_use/tool_result validation, so
    we strip such trailing turns and leave a clean history to conclude from.
    """
    trimmed = list(messages)
    while trimmed and getattr(trimmed[-1], "tool_calls", None):
        trimmed.pop()
    return trimmed



def classify_project(
    project_name: str,
    council: str,
    *,
    max_searches: int = _DEFAULT_MAX_SEARCHES,
) -> Classification:
    """Classify one project with a LangGraph ReAct agent that searches in a loop."""
    try:
        from langgraph.prebuilt import create_react_agent
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.errors import GraphRecursionError
        from langchain_core.messages import SystemMessage, HumanMessage
    except ImportError as exc:  # pragma: no cover - import guard
        raise AgentConfigError(
            "The classifier needs langgraph. "
            "Install with: pip install langgraph langchain-core"
        ) from exc

    collected_urls: dict[str, str] = {}
    model = _build_chat_model()
    search_tool = _make_search_tool(collected_urls, _DEFAULT_MAX_RESULTS)
    # A checkpointer lets us recover the gathered messages if the search budget
    # is exhausted, so we can still conclude from the evidence already found.
    agent = create_react_agent(model, tools=[search_tool], checkpointer=InMemorySaver())

    messages = [
        SystemMessage(content=_AGENT_SYSTEM),
        HumanMessage(content=_AGENT_TASK.format(project=project_name, council=council)),
    ]
    # recursion_limit bounds the loop: each search is ~2 steps (call + observe),
    # plus headroom for the final answer. thread_id keys the checkpointed state.
    config = {
        "recursion_limit": max_searches * 2 + 3,
        "configurable": {"thread_id": "classify"},
    }

    backend = os.getenv("LLM_BACKEND", "anthropic").strip().lower()
    model_name = getattr(model, "model", "") or getattr(model, "model_id", "")

    def _unknown(reason: str) -> Classification:
        return Classification(
            project_name=project_name,
            council=council,
            status="unknown",
            confidence="low",
            justification=reason,
            citations=[],
            model=model_name,
            backend=backend,
        )

    try:
        result = agent.invoke({"messages": messages}, config=config)
        final_text = result["messages"][-1].content
    except GraphRecursionError:
        # Budget spent before the agent volunteered a verdict. Rather than throw
        # away its research, ask it to conclude from what it already gathered.
        try:
            state = agent.get_state(config)
            gathered = list(state.values.get("messages", [])) if state and state.values else []
            gathered = _trim_for_finalization(gathered) or list(messages)
            gathered.append(HumanMessage(content=_FINALIZE_INSTRUCTION))
            final_text = model.invoke(gathered).content
        except Exception as exc:
            return _unknown(f"Agent run failed to finalise after budget exhausted: {exc}")
    except Exception as exc:
        return _unknown(f"Agent run failed: {exc}")

    try:
        parsed = _parse_response(final_text)
    except ValueError:
        return _unknown("Agent did not return a parseable JSON verdict.")

    status = str(parsed.get("status", "unknown")).strip().lower()
    if status not in STATUSES and status != "unknown":
        status = "unknown"

    confidence = str(parsed.get("confidence", "low")).strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "low"

    citations = _clean_citations(parsed.get("citations"), collected_urls)

    # Enforce the no-guessing rule: a concrete status must cite a real source.
    if status != "unknown" and not citations:
        status, confidence = "unknown", "low"

    return Classification(
        project_name=project_name,
        council=council,
        status=status,
        confidence=confidence,
        justification=str(parsed.get("justification", "")).strip(),
        citations=citations,
        model=model_name,
        backend=backend,
    )
