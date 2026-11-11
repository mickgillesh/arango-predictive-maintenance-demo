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

import json
import ssl
import textwrap
import time
from collections import Counter
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
    include_metadata: bool = True,
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


def metadata_of(answer: Any) -> Any:
    """The retriever's metadata for an answer (requested with ``include_metadata``).

    The service returns it as a string, usually JSON; it is parsed when possible
    and returned as-is otherwise. ``None`` when the answer carries none.
    """
    raw = answer.get("metadata") if isinstance(answer, dict) else getattr(answer, "metadata", None)
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _clip(text: Any, n: int) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= n else one_line[: n - 1] + "…"


def _cited_title(content: str, n: int = 90) -> str:
    """A label for a cited chunk: its document title, else a heading, else its first words."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    heading = next((ln for ln in lines if ln.startswith("# ")), None) or next(
        (ln for ln in lines if ln.startswith("#")), None)
    return _clip(heading.lstrip("# ") if heading else " ".join(lines), n)


def _collection_counts(items: list[Any]) -> str:
    """'3 Communities, 2 Entities' from ids like '<project>_Communities/<key>'."""
    counts = Counter(str(i).split("/", 1)[0].rsplit("_", 1)[-1] for i in items)
    return ", ".join(f"{n} {name}" for name, n in counts.most_common())


def _graph_value(value: Any) -> str:
    """'3 engines' for a list of ids, 'N items' for other lists, a clipped value otherwise."""
    if isinstance(value, list):
        if value and all(isinstance(v, str) for v in value):
            return _collection_counts(value)
        return f"{len(value)} items"
    return _clip(value, 60)


# Keys summarize_metadata() renders explicitly; anything else is listed by size.
_SUMMARISED = {
    "model", "llm_planner_used", "original_query_type", "completed_steps", "total_steps",
    "iterations", "execution_log", "search_methods_used", "total_results",
    "final_results_count", "status", "final_support_points", "citation_mapping",
    "graph_metadata", "partition_routing",
}


def summarize_metadata(meta: Any, max_items: int = 8) -> list[str]:
    """Readable lines from retriever metadata (the full payload can be 100k+ chars).

    Shapes observed on the platform (2026-11-11):
    - LOCAL: LLM-planner ``execution_log`` (sub-queries and their graph context)
      plus ``citation_mapping``
    - UNIFIED: search statistics plus ``citation_mapping``
    - GLOBAL: map-reduce ``final_support_points`` (scored) and the communities read
    All of them carry ``graph_metadata``, ``partition_routing`` and ``model``.
    Unknown keys are listed with their size rather than dropped.
    """
    if not isinstance(meta, dict):
        return [_clip(meta, 300)]
    out: list[str] = []
    if meta.get("model"):
        out.append(f"model: {meta['model']}")
    if "execution_log" in meta or "llm_planner_used" in meta:
        out.append(
            f"LLM planner: {meta.get('llm_planner_used')} | original query type: "
            f"{meta.get('original_query_type')} | steps {meta.get('completed_steps')}/"
            f"{meta.get('total_steps')} | iterations {meta.get('iterations')}")
        for step in (meta.get("execution_log") or [])[:max_items]:
            if not isinstance(step, dict):
                continue
            ctx = [c for c in step.get("context_data") or [] if isinstance(c, dict)]
            chunks = sum(len(c.get("chunks") or []) for c in ctx)
            out.append(f"  step {step.get('step_id')} [{step.get('status')}] "
                       f"{_clip(step.get('description', ''), 80)}")
            out.append(f"      asked: {_clip(step.get('query', ''), 90)}")
            out.append(f"      context: {len(ctx)} graph nodes, {chunks} chunks")
    if "search_methods_used" in meta or "total_results" in meta:
        out.append(
            f"search: methods {meta.get('search_methods_used') or '-'} | results "
            f"{meta.get('total_results')} -> {meta.get('final_results_count')} | "
            f"status {meta.get('status')}")
    points = [p for p in meta.get("final_support_points") or [] if isinstance(p, dict)]
    if points:
        out.append(f"GLOBAL map-reduce: {len(points)} scored support points")
        for point in points[:max_items]:
            out.append(f"  {point.get('score', '?'):>3}  {_clip(point.get('answer', ''), 90)}")
    cites = meta.get("citation_mapping")
    if isinstance(cites, dict) and cites:
        out.append(f"citations: {len(cites)}")
        for key, cite in list(cites.items())[:max_items]:
            content = cite.get("content", "") if isinstance(cite, dict) else str(cite)
            out.append(f"  [{key}] {_cited_title(content)}")
    graph = meta.get("graph_metadata")
    if isinstance(graph, dict) and graph:
        parts = [f"{k}: {_graph_value(v)}" for k, v in graph.items()]
        out.append("graph: " + " | ".join(parts))
    if meta.get("partition_routing"):
        out.append("partition routing: " + _clip(json.dumps(meta["partition_routing"],
                                                             default=str), 200))
    other = {k: len(json.dumps(v, default=str)) for k, v in meta.items()
             if k not in _SUMMARISED}
    if other:
        out.append("also: " + ", ".join(f"{k} ({n:,} chars)" for k, n in other.items()))
    return out


def show(
    title: str,
    answer: Any,
    width: int = 100,
    max_chars: int = 4000,
    metadata: bool = True,
    raw_metadata_chars: int = 0,
) -> None:
    """Print an answer (SDK response object, REST dict or string) under a heading,
    then a summary of the retriever's metadata. ``raw_metadata_chars`` > 0 also
    prints that much of the raw JSON; ``metadata_of(answer)`` returns all of it."""
    if isinstance(answer, dict):
        text = answer.get("result") or answer.get("error_message") or str(answer)
    else:
        text = getattr(answer, "result", None) or str(answer)
    text = text if len(text) <= max_chars else text[:max_chars] + " …"
    print(f"\n{'=' * width}\n{title}\n{'-' * width}")
    for para in text.split("\n"):
        print(textwrap.fill(para, width) if para.strip() else "")
    meta = metadata_of(answer) if metadata else None
    if meta is None:
        return
    raw = meta if isinstance(meta, str) else json.dumps(meta, indent=1, default=str)
    print(f"{'-' * width}\nmetadata ({len(raw):,} chars; metadata_of(answer) for all)")
    for line in summarize_metadata(meta):
        print(line)
    if raw_metadata_chars > 0:
        print(raw[:raw_metadata_chars] + (" …" if len(raw) > raw_metadata_chars else ""))


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
