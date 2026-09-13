.PHONY: install test run compose-up compose-config

install:
	pip install -e ".[dev]"

test:
	pytest

run:
	ecochronos-vault

compose-up:
	docker compose up --build

compose-config:
	docker compose config --quiet
