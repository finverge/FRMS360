# Single image shared by every microservice. Each compose service overrides `command`
# to launch its own uvicorn app. Keeps the scaffold trivially runnable.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps for psycopg2-binary are already bundled; keep image lean.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install the shared library as an editable package so `import cp_common` works everywhere.
COPY packages ./packages
RUN pip install --no-cache-dir -e packages/cp_common

# Application code.
COPY services ./services
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

# Default command is overridden per-service in docker-compose.yml.
CMD ["uvicorn", "services.gateway.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
