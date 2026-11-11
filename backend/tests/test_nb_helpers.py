"""Display helpers in notebooks/nb_helpers.py (no cluster needed)."""

import json
from types import SimpleNamespace

import pytest

from notebooks.nb_helpers import metadata_of, show


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


def test_show_prints_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    show("Q", {"result": "answer", "metadata": json.dumps({"sources": ["AD-1"]})})
    out = capsys.readouterr().out
    assert "answer" in out and "metadata — keys: sources" in out and "AD-1" in out
    show("Q", {"result": "answer", "metadata": json.dumps({"x": 1})}, metadata=False)
    assert "metadata" not in capsys.readouterr().out


def test_show_truncates_long_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    show("Q", {"result": "a", "metadata": json.dumps({"big": "x" * 10_000})},
         max_metadata_chars=100)
    assert "metadata_of(answer) for all" in capsys.readouterr().out
