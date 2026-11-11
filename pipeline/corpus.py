"""
Maintenance document corpus for the AutoGraph enablement demo.

The corpus is a set of hand-authored Markdown files under ``corpus/``:

  corpus/initial/      ingested by AutoGraph in the first build
  corpus/incremental/  held back and added later to demonstrate an
                       incremental AutoGraph update

Every document starts with bold header fields (``**Field:** value``). This
module parses those headers, builds ``corpus/MANIFEST.json`` and checks that
the manifest still matches the files. The manifest is deterministic: it
depends only on file contents, so regenerating it without changing a file
produces an identical result.

CLI:
  uv run python -m pipeline.corpus --write   # regenerate MANIFEST.json
  uv run python -m pipeline.corpus --check   # exit 1 if MANIFEST.json is stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pipeline.synthetic_graph import ENGINE_MODELS, SUBSYSTEM_TYPES

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
MANIFEST_PATH = CORPUS_DIR / "MANIFEST.json"
STAGES = ("initial", "incremental")

# AutoGraph category (second scope label) the notebook uploads documents under.
CATEGORY = "maintenance_docs"

_HEADER_RE = re.compile(r"^\*\*(?P<field>[^*]+):\*\*\s*(?P<value>.*?)\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^#\s+(?P<doc_id>\S+)\s+—\s+(?P<title>.+)$", re.MULTILINE)


@dataclass(frozen=True)
class CorpusDoc:
    """One corpus document, as recorded in the manifest."""

    doc_id: str
    title: str
    stage: str
    path: str
    doc_type: str
    issuer: str
    effective_date: str
    engine_models: list[str]
    subsystems: list[str]
    supersedes: list[str]
    related: list[str]
    fleet_linked: bool
    words: int
    sha256: str


def _split_list(value: str) -> list[str]:
    if not value or value.strip().lower() == "none":
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_headers(text: str) -> dict[str, str]:
    """Return the bold ``**Field:** value`` header fields of a document."""
    return {m.group("field").strip(): m.group("value") for m in _HEADER_RE.finditer(text)}


def word_count(text: str) -> int:
    """Count whitespace-separated tokens containing a letter or digit.

    Ignores Markdown table syntax (``|``, ``---``) so tables don't inflate
    the count.
    """
    return sum(1 for tok in text.split() if re.search(r"[A-Za-z0-9]", tok))


def parse_doc(path: Path, stage: str) -> CorpusDoc:
    """Parse one Markdown document into a :class:`CorpusDoc`."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    title_match = _TITLE_RE.search(text)
    if not title_match:
        raise ValueError(f"{path}: first heading must be '# <DOC-ID> — <title>'")
    doc_id = title_match.group("doc_id")
    if doc_id != path.stem:
        raise ValueError(f"{path}: heading id {doc_id!r} does not match file name")

    h = parse_headers(text)
    models = _split_list(h.get("Affected Engine Models", ""))
    issuer = h.get("Issuer") or h.get("Recommended By") or h.get("Publisher") or ""
    return CorpusDoc(
        doc_id=doc_id,
        title=title_match.group("title").strip(),
        stage=stage,
        path=path.relative_to(CORPUS_DIR).as_posix(),
        doc_type=h.get("Document Type", ""),
        issuer=issuer,
        effective_date=h.get("Effective Date", ""),
        engine_models=models,
        subsystems=_split_list(h.get("Affected Subsystems", "")),
        supersedes=_split_list(h.get("Supersedes", "")),
        related=_split_list(h.get("Related Documents", "")),
        fleet_linked=any(m in ENGINE_MODELS for m in models),
        words=word_count(text),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_corpus(root: Path = CORPUS_DIR) -> list[CorpusDoc]:
    """Parse every document under ``root/<stage>/*.md``, sorted by stage then id."""
    docs: list[CorpusDoc] = []
    for stage in STAGES:
        for path in sorted((root / stage).glob("*.md")):
            docs.append(parse_doc(path, stage))
    return docs


def build_manifest(root: Path = CORPUS_DIR) -> dict:
    """Build the manifest dictionary for the corpus under ``root``."""
    docs = load_corpus(root)
    return {
        "category": CATEGORY,
        "fleet_models": ENGINE_MODELS,
        "subsystems": SUBSYSTEM_TYPES,
        "counts": {stage: sum(1 for d in docs if d.stage == stage) for stage in STAGES},
        "total_words": sum(d.words for d in docs),
        "documents": [asdict(d) for d in docs],
    }


def _dump(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="regenerate MANIFEST.json")
    group.add_argument("--check", action="store_true", help="fail if MANIFEST.json is stale")
    args = parser.parse_args(argv)

    expected = _dump(build_manifest())
    if args.write:
        MANIFEST_PATH.write_text(expected, encoding="utf-8")
        print(f"Wrote {MANIFEST_PATH.relative_to(CORPUS_DIR.parent)}")
        return 0
    current = MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else ""
    if current != expected:
        print("MANIFEST.json is stale — run: uv run python -m pipeline.corpus --write")
        return 1
    print("MANIFEST.json is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
