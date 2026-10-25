FROM node:22-bookworm-slim AS codex-cli

RUN npm install --global @openai/codex@0.163.0 \
    && mkdir -p /usr/local/vendor \
    && cp -a /usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/. /usr/local/vendor/

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-cli /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=codex-cli /usr/local/vendor /usr/local/vendor

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
