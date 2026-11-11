"""Offline checks for the AutoGraph maintenance corpus (corpus/)."""

import json

import pytest

from pipeline.corpus import MANIFEST_PATH, _dump, build_manifest, load_corpus
from pipeline.synthetic_graph import ENGINE_MODELS, SUBSYSTEM_TYPES

# Engine models that appear only in deliberate "distractor" documents: realistic
# noise that retrieval must learn to ignore for this fleet.
NON_FLEET_MODELS = {"Trent 700", "GE90-115B", "CF6-80C2"}
REQUIRED_HEADERS = ("Document Type", "Effective Date", "Affected Engine Models",
                    "Affected Subsystems", "Related Documents")

DOCS = load_corpus()
BY_ID = {d.doc_id: d for d in DOCS}
INITIAL = [d for d in DOCS if d.stage == "initial"]
INCREMENTAL = [d for d in DOCS if d.stage == "incremental"]


def test_manifest_is_up_to_date() -> None:
    assert MANIFEST_PATH.exists(), "run: uv run python -m pipeline.corpus --write"
    assert MANIFEST_PATH.read_text(encoding="utf-8") == _dump(build_manifest()), (
        "corpus/MANIFEST.json is stale — run: uv run python -m pipeline.corpus --write"
    )


def test_manifest_build_is_deterministic() -> None:
    assert json.dumps(build_manifest()) == json.dumps(build_manifest())


def test_corpus_shape() -> None:
    assert len(INITIAL) >= 30
    assert len(INCREMENTAL) == 1
    assert len(BY_ID) == len(DOCS), "duplicate document ids"


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.doc_id)
def test_required_headers_present(doc) -> None:
    text = (MANIFEST_PATH.parent / doc.path).read_text(encoding="utf-8")
    for field in REQUIRED_HEADERS:
        assert f"**{field}:**" in text, f"{doc.doc_id} missing header {field!r}"
    assert doc.issuer, f"{doc.doc_id} has no Issuer / Recommended By / Publisher"


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.doc_id)
def test_models_and_subsystems_are_known(doc) -> None:
    assert doc.engine_models, f"{doc.doc_id} lists no engine models"
    unknown = set(doc.engine_models) - set(ENGINE_MODELS) - NON_FLEET_MODELS
    assert not unknown, f"{doc.doc_id} names unknown models {unknown}"
    assert set(doc.subsystems) <= set(SUBSYSTEM_TYPES), doc.subsystems


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.doc_id)
def test_document_length(doc) -> None:
    minimum = 750 if doc.fleet_linked else 450
    assert minimum <= doc.words <= 2500, f"{doc.doc_id}: {doc.words} words"


def test_cross_references_resolve() -> None:
    """Related Documents must exist in the corpus. (Supersedes may name an earlier
    revision that is deliberately not in the corpus, e.g. "Revision 15".)"""
    initial_ids = {d.doc_id for d in INITIAL}
    for d in INITIAL:
        missing = set(d.related) - initial_ids
        assert not missing, f"{d.doc_id} references {missing} outside corpus/initial"
    for d in INCREMENTAL:
        missing = set(d.related + d.supersedes) - set(BY_ID)
        assert not missing, f"{d.doc_id} references unknown {missing}"


def test_incremental_document_is_held_back() -> None:
    """Initial docs must not know about the incremental doc, or the demo leaks."""
    for inc in INCREMENTAL:
        for d in INITIAL:
            text = (MANIFEST_PATH.parent / d.path).read_text(encoding="utf-8")
            assert inc.doc_id not in text, f"{d.doc_id} mentions held-back {inc.doc_id}"
        assert inc.supersedes, "incremental AD should supersede an initial document"
        assert all(BY_ID[s].stage == "initial" for s in inc.supersedes)


def test_fleet_coverage() -> None:
    """Every fleet model and every subsystem is covered by fleet-linked documents."""
    fleet_docs = [d for d in DOCS if d.fleet_linked]
    for model in ENGINE_MODELS:
        assert sum(model in d.engine_models for d in fleet_docs) >= 3, model
    for sub in SUBSYSTEM_TYPES:
        assert sum(sub in d.subsystems for d in fleet_docs) >= 2, sub
