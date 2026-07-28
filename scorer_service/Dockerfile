FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Install Python deps (cached layer — only re-runs if pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source packages needed by the scorer
COPY backend/  ./backend/
COPY pipeline/ ./pipeline/
COPY data/     ./data/
COPY scorer_service/ ./scorer_service/

EXPOSE 8001
CMD ["uv", "run", "uvicorn", "scorer_service.app:app", "--host", "0.0.0.0", "--port", "8001"]
