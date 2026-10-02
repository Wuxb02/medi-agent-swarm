FROM node:22.14.0-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv
FROM python:3.12.12-slim AS backend
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_CACHE_DIR=/tmp/uv-cache
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
ARG EMBEDDING_REVISION=7999e1d3359715c523056ef9478215996d62a620
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5', revision='${EMBEDDING_REVISION}', local_dir='/opt/models/embedding', allow_patterns=['*.json', '*.txt', '*.safetensors', 'pytorch_model.bin', '1_Pooling/*'])"
COPY mediZJ/ ./mediZJ/
COPY .claude/skills/ ./.claude/skills/
RUN uv sync --frozen --no-dev --no-editable --offline && rm -rf /tmp/uv-cache
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN useradd --uid 10001 --create-home app && mkdir -p /data/uploads && chown -R app:app /data /app
USER app
ENV EMBEDDING_MODEL_NAME=/opt/models/embedding HF_HUB_OFFLINE=1
EXPOSE 8000
CMD ["uvicorn", "mediZJ.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "128"]
