.PHONY: check test load score dev image smoke reset

check:
	uv run python scripts/check_connection.py

test:
	uv run pytest
	cd frontend && npm run build

load:
	uv run python pipeline/loader.py

score:
	uv run python pipeline/scorer_runner.py

dev:
	uv run uvicorn backend.app:app --reload &
	cd frontend && npm run dev

reset: load score

image:
	docker build -t aerofleet-demo .

smoke:
	docker run --rm --env-file .env.local -p 8080:8080 aerofleet-demo &
	sleep 8
	curl -f http://localhost:8080/api/health
	docker stop $$(docker ps -q --filter ancestor=aerofleet-demo)
