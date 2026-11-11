"""The committed C-MAPSS FD001 file is the genuine dataset and never changes."""

import hashlib
from pathlib import Path

FD001 = Path(__file__).resolve().parents[2] / "data" / "train_FD001.txt"
FD001_SHA256 = "963b5e22825b34d8b21c69e1aeb4af3e647050eb672ee8834ba4b5d91d2de0f8"


def test_fd001_is_committed_and_unchanged() -> None:
    assert FD001.is_file(), "data/train_FD001.txt missing (it is committed to the repo)"
    assert hashlib.sha256(FD001.read_bytes()).hexdigest() == FD001_SHA256


def test_fd001_shape() -> None:
    rows = [line.split() for line in FD001.read_text().splitlines() if line.strip()]
    assert len(rows) == 20_631  # one `readings` document per row
    assert {len(r) for r in rows} == {26}  # unit, cycle, 3 settings, 21 sensors
    assert len({r[0] for r in rows}) == 100  # 100 engines
