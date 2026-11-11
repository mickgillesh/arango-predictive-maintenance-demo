"""Strip outputs and execution counts from Jupyter notebooks in place.

Usage: uv run python scripts/strip_notebook.py notebooks/*.ipynb
"""

import json
import sys
from pathlib import Path


def strip(path: Path) -> bool:
    """Remove outputs from ``path``. Returns True if the file changed."""
    original = path.read_text(encoding="utf-8")
    nb = json.loads(original)
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        cell.get("metadata", {}).pop("execution", None)
    updated = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(f"{'stripped' if strip(Path(arg)) else 'clean   '}  {arg}")
