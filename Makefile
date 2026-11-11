.PHONY: check test lint load score embed reset dev tools corpus nb-strip

check:
	uv run python scripts/check_connection.py

test:
	uv run pytest
	cd frontend && npm run build

lint:
	uv run ruff check .

# Structured fleet graph (destructive: only runs against a DB whose name contains "aerofleet")
load:
	uv run python pipeline/loader.py

score:
	uv run python pipeline/scorer_runner.py

embed:
	uv run python pipeline/embed_runner.py

reset: load score embed

dev:
	uv run uvicorn backend.app:app --reload &
	cd frontend && npm run dev

# Register the Custom Retriever tools (needs AutoGraph ingestion to have run; see notebook §7)
tools:
	uv run python scripts/setup_retriever.py

# Regenerate corpus/MANIFEST.json after editing any corpus document
corpus:
	uv run python -m pipeline.corpus --write

# Strip outputs from the enablement notebook before committing
nb-strip:
	uv run python scripts/strip_notebook.py notebooks/aerofleet_autograph_autorag.ipynb
