FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.6
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd --create-home app && mkdir -p /app/data && chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH"
USER app
CMD ["sh", "-c", "trading-agent init-db && exec trading-agent serve --host 0.0.0.0"]
