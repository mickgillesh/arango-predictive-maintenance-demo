"""
Retriever proxy — calls the ArangoDB Custom Retriever (query_type=4) with context
derived from a work order (engine model + degrading subsystems) and returns the most
relevant Airworthiness Directives and Service Bulletins for the technician.

Configuration (env vars):
  RETRIEVER_URL      — full base URL of the retriever service, e.g.:
                       https://<endpoint>:8529/graphrag/retriever/<service_id>/v1
  RETRIEVER_TOOL_ID  — _key of the tool document in technicians_documentation_Tools collection
                       (created by scripts/setup_retriever.py), default: aerofleet_techdocs_v1

Authentication is handled automatically using ARANGO_URL / ARANGO_USER / ARANGO_PASSWORD
to fetch a JWT from /_open/auth. No separate RETRIEVER_AUTH env var is needed.

If RETRIEVER_URL is not set the endpoint returns {"docs": [], "configured": false}.

API reference: https://docs.arango.ai/agentic-ai-suite/retriever/search-methods/custom-retriever/
"""

import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.aql import Q_ENGINE_MODEL_FOR_WO
from backend.db import get_db

log = logging.getLogger(__name__)

router = APIRouter()

_RETRIEVER_URL     = os.environ.get("RETRIEVER_URL", "").strip().rstrip("/")
_RETRIEVER_TOOL_ID = os.environ.get("RETRIEVER_TOOL_ID", "aerofleet_techdocs_v1").strip()

_ARANGO_URL  = os.environ.get("ARANGO_URL", "").strip().rstrip("/")
_ARANGO_USER = os.environ.get("ARANGO_USER", "")
_ARANGO_PASS = os.environ.get("ARANGO_PASSWORD", "")

_TOP_K = 5

# Simple in-process JWT cache — refreshed 60 s before expiry (ArangoDB JWTs last ~1 h)
_jwt_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


async def _get_jwt() -> str:
    now = time.time()
    if _jwt_cache["token"] and now < _jwt_cache["expires_at"]:
        return _jwt_cache["token"]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_ARANGO_URL}/_open/auth",
            json={"username": _ARANGO_USER, "password": _ARANGO_PASS},
        )
        resp.raise_for_status()
        token = resp.json()["jwt"]

    _jwt_cache["token"] = token
    _jwt_cache["expires_at"] = now + 55 * 60  # refresh 5 min before the 1-hour expiry
    return token


def _doc_type_from_filename(filename: str) -> str:
    name = (filename or "").split("/")[-1]
    if name.startswith("AD-"):
        return "AD"
    if name.startswith("SB-"):
        return "SB"
    return "AD"


def _parse_content_fields(content: str) -> dict[str, str | None]:
    """Extract issuer and compliance interval from the markdown header block."""
    out: dict[str, str | None] = {}
    for key, pattern in [
        ("issuer",            r"\*\*(?:Issuer|Recommended By):\*\*\s*(.+)$"),
        ("complianceInterval",r"\*\*(?:Compliance Interval|Recommended Interval):\*\*\s*(.+)$"),
    ]:
        m = re.search(pattern, content, re.MULTILINE)
        out[key] = m.group(1).strip() if m else None
    return out


def _parse_doc(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one AQL result node to the TechnicalDoc schema.

    The AQL template returns: { content, title, filename, doc_meta }
    where doc_meta is the technicians_documentation_Documents record for the source file.
    """
    content: str = raw.get("content") or ""
    title: str   = raw.get("title") or ""
    filename: str = raw.get("filename") or ""
    doc_meta: dict[str, Any] = raw.get("doc_meta") or {}

    if not filename:
        filename = doc_meta.get("filename") or doc_meta.get("source") or ""
    if not title:
        title = doc_meta.get("title") or ""

    stem = filename.split("/")[-1].removesuffix(".md").removesuffix(".txt")
    doc_type = _doc_type_from_filename(filename)

    doc_number = stem
    display_title = title
    if title and " — " in title:
        parts = title.split(" — ", 1)
        doc_number    = parts[0].strip()
        display_title = parts[1].strip()
    elif not display_title:
        display_title = stem

    parsed = _parse_content_fields(content)

    return {
        "docNumber":          doc_number or "—",
        "title":              display_title or "—",
        "docType":            doc_type,
        "issuer":             parsed.get("issuer") or "—",
        "affectedSubsystems": [],
        "complianceInterval": parsed.get("complianceInterval"),
        "mandatoryActions":   None,
        "content":            content[:400] if content else "",
        "relevanceScore":     raw.get("score") or raw.get("_score"),
    }


def _extract_nodes(data: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    for key in ("context", "results", "citations", "documents"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


@router.get("/docs")
async def get_technicians_documentation(wo_key: str = Query(..., alias="woKey")) -> JSONResponse:
    if not _RETRIEVER_URL:
        return JSONResponse({"docs": [], "configured": False})

    db = get_db()
    rows = list(db.aql.execute(Q_ENGINE_MODEL_FOR_WO, bind_vars={"wo_key": wo_key}))
    if not rows or rows[0] is None:
        return JSONResponse({"docs": [], "configured": True, "error": "work order not found"})

    ctx = rows[0]
    engine_model: str     = ctx.get("model") or ""
    subsystems: list[str] = ctx.get("driverSubsystems") or []

    if not engine_model and not subsystems:
        return JSONResponse({"docs": [], "configured": True,
                             "engineModel": engine_model, "subsystems": subsystems})

    query = (
        f"{engine_model} {' '.join(subsystems)} "
        "maintenance inspection requirements compliance airworthiness"
    ).strip()

    try:
        jwt = await _get_jwt()
    except Exception as exc:
        log.warning("Failed to obtain JWT for retriever: %s", exc)
        return JSONResponse({"docs": [], "configured": True, "error": "auth_failed"}, status_code=502)

    payload: dict[str, Any] = {
        "query":               query,
        "query_type":          4,
        "custom_tools":        [_RETRIEVER_TOOL_ID],
        "auto_create_indexes": True,
    }
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_RETRIEVER_URL}/graphrag-query",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning("Retriever API %s: %s", exc.response.status_code, exc.response.text[:200])
        return JSONResponse({"docs": [], "configured": True,
                             "error": f"retriever_http_{exc.response.status_code}"}, status_code=502)
    except Exception as exc:
        log.warning("Retriever API call failed: %s", exc)
        return JSONResponse({"docs": [], "configured": True, "error": "retriever_unavailable"}, status_code=502)

    nodes = _extract_nodes(data)
    docs  = [_parse_doc(n) for n in nodes[:_TOP_K * 2] if isinstance(n, dict)]

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for d in docs:
        key = d["docNumber"]
        if key not in seen:
            seen.add(key)
            unique.append(d)
        if len(unique) == _TOP_K:
            break

    return JSONResponse({
        "docs":        unique,
        "configured":  True,
        "engineModel": engine_model,
        "subsystems":  subsystems,
    })
