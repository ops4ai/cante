.PHONY: up down seed smoke test lint clean

PROFILE ?=

define COMPOSE_CMD
docker compose -f docker-compose.yml
endef

ifneq ($(PROFILE),)
	COMPOSE_CMD := $(COMPOSE_CMD) -f docker-compose.$(PROFILE).yml
endif

up:
	$(COMPOSE_CMD) up -d --build

down:
	$(COMPOSE_CMD) down

seed:
	$(COMPOSE_CMD) exec api python -m seeds

smoke:
	$(COMPOSE_CMD) exec api python -m tests.smoke

test:
	cd core && pytest -v --cov=cante --cov-report=term-missing

lint:
	ruff check core/ services/ && mypy core/

clean:
	$(COMPOSE_CMD) down -v
	rm -rf core/__pycache__ core/cante/__pycache__

logs:
	$(COMPOSE_CMD) logs -f
