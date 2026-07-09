"""Print ArangoDB version, database name, and collection count. Used by `make check`."""
from dotenv import load_dotenv

load_dotenv(".env.local", override=True)

from backend.db import check_connection  # noqa: E402


def main() -> None:
    info = check_connection()
    print(f"ArangoDB version : {info['version']}")
    print(f"Database         : {info['database']}")
    print(f"Collections      : {info['collection_count']}")


if __name__ == "__main__":
    main()
