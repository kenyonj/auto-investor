FROM python:3.13-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code and default config
COPY src/ src/
COPY config.yaml .

# Dashboard port
ENV PORT=8000
EXPOSE 8000

# Data directory for SQLite database
VOLUME /app/data
ENV DB_PATH=/app/data/auto_investor.db

ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "auto_investor", "--schedule", "--execute"]
