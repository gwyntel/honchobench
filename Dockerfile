# HonchoBench — container image.
#
# The image is deliberately self-contained: it pip-installs the pinned runtime
# deps, copies the package source, and the ENTRYPOINT runs the runner CLI.
# Model keys / base URLs are NEVER baked in — they are read from the environment
# at container runtime (sourced from .env via docker compose env_file).
#
# Build:    docker build -t honchobench .
# Run CLI:  docker run --rm --env-file .env -v "$PWD/data":/app/data \
#             -v "$PWD/reports":/app/reports honchobench [args]

FROM python:3.11-slim

# Non-interactive; mirrors host layout so bind-mounts stay predictable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKDIR=/app

WORKDIR /app

# Install Python deps first (best layer reuse) and copy package source after.
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Install the package itself (provides the `honchobench` console entry point and
# makes `python -m honchobench` importable).
RUN pip install --no-cache-dir .

# Data + reports live on mount points; create them so bind-mounts overlay cleanly.
RUN mkdir -p /app/data /app/reports

ENTRYPOINT ["honchobench"]
CMD ["--help"]
