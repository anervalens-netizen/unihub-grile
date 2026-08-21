FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Install dependencies while building the image. Runtime containers only use
# the mounted source tree and never need network/package-manager access.
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY migrations ./migrations
