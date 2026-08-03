.PHONY: up down logs demo samples test eval venv

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker dashboard

samples:
	.venv/bin/python scripts/generate_samples.py

demo: samples
	./scripts/demo.sh

test:
	.venv/bin/pip install -q -r requirements-dev.txt
	PYTHONPATH=packages/moderation_shared/src .venv/bin/pytest -q

eval: samples
	PYTHONPATH=apps/worker:packages/moderation_shared/src .venv/bin/python eval/harness.py
