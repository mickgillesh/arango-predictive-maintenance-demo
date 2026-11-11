"""
Helpers for notebooks/aerofleet_autograph_autorag.ipynb.

The AI Suite SDK covers project, secrets, file upload, AutoGraph and AutoRAG
``ask()`` — but ``ask()`` does not accept ``custom_tools`` or
``use_llm_planner``. Custom Retriever and Deep Search queries therefore go to
the Retriever's REST endpoint directly; ``graphrag_query`` below does that.

Query types (from the SDK's retrievers.proto):
    GLOBAL = 1, LOCAL = 2, UNIFIED = 3, CUSTOM = 4
    Deep Search = CUSTOM + use_llm_planner=true (proto alias: mode=DEEP_SEARCH)
"""

from __future__ import annotations

import ssl
import textwrap
import time
from typing import Any

import httpx

from backend.db import tls_verify

GLOBAL, LOCAL, UNIFIED, CUSTOM = 1, 2, 3, 4


def _httpx_verify() -> bool | ssl.SSLContext:
    """``ARANGO_TLS_VERIFY`` in the form httpx expects (a CA path becomes an SSL context)."""
    verify = tls_verify()
    return ssl.create_default_context(cafile=verify) if isinstance(verify, str) else verify

_JWT_TTL_SECONDS = 50 * 60
_jwt_cache: dict[tuple[str, str], tuple[str, float]] = {}


def get_jwt(arango_url: str, username: str, password: str) -> str:
    """Return a cached JWT from ``POST {arango_url}/_open/auth`` (refreshed after 50 min)."""
    key = (arango_url, username)
    cached = _jwt_cache.get(key)
    if cached and time.monotonic() - cached[1] < _JWT_TTL_SECONDS:
        return cached[0]
    resp = httpx.post(f"{arango_url.rstrip('/')}/_open/auth",
                      json={"username": username, "password": password}, timeout=30,
                      verify=_httpx_verify())
    resp.raise_for_status()
    token = resp.json()["jwt"]
    _jwt_cache[key] = (token, time.monotonic())
    return token


def graphrag_query(
    retriever_url: str,
    jwt: str,
    query: str,
    *,
    query_type: int = CUSTOM,
    custom_tools: list[str] | None = None,
    use_llm_planner: bool | None = None,
    auto_select_partitions: bool | None = None,
    auto_create_indexes: bool | None = True,
    include_metadata: bool = False,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """POST a query to ``{retriever_url}/v1/graphrag-query`` and return the JSON body.

    ``retriever_url`` is ``AutoRag.service_url`` from the SDK. Options left as
    ``None`` are omitted so the service default applies.
    """
    body: dict[str, Any] = {"query": query, "query_type": query_type,
                            "include_metadata": include_metadata}
    optional = {"custom_tools": custom_tools, "use_llm_planner": use_llm_planner,
                "auto_select_partitions": auto_select_partitions,
                "auto_create_indexes": auto_create_indexes}
    body.update({k: v for k, v in optional.items() if v is not None})
    resp = httpx.post(f"{retriever_url.rstrip('/')}/v1/graphrag-query", json=body,
                      headers={"Authorization": f"Bearer {jwt}"}, timeout=timeout,
                      verify=_httpx_verify())
    if resp.status_code >= 400:
        raise RuntimeError(f"graphrag-query HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def show(title: str, answer: Any, width: int = 100, max_chars: int = 4000) -> None:
    """Print an answer (SDK response object, REST dict or string) under a heading."""
    if isinstance(answer, dict):
        text = answer.get("result") or answer.get("error_message") or str(answer)
    else:
        text = getattr(answer, "result", None) or str(answer)
    text = text if len(text) <= max_chars else text[:max_chars] + " …"
    print(f"\n{'=' * width}\n{title}\n{'-' * width}")
    for para in text.split("\n"):
        print(textwrap.fill(para, width) if para.strip() else "")


def answer_mentions(answer: Any, *needles: str) -> dict[str, bool]:
    """Which expected facts (case-insensitive substrings) appear in an answer."""
    text = answer.get("result", "") if isinstance(answer, dict) else getattr(answer, "result", "")
    low = (text or "").lower()
    return {n: n.lower() in low for n in needles}


def peek(db: Any, aql: str, bind_vars: dict[str, Any] | None = None, limit: int = 20):
    """Run a read-only AQL query and return the rows as a pandas DataFrame."""
    import pandas as pd
    rows = list(db.aql.execute(aql, bind_vars=bind_vars or {}))
    return pd.json_normalize(rows[:limit]) if rows else pd.DataFrame()
