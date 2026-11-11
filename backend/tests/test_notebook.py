"""Static checks for the enablement notebook (no execution — it needs a live AI Suite)."""

import json
import re
from pathlib import Path

import pytest

from pipeline import retriever_tools

NB_PATH = Path(__file__).resolve().parents[2] / "notebooks" / "aerofleet_autograph_autorag.ipynb"
NB = json.loads(NB_PATH.read_text(encoding="utf-8"))
CODE = ["".join(c["source"]) for c in NB["cells"] if c["cell_type"] == "code"]
ALL_TEXT = "\n".join("".join(c["source"]) for c in NB["cells"])


def test_nbformat_4() -> None:
    assert NB["nbformat"] == 4
    assert NB["cells"]


def test_no_outputs_committed() -> None:
    for cell in NB["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [], "run `make nb-strip` before committing"
            assert cell["execution_count"] is None


@pytest.mark.parametrize("index", range(len(CODE)))
def test_code_cells_compile(index: int) -> None:
    # IPython line magics (%pip ...) are not Python; drop them before compiling.
    src = "\n".join(ln for ln in CODE[index].splitlines() if not ln.lstrip().startswith(("%", "!")))
    compile(src, f"cell-{index}", "exec")


def test_no_secret_literals() -> None:
    assert not re.search(r"sk-[A-Za-z0-9]{10,}", ALL_TEXT)
    assert not re.search(r"(?i)password\s*=\s*['\"][^'\"]+['\"]", ALL_TEXT)
    assert "getpass" in ALL_TEXT


def test_sdk_pinned_to_commit() -> None:
    assert re.search(r'SDK_REF = "[0-9a-f]{40}"', ALL_TEXT)


def test_tool_ids_exist() -> None:
    for name in ("TECHDOCS_TOOL_ID", "KG_TOOL_ID", "FLEET_ENGINE_TOOL_ID", "FLEET_STATUS_TOOL_ID"):
        assert hasattr(retriever_tools, name)
        assert getattr(retriever_tools, name) in ALL_TEXT or name in ALL_TEXT


def test_no_partition_id_tagging() -> None:
    """No cell writes a partition_id onto documents (the removed workaround)."""
    code = "\n".join(CODE)
    assert not re.search(r"""["']partition_id["']\s*:|\bpartition_id\s*=""", code)
