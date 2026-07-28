# Stage 1: build the React frontend
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --quiet
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime — serves frontend static files + FastAPI backend
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# Install Python deps (cached layer — only re-runs if lock file changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application source
COPY backend/  ./backend/
COPY pipeline/ ./pipeline/

# Copy built frontend so FastAPI can serve it at /
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Cloud Run injects PORT (default 8080); smoke test also uses 8080
ENV PORT=8080
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
