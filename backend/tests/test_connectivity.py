import os

import pytest

from backend.db import check_connection


def test_check_connection() -> None:
    info = check_connection()
    assert "version" in info
    assert info["database"] == os.environ["ARANGO_DB"]
    assert isinstance(info["collection_count"], int)
    assert info["collection_count"] >= 0
    print(
        f"\nArangoDB {info['version']} | db={info['database']} | "
        f"collections={info['collection_count']}"
    )
