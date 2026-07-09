"""
Print a fresh ArangoDB JWT and the resulting TXT2AQL env var values.

The running application auto-refreshes its own token from ARANGO_USER /
ARANGO_PASSWORD, so you do NOT need to run this script to keep the demo alive.
Use it for:
  - Confirming credentials work before a demo session
  - Pinning a specific token via TXT2AQL_AUTH (uncommon)
  - CI environments that need a one-shot token

Usage:
  uv run python scripts/get_token.py

Reads ARANGO_URL / ARANGO_USER / ARANGO_PASSWORD from .env.local.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

_env_file = Path(__file__).resolve().parent.parent / ".env.local"
load_dotenv(_env_file, override=True)

url = os.environ["ARANGO_URL"].rstrip("/")
user = os.environ["ARANGO_USER"]
password = os.environ["ARANGO_PASSWORD"]

resp = requests.post(
    f"{url}/_open/auth",
    json={"username": user, "password": password},
    timeout=10,
)
resp.raise_for_status()
token = resp.json()["jwt"]

print("Token generated successfully (valid ~1 hour).\n")
print("To override the app's auto-refresh, add to .env.local:\n")
print(f"TXT2AQL_AUTH=Bearer {token}")
print()
print("# TXT2AQL_URL format (replace <serviceIdPostfix> with the trailing")
print("# segment of your serviceId from the platform deploy response):")
print(f"TXT2AQL_URL={url}/graph-rag/<serviceIdPostfix>")
