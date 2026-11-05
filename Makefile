# WOYM: one-command entry points. Targets are filled in as components land.
.PHONY: help sync test up down setup status demo reset fresh images customer-up customer-down

help:
	@echo "make sync      install python deps (uv)"
	@echo "make test      run unit tests"
	@echo "make up        start Switch + Mattermost + gateway (infra/switch)"
	@echo "make down      stop the stack"
	@echo "make setup     users, keys, room, addressing (scripts/setup.sh)"
	@echo "make status    preflight: stack, agents, owners, rule, room, latest case"
	@echo "make demo      run the full case end to end (FIXER=claude|fixture, default claude)"
	@echo "make reset     back to the pre-demo state (scripts/reset.sh)"
	@echo "make fresh     clone HEAD to a temp dir and run the judge path (scripts/fresh-clone-test.sh)"
	@echo "make images    pre-pull node images and build the acme-laptop image"
	@echo "make customer-up / customer-down   start or remove the customer laptop container"

sync:
	uv sync

test:
	uv run pytest -q

up:
	./infra/switch/up.sh

down:
	./infra/switch/down.sh

setup:
	./scripts/setup.sh

FIXER ?= claude

demo:
	uv run python scripts/demo.py --fixer $(FIXER)

images:
	./scripts/pull-images.sh

NAME ?= acme-laptop

customer-up:
	uv run python -m woym.sandbox.customer --name $(NAME) up

customer-down:
	uv run python -m woym.sandbox.customer --name $(NAME) down

reset:
	./scripts/reset.sh

fresh:
	./scripts/fresh-clone-test.sh

status:
	uv run python scripts/status.py
