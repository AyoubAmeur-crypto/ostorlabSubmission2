# Tidewright Orchestrator developer commands. Everything runs in Docker; the
# only host requirement is Docker with the compose plugin.
#
# Set COMPOSE_PROJECT_NAME to run an isolated copy of the stack, e.g.
#   COMPOSE_PROJECT_NAME=tw-review make test

COMPOSE ?= docker compose
RUN     := $(COMPOSE) run --rm
PYTEST_ARGS ?=
SERVICE ?=
SEED ?= 42
SEED_ARGS ?=
BENCH_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help build setup up down clean migrate seed test lint logs shell psql bench bench-suppression

help:
	@echo "Targets:"
	@echo "  setup    build images, start db/redis, migrate, seed, start everything"
	@echo "  up       start all services (api on http://127.0.0.1:8080)"
	@echo "  down     stop all services"
	@echo "  clean    stop all services and delete volumes (dev data is lost)"
	@echo "  migrate  apply alembic migrations to the dev database"
	@echo "  seed     load deterministic demo data (SEED=$(SEED), SEED_ARGS=\"--reset ...\")"
	@echo "  test     run pytest against tidewright_test (PYTEST_ARGS=...)"
	@echo "  lint     ruff check"
	@echo "  logs     follow logs (SERVICE=worker to filter)"
	@echo "  shell    bash in an app container"
	@echo "  psql     psql on the dev database"
	@echo "  bench    time the findings list and export on seeded data"
	@echo "  bench-suppression  compare original PR and fixed matching (BENCH_ARGS=...)"

build:
	$(COMPOSE) build

setup: build
	$(COMPOSE) up -d --wait db redis
	$(MAKE) migrate
	$(MAKE) seed
	$(COMPOSE) up -d

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) --profile tests down -v --remove-orphans

migrate:
	$(RUN) api alembic upgrade head

seed:
	$(RUN) api python -m seed.generate --seed $(SEED) $(SEED_ARGS)

test:
	$(COMPOSE) up -d --wait db redis
	$(COMPOSE) --profile tests run --rm tests pytest $(PYTEST_ARGS)

lint:
	$(RUN) --no-deps tests ruff check --no-cache app tests migrations seed mockhook scripts

logs:
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

shell:
	$(RUN) api bash

psql:
	$(COMPOSE) exec db psql -U tidewright -d tidewright

bench:
	$(RUN) api python scripts/bench_findings.py

bench-suppression:
	$(RUN) api python scripts/bench_suppression.py $(BENCH_ARGS)
