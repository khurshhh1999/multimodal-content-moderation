.PHONY: up down logs demo samples seed labeled-set test eval venv

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker dashboard prometheus grafana

samples:
	.venv/bin/python scripts/generate_samples.py

seed: samples
	./scripts/seed.sh

demo: samples
	./scripts/demo.sh

labeled-set: samples
	.venv/bin/python scripts/build_labeled_set.py

test:
	.venv/bin/pip install -q -r requirements-dev.txt
	PYTHONPATH=apps/worker:packages/moderation_shared/src .venv/bin/pytest -q

eval: labeled-set
	PYTHONPATH=apps/worker:packages/moderation_shared/src .venv/bin/python eval/harness.py
