"""Download the NASA C-MAPSS FD001 training file into data/."""
import io
import pathlib
import zipfile

import requests

DATA_DIR = pathlib.Path("data")
FD001_PATH = DATA_DIR / "train_FD001.txt"

# NASA Open Data Portal — CMAPSS Jet Engine Simulated Data (ff5v-kuh6)
NASA_URL = "https://data.nasa.gov/download/ff5v-kuh6/application%2Fzip"


def ensure_fd001() -> pathlib.Path:
    """Return path to train_FD001.txt, downloading from NASA if not present."""
    if FD001_PATH.exists():
        print(f"  data already present: {FD001_PATH}")
        return FD001_PATH

    DATA_DIR.mkdir(exist_ok=True)
    print(f"  downloading C-MAPSS FD001 from {NASA_URL} …")
    try:
        resp = requests.get(NASA_URL, timeout=120, stream=False)
        resp.raise_for_status()
    except Exception as exc:
        _manual_instructions(str(exc))
        raise SystemExit(1)

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            candidates = [n for n in zf.namelist() if n.endswith("train_FD001.txt")]
            if not candidates:
                _manual_instructions(
                    f"train_FD001.txt not found inside zip. Contents: {zf.namelist()}"
                )
                raise SystemExit(1)
            with zf.open(candidates[0]) as src:
                FD001_PATH.write_bytes(src.read())
    except zipfile.BadZipFile as exc:
        _manual_instructions(str(exc))
        raise SystemExit(1)

    print(f"  saved to {FD001_PATH}")
    return FD001_PATH


def _manual_instructions(reason: str) -> None:
    print(f"\nERROR: Could not download C-MAPSS FD001: {reason}")
    print("Please download the dataset manually and place train_FD001.txt in data/")
    print("Sources:")
    print("  NASA:   https://data.nasa.gov/Aerospace/CMAPSS-Jet-Engine-Simulated-Data/ff5v-kuh6")
    print("  Kaggle: https://www.kaggle.com/datasets/behrad3d/nasa-cmaps")
