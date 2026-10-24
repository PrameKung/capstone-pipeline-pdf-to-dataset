.PHONY: help backend-sync backend-run pipeline-once docker-build docker-up docker-down docker-workers docker-logs

help:
	@printf '%s\n' \
	  'backend-sync  Install backend dependencies from uv.lock' \
	  'backend-run   Run the backend locally on 127.0.0.1:8000' \
	  'pipeline-once Process current PDFs once using the Docker worker' \
	  'docker-build  Build the backend image' \
	  'docker-up     Build and start backend and worker containers' \
	  'docker-down   Stop the Compose services' \
	  'docker-workers Scale Docker workers (WORKERS=3)' \
	  'docker-logs   Follow backend container logs'

backend-sync:
	cd backend && uv sync --locked

backend-run:
	cd backend && uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pipeline-once:
	docker compose run --rm worker python -m app.pipeline.jobs.worker --once

docker-build:
	docker compose build

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-workers:
	docker compose up --build -d --scale worker="$${WORKERS:-2}" backend worker

docker-logs:
	docker compose logs -f backend
