.PHONY: help backend-sync backend-run docker-build docker-up docker-down docker-logs

help:
	@printf '%s\n' \
	  'backend-sync  Install backend dependencies from uv.lock' \
	  'backend-run   Run the backend locally on 127.0.0.1:8000' \
	  'docker-build  Build the backend image' \
	  'docker-up     Build and start the backend container' \
	  'docker-down   Stop the Compose services' \
	  'docker-logs   Follow backend container logs'

backend-sync:
	cd backend && uv sync --locked

backend-run:
	cd backend && uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

docker-build:
	docker compose build

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f backend
