.PHONY: install test run ingest ingest-status batch migrate compose-up compose-config

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

batch:
	ecochronos-vault-batch

migrate:
	ecochronos-vault-migrate

compose-up:
	docker compose up --build

compose-config:
	docker compose config --quiet
