.PHONY: up down migrate seed smoke test lint clean logs

PROFILE ?=

define COMPOSE_CMD
docker compose -f docker-compose.yml
endef

ifneq ($(PROFILE),)
	COMPOSE_CMD := $(COMPOSE_CMD) -f docker-compose.$(PROFILE).yml
endif

up:
	$(COMPOSE_CMD) up -d --build
	$(MAKE) migrate

down:
	$(COMPOSE_CMD) down

migrate:
	$(COMPOSE_CMD) exec api python -c "from cante.db import run_migrations_async; import asyncio; asyncio.run(run_migrations_async())"

seed:
	$(COMPOSE_CMD) exec api python -m seeds

smoke:
	$(COMPOSE_CMD) exec api python -m tests.smoke

test:
	pytest -v --cov=cante --cov=services --cov-report=term-missing

lint:
	ruff check core/ services/ && mypy core/ services/

clean:
	$(COMPOSE_CMD) down -v
	rm -rf core/__pycache__ core/cante/__pycache__

logs:
	$(COMPOSE_CMD) logs -f
