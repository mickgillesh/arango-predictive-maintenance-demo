"""
AI-assisted AQL query generation via LangChain ArangoGraphQAChain.

The chain connects directly to ArangoDB, introspects the schema (including the
JSON Schema metadata and named graph edge definitions applied by the loader),
generates AQL with an LLM, executes it, and returns a natural-language answer.

Required env:
  OPENAI_API_KEY  — OpenAI API key

Optional env:
  OPENAI_MODEL    — model name (default: gpt-4o)

Public interface (unchanged from previous txt2aql HTTP implementation):
  async def ask(question: str) -> dict   # {answer, aql, raw, error}
  async def health() -> str              # "ok" | "down" | "not_configured"
"""
import logging
import os
import re

from langchain_arangodb import ArangoGraph, ArangoGraphQAChain
from langchain_openai import ChatOpenAI

from backend.db import get_db

_log = logging.getLogger(__name__)

# Kept for test compatibility — also acts as a second-layer guard alongside
# force_read_only_query=True inside the chain itself.
_MUTATING_RE = re.compile(
    r"\b(INSERT|UPDATE|REPLACE|REMOVE|UPSERT)\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Lazy-initialised chain cache
# ---------------------------------------------------------------------------
_chain: ArangoGraphQAChain | None = None


def _get_chain() -> ArangoGraphQAChain | None:
    global _chain
    if _chain is not None:
        return _chain

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        db = get_db()
        graph = ArangoGraph(db=db)
        llm = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            temperature=0,
            api_key=api_key,
        )
        _chain = ArangoGraphQAChain.from_llm(
            llm=llm,
            graph=graph,
            verbose=False,
            allow_dangerous_requests=True,
            force_read_only_query=True,   # raises ValueError on any write op
            return_aql_query=True,        # puts raw output in result["aql_query"]
            return_aql_result=True,
        )
        return _chain
    except Exception as exc:
        _log.warning("txt2aql: chain init failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

_NOT_CONFIGURED = {
    "answer": (
        "The AI query assistant is not configured. "
        "Set OPENAI_API_KEY in your environment."
    ),
    "aql": None,
    "raw": None,
    "error": "service_not_configured",
}

_SERVICE_DOWN = {
    "answer": "The AI query assistant is currently unavailable. Please try again later.",
    "aql": None,
    "raw": None,
    "error": "service_unavailable",
}

_MUTATING_REFUSED = {
    "answer": (
        "That question produced a query that would modify data. "
        "This demo only surfaces read-only queries."
    ),
    "aql": None,
    "raw": None,
    "error": "mutating_query_refused",
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def ask(question: str) -> dict:
    """Translate a natural-language question to AQL, execute it, return NL + AQL."""
    chain = _get_chain()
    if chain is None:
        return _NOT_CONFIGURED

    try:
        result = await chain.ainvoke({"query": question})
    except ValueError as exc:
        msg = str(exc)
        if "Security violation" in msg or "Write operations are not allowed" in msg:
            return _MUTATING_REFUSED
        _log.warning("txt2aql: chain ValueError: %s", exc)
        return _SERVICE_DOWN
    except Exception as exc:
        _log.warning("txt2aql: chain error: %s", exc)
        return _SERVICE_DOWN

    # Extract natural-language answer (qa_chain returns AIMessage or str)
    nl = result.get("result", "")
    if hasattr(nl, "content"):
        nl = nl.content
    nl = str(nl).strip()

    # Clean AQL is stored as a side-effect on the chain instance
    aql: str | None = getattr(chain, "_last_aql_query", None) or None

    # Second-layer mutation guard (belt-and-suspenders)
    if aql and _MUTATING_RE.search(aql):
        return _MUTATING_REFUSED

    return {
        "answer": nl,
        "aql": aql,
        "raw": {k: str(v) for k, v in result.items()},  # serialise for JSON
        "error": None,
    }


async def health() -> str:
    """Return 'ok', 'down', or 'not_configured'."""
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return "not_configured"
    return "ok" if _get_chain() is not None else "down"
