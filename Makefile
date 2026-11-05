# WOYM: one-command entry points. Targets are filled in as components land.
.PHONY: help sync test up down setup customer-up demo

help:
	@echo "make sync      install python deps (uv)"
	@echo "make test      run unit tests"
	@echo "make up        start Switch + Mattermost + gateway (infra/switch)"
	@echo "make down      stop the stack"
	@echo "make setup     users, keys, room, addressing (scripts/setup.sh)"
	@echo "make demo      run the full case end to end (scripts/demo.py)"

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

demo:
	uv run python scripts/demo.py
