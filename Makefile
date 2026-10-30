PYTHON ?= .venv/bin/python
PYTEST ?= $(PYTHON) -m pytest

# Keep inherited PYTHONPATH (e.g. ROS) from leaking incompatible packages in.
export PYTHONPATH :=

.PHONY: install run test test-integration cov lint format up down migrate loadtest

install:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

run: migrate
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PYTEST)

test-integration:
	docker compose up -d --wait db redis
	TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+psycopg://postgres:postgres@localhost:5432/urlshortener_test} \
	TEST_REDIS_URL=$${TEST_REDIS_URL:-redis://localhost:6379/15} \
	$(PYTEST) -m integration -o addopts=""

cov:
	$(PYTEST) --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=85

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

up:
	docker compose up --build

down:
	docker compose down

migrate:
	$(PYTHON) -m alembic upgrade head

loadtest:
	$(PYTHON) -m locust -f load/locustfile.py --headless -u 100 -r 10 -t 60s \
		--host http://localhost:8000 --csv load/results
