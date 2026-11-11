"""Custom Retriever tool definitions: offline structure checks + read-only AQL checks."""

import json

import pytest

from backend.aql import Q_TOOL_FLEET_EXPAND, Q_TOOL_KG_ENTITIES, Q_TOOL_TECHDOCS
from pipeline.retriever_tools import (
    FLEET_STATUS_TOOL_ID,
    RETRIEVER_BIND_PARAMS,
    AutographCollections,
    build_tools,
    default_tools_collection,
    discover_autograph_collections,
    template_bind_params,
)

AG = AutographCollections(
    chunks="p_Chunks", documents="p_Documents", relations="p_Relations", entities="p_Entities"
)
TOOLS = build_tools(AG)


def test_all_four_tools_built() -> None:
    assert len(TOOLS) == 4
    assert len({t["tool_id"] for t in TOOLS}) == 4
    assert all(t["_key"] == t["tool_id"] for t in TOOLS)
    assert all(t["tool_type"] == "custom_retriever" for t in TOOLS)


def test_fleet_tools_only_without_autograph() -> None:
    assert [t["config"]["collection"] for t in build_tools(None)] == ["engines", "engines"]


def test_no_partition_id_workaround() -> None:
    """The partition_id tagging workaround is gone from tools and templates."""
    assert "partition_id" not in json.dumps(TOOLS)


def test_no_planner_dependency() -> None:
    """The planner was removed; tools must not depend on planner work orders."""
    assert "generatedByPlanner" not in json.dumps(TOOLS)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["tool_id"])
def test_template_uses_nodes(tool) -> None:
    assert "@nodes" in tool["config"]["aql_config"]["template"]


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["tool_id"])
def test_bind_params_match_template(tool) -> None:
    aql = tool["config"]["aql_config"]
    used = template_bind_params(aql["template"]) - RETRIEVER_BIND_PARAMS
    assert used == set(aql["bind_params"]), tool["tool_id"]


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["tool_id"])
def test_search_modes_valid(tool) -> None:
    modes = tool["config"]["search_modes"]
    assert modes and all(m["type"] in ("lexical", "semantic") for m in modes)


def test_fleet_engine_description_points_to_status_tool() -> None:
    assert FLEET_STATUS_TOOL_ID in TOOLS[2]["description"]


def test_template_bind_params_parser() -> None:
    assert template_bind_params("FOR x IN @@coll FILTER x.a == @a RETURN @b") == {
        "@coll", "a", "b"}
    assert template_bind_params("RETURN 'user@example.com'") == set()


def test_default_tools_collection(monkeypatch) -> None:
    monkeypatch.delenv("TOOLS_COLLECTION", raising=False)
    assert default_tools_collection("proj") == "proj_Tools"
    monkeypatch.setenv("TOOLS_COLLECTION", "Tools")
    assert default_tools_collection("proj") == "Tools"


def test_discover_exact_names() -> None:
    names = ["engines", "p_Chunks", "p_Documents", "p_Relations", "p_Entities", "p_Tools"]
    assert discover_autograph_collections(names, "p") == AG


def test_discover_without_entities() -> None:
    ag = discover_autograph_collections(["p_Chunks", "p_Documents", "p_Relations"], "p")
    assert ag.entities is None
    assert len(build_tools(ag)) == 3


def test_discover_missing_raises() -> None:
    with pytest.raises(ValueError, match="missing"):
        discover_autograph_collections(["p_Chunks"], "p")


def test_discover_ambiguous_raises() -> None:
    names = ["p_a_Chunks", "p_b_Chunks", "p_Documents", "p_Relations"]
    with pytest.raises(ValueError, match="Several"):
        discover_autograph_collections(names, "p")


# ---------------------------------------------------------------------------
# Read-only checks against the loaded database
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db():
    from backend.db import get_db
    return get_db()


@pytest.mark.parametrize("template", [Q_TOOL_TECHDOCS, Q_TOOL_KG_ENTITIES, Q_TOOL_FLEET_EXPAND],
                         ids=["techdocs", "kg_entities", "fleet_expand"])
def test_templates_parse(db, template) -> None:
    db.aql.validate(template)


def _critical_ids(db) -> list[str]:
    return list(db.aql.execute(
        "FOR e IN engines FILTER e.riskBucket == 'critical' RETURN e._id"))


def test_fleet_expand_returns_engine_context(db) -> None:
    ids = _critical_ids(db)
    assert ids
    rows = list(db.aql.execute(Q_TOOL_FLEET_EXPAND, bind_vars={
        "nodes": ids, "degraded_only": True, "max_parts": 5, "max_techs": 3, "max_wos": 2}))
    assert len(rows) == len(ids)
    ruls = [r["predictedRUL"] for r in rows]
    assert ruls == sorted(ruls)
    for r in rows:
        assert r["content"].startswith("Engine ")
        assert r["aircraft"]["tailNumber"]
        assert r["degradingSubsystems"]
    assert any(r["blockingParts"] for r in rows)


def test_fleet_expand_never_limits_away_blocking_parts(db) -> None:
    """With max_parts=1, every blocking part must still be reported and listed first."""
    ids = _critical_ids(db)
    rows = list(db.aql.execute(Q_TOOL_FLEET_EXPAND, bind_vars={
        "nodes": ids, "degraded_only": True, "max_parts": 1, "max_techs": 1, "max_wos": 1}))
    for r in rows:
        assert len(r["parts"]) <= 1
        if r["blockingParts"]:
            assert r["parts"][0]["blocking"] is True
    full = list(db.aql.execute(Q_TOOL_FLEET_EXPAND, bind_vars={
        "nodes": ids, "degraded_only": True, "max_parts": 50, "max_techs": 1, "max_wos": 1}))
    assert [r["blockingParts"] for r in rows] == [r["blockingParts"] for r in full]


def test_fleet_expand_degraded_only_drops_healthy(db) -> None:
    healthy = list(db.aql.execute(
        "FOR e IN engines FILTER e.riskBucket == 'healthy' LIMIT 3 RETURN e._id"))
    params = {"nodes": healthy, "max_parts": 1, "max_techs": 1, "max_wos": 1}
    assert list(db.aql.execute(Q_TOOL_FLEET_EXPAND,
                               bind_vars={**params, "degraded_only": True})) == []
    assert len(list(db.aql.execute(Q_TOOL_FLEET_EXPAND,
                                   bind_vars={**params, "degraded_only": False}))) == 3
