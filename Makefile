.PHONY: up down logs demo samples seed labeled-set redrive test lint eval eval-smoke dashboard-build ci venv

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker dashboard prometheus grafana jaeger

samples:
	.venv/bin/python scripts/generate_samples.py

seed: samples
	./scripts/seed.sh

demo: samples
	./scripts/demo.sh

redrive:
	./scripts/redrive.sh $(ARGS)

labeled-set: samples
	.venv/bin/python scripts/build_labeled_set.py

lint:
	.venv/bin/pip install -q -r requirements-dev.txt
	.venv/bin/ruff check apps packages tests scripts eval

test:
	.venv/bin/pip install -q -r requirements-dev.txt
	.venv/bin/pytest -q

eval: labeled-set
	PYTHONPATH=apps/worker:packages/moderation_shared/src .venv/bin/python eval/harness.py

eval-smoke: labeled-set
	PYTHONPATH=apps/worker:packages/moderation_shared/src .venv/bin/python eval/harness.py --min-n 50

dashboard-build:
	cd apps/dashboard && npm ci && npm run build

ci: lint test eval-smoke dashboard-build
