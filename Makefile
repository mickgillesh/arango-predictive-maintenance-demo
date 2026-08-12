.PHONY: check test load score dev image smoke reset deploy

GCP_PROJECT ?= $(shell gcloud config get-value project 2>/dev/null)
GCP_REGION  ?= europe-west2
SERVICE     := aerofleet-demo

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

# Deploy to GCP Cloud Run (scales to zero — near-zero cost for demos)
# Prerequisites: gcloud CLI authenticated, project set, Cloud Run API enabled
# Sensitive vars: move to Secret Manager for shared deployments
deploy:
	gcloud run deploy $(SERVICE) \
	  --source . \
	  --region $(GCP_REGION) \
	  --project $(GCP_PROJECT) \
	  --allow-unauthenticated \
	  --memory 1Gi \
	  --set-env-vars "ARANGO_URL=$(ARANGO_URL),ARANGO_DB=$(ARANGO_DB),ARANGO_USER=$(ARANGO_USER),ARANGO_PASSWORD=$(ARANGO_PASSWORD),OPENAI_API_KEY=$(OPENAI_API_KEY),GOOGLE_CLIENT_ID=$(GOOGLE_CLIENT_ID),GOOGLE_CLIENT_SECRET=$(GOOGLE_CLIENT_SECRET),GOOGLE_REDIRECT_URI=$(GOOGLE_REDIRECT_URI),SESSION_SECRET=$(SESSION_SECRET)"
