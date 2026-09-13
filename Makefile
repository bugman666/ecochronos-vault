.PHONY: install test run ingest ingest-status compose-up compose-config

install:
	pip install -e ".[dev]"

test:
	pytest

run:
	ecochronos-vault serve

ingest:
	ecochronos-vault ingest

ingest-status:
	ecochronos-vault ingest-status

compose-up:
	docker compose up --build

compose-config:
	docker compose config --quiet
