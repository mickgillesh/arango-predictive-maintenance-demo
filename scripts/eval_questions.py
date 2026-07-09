"""
Evaluate all curated suggestion questions against the live txt2aql service.

Usage:
  uv run python scripts/eval_questions.py

Prints question → generated AQL → NL answer for every suggestion chip.
Use this to iterate on question phrasing until all questions return
sensible, syntactically valid AQL.

Paste the output as evidence when claiming Phase 4 acceptance criteria.
"""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve .env.local relative to the project root regardless of cwd
_env_file = Path(__file__).resolve().parent.parent / ".env.local"
load_dotenv(_env_file, override=True)

# Show txt2aql auth/token warnings on stderr
logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

from backend.routes.chat import SUGGESTIONS  # noqa: E402
from backend.txt2aql import ask, health  # noqa: E402

SEP = "─" * 72


async def main() -> None:
    # Check service reachability first
    txt2aql_url = os.environ.get("TXT2AQL_URL", "")
    arango_url = os.environ.get("ARANGO_URL", "")
    arango_user = os.environ.get("ARANGO_USER", "")
    txt2aql_auth = os.environ.get("TXT2AQL_AUTH", "")

    print("Configuration:")
    print(f"  TXT2AQL_URL  : {txt2aql_url or '(not set)'}")
    print(f"  ARANGO_URL   : {arango_url or '(not set)'}")
    print(f"  ARANGO_USER  : {arango_user or '(not set)'}")
    print(f"  TXT2AQL_AUTH : {'(set — will use override)' if txt2aql_auth else '(not set — will auto-generate)'}")
    print()

    svc_health = await health()
    print(f"txt2aql service health: {svc_health}\n")
    if svc_health != "ok":
        if not txt2aql_url:
            print("TXT2AQL_URL is not set. Add it to .env.local:")
            print("  TXT2AQL_URL=https://<host>/graph-rag/<serviceIdPostfix>")
        elif not arango_url or not arango_user:
            print("ARANGO_URL / ARANGO_USER / ARANGO_PASSWORD are needed to auto-generate")
            print("the Bearer token. Check your .env.local.")
        else:
            print("Service returned non-OK status. Check the warning above for the JWT")
            print("error, or verify the service is deployed and TXT2AQL_URL is correct.")
        return

    print(f"Evaluating {len(SUGGESTIONS)} questions against live service…\n{SEP}")
    errors = 0

    for i, question in enumerate(SUGGESTIONS, 1):
        print(f"\n[{i}/{len(SUGGESTIONS)}] {question}")
        result = await ask(question)

        if result.get("error"):
            print(f"  ✗ ERROR ({result['error']}): {result['answer']}")
            errors += 1
        else:
            aql = (result.get("aql") or "").strip()
            if aql:
                # Indent multi-line AQL for readability
                aql_display = "\n    ".join(aql.splitlines())
                print(f"  AQL:\n    {aql_display}")
            else:
                print("  AQL: (none returned)")
            answer = (result.get("answer") or "").strip()
            print(f"  Answer: {answer}")
        print(SEP)

    print(f"\nSummary: {len(SUGGESTIONS) - errors}/{len(SUGGESTIONS)} questions succeeded.")
    if errors:
        print(f"  {errors} error(s) — refine those question phrasings and re-run.")


if __name__ == "__main__":
    asyncio.run(main())
