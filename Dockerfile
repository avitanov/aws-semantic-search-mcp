# syntax=docker/dockerfile:1
#
# AgentCore Runtime requires linux/arm64. Build with:
#   docker buildx build --platform linux/arm64 -t article-search-mcp .

# ---------- builder: resolve dependencies into a self-contained venv ----------
FROM ghcr.io/astral-sh/uv:0.9.7-python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the lockfiles are mounted, so this layer is cached until dependencies change --
# editing the server code does not trigger a reinstall. --locked fails the build if the
# lockfile is out of date, so the image can never drift from the pinned versions.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

# ---------- runtime: interpreter, venv and server only, no build tooling ----------
FROM python:3.13-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Unprivileged user: nothing in this container needs root.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv ./.venv
# Same layout as the repo, so the manifest lookup in common.py resolves identically here
# and on a laptop. ingest.py is left out -- the server never runs it.
COPY --chown=app:app src/common.py src/mcp_server.py src/utils.py ./src/
COPY --chown=app:app data/articles.json ./data/

USER app
EXPOSE 8000

# Configuration and credentials come from the environment and the AgentCore execution
# role at run time -- nothing sensitive is baked into the image.
CMD ["python", "src/mcp_server.py"]
