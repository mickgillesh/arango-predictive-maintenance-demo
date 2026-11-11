"""Display helpers in notebooks/nb_helpers.py (no cluster needed).

The metadata fixtures follow the shapes the retriever returned on the platform
(2026-11-11), trimmed to the fields the summary reads.
"""

import json
from types import SimpleNamespace

import pytest

from notebooks.nb_helpers import metadata_of, show, summarize_metadata

P = "aerofleet_docs_11111216"

LOCAL_META = {
    "execution_log": [{
        "step_id": 1,
        "description": "Identify the purpose of AD-EASA-2024-0312",
        "query": "What is the purpose of AD-EASA-2024-0312?",
        "status": "completed",
        "result": "…",
        "context_data": [{"node_id": "n1", "chunks": [{"content": "x"}, {"content": "y"}]}],
    }],
    "completed_steps": 1, "total_steps": 1, "iterations": 1, "global_context": "g" * 500,
    "llm_planner_used": True, "original_query_type": "LOCAL",
    "citation_mapping": {"1": {"chunk_id": "c1", "citable_url": "",
                               "content": "# AD-EASA-2024-0312 — HPC Blade Erosion\nbody"}},
    "graph_metadata": {"documents": [f"{P}_Entities/a", f"{P}_Entities/b"]},
    "partition_routing": {"selected": ["p0"]},
    "model": "gpt-4o",
}
UNIFIED_META = {
    "status": "unknown", "total_results": 0, "search_methods_used": [],
    "collection_metadata": {}, "final_results_count": 0,
    "citation_mapping": {"1": {"content": "deionised water wash\n## Expected Results\n…"}},
    "graph_metadata": {}, "partition_routing": {}, "model": "gpt-4o",
}
GLOBAL_META = {
    "final_support_points": [
        {"analyst": 0, "answer": "EGT margin loss on CFM56-7B27", "score": 90}],
    "graph_metadata": {"documents": [f"{P}_Communities/0", f"{P}_Communities/11"]},
    "partition_routing": {}, "model": "gpt-4o",
}


def test_metadata_parsed_from_json_string() -> None:
    sdk_answer = SimpleNamespace(result="ok", metadata=json.dumps({"partitions": ["p1"]}))
    assert metadata_of(sdk_answer) == {"partitions": ["p1"]}
    rest_answer = {"result": "ok", "metadata": '{"tools": ["t"]}'}
    assert metadata_of(rest_answer) == {"tools": ["t"]}


def test_metadata_absent_or_plain_text() -> None:
    assert metadata_of({"result": "ok"}) is None
    assert metadata_of(SimpleNamespace(result="ok", metadata="")) is None
    assert metadata_of("FAILED [Timeout]") is None
    assert metadata_of({"metadata": "not json"}) == "not json"


def test_summary_local_planner() -> None:
    text = "\n".join(summarize_metadata(LOCAL_META))
    assert "LLM planner: True | original query type: LOCAL | steps 1/1" in text
    assert "asked: What is the purpose of AD-EASA-2024-0312?" in text
    assert "context: 1 graph nodes, 2 chunks" in text
    assert "[1] AD-EASA-2024-0312 — HPC Blade Erosion" in text
    assert "graph: documents: 2 Entities" in text
    assert "global_context (" in text  # unsummarised keys listed by size


def test_summary_unified_and_global() -> None:
    unified = "\n".join(summarize_metadata(UNIFIED_META))
    assert "search: methods - | results 0 -> 0" in unified
    assert "[1] Expected Results" in unified  # mid-document chunk: nearest heading
    glob = "\n".join(summarize_metadata(GLOBAL_META))
    assert "GLOBAL map-reduce: 1 scored support points" in glob
    assert " 90  EGT margin loss" in glob
    assert "graph: documents: 2 Communities" in glob


def test_summary_custom_tool_graph() -> None:
    """Shape returned by a custom tool on the platform (2026-11-11)."""
    meta = {"graph_metadata": {"documents": ["engines/48", "engines/51", "engines/9"],
                               "edges": [],
                               "by_query": [{"query": "", "step_id": None,
                                             "documents": ["engines/48"]}]},
            "partition_routing": {"mode": "tool_config_disabled"}}
    text = "\n".join(summarize_metadata(meta))
    assert "graph: documents: 3 engines | edges: 0 items | by_query: 1 items" in text
    assert "tool_config_disabled" in text


def test_summary_shows_tools_and_text_lists() -> None:
    """Deep Search metadata (2026-11-11): tool outcome keys, and by_query as long text."""
    meta = {"custom_retrievers_used": ["a", "b"], "num_tools": 2, "successful_tools": ["a"],
            "failed_tools": ["b"],
            "graph_metadata": {"documents": ["p_Chunks/1"], "by_query": ["**content:** x y"]}}
    text = "\n".join(summarize_metadata(meta))
    assert "tools: used ['a', 'b'] | succeeded ['a'] | failed ['b']" in text
    assert "documents: 1 Chunks | by_query: 1 items" in text
    assert "also:" not in text


def test_summary_tolerates_unknown_shapes() -> None:
    assert summarize_metadata("plain text") == ["plain text"]
    assert summarize_metadata({"tool_calls": [1, 2]}) == ["also: tool_calls (6 chars)"]


def test_show_prints_summary_not_raw(capsys: pytest.CaptureFixture[str]) -> None:
    show("Q", {"result": "answer", "metadata": json.dumps(LOCAL_META)})
    out = capsys.readouterr().out
    assert "answer" in out and "metadata (" in out and "LLM planner" in out
    assert '"execution_log"' not in out  # no raw JSON by default
    show("Q", {"result": "a", "metadata": json.dumps(LOCAL_META)}, raw_metadata_chars=50)
    assert '"execution_log"' in capsys.readouterr().out
    show("Q", {"result": "a", "metadata": json.dumps(LOCAL_META)}, metadata=False)
    assert "metadata" not in capsys.readouterr().out
