.PHONY: sync lint format typecheck test eval eval-offline eval-safety eval-rag check run up down \
        frontend-install frontend-dev frontend-build docker-build clean

sync:
	uv sync --all-extras --dev

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src tests

test:
	uv run pytest

eval:
	uv run python -m tests.evals.runner

eval-offline:
	uv run python -m tests.evals.runner --offline

eval-safety:
	uv run python -m tests.evals.scenario_runner

eval-rag:
	uv run python -m tests.evals.rag_runner --tenant demo

check: lint typecheck test

run:
	uv run fastapi dev src/ai_agent_template/main.py

up:
	docker compose up --build

down:
	docker compose down

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

docker-build:
	docker build -t enterprise-it-helpdesk-agent:local .

clean:
	rm -rf .coverage .mypy_cache .pytest_cache .ruff_cache htmlcov dist \
	       src/*.egg-info **/__pycache__
